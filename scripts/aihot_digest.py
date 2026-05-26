#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BASE_URL = "https://aihot.virxact.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CATEGORY_LABELS = OrderedDict(
    [
        ("ai-models", "模型发布/更新"),
        ("ai-products", "产品发布/更新"),
        ("industry", "行业动态"),
        ("paper", "论文研究"),
        ("tip", "技巧与观点"),
        (None, "其他"),
    ]
)


def fetch_json(path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    if query:
        clean_query = {k: v for k, v in query.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean_query)}"

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI HOT request failed: {exc.code} {body}") from exc


def human_time(value: str | None, tz: ZoneInfo, now: datetime) -> str:
    if not value:
        return ""

    published = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)
    delta = now - published

    if timedelta(seconds=0) <= delta < timedelta(minutes=1):
        return "刚刚"
    if timedelta(minutes=1) <= delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} 分钟前"
    if timedelta(hours=1) <= delta < timedelta(hours=24):
        return f"{int(delta.total_seconds() // 3600)} 小时前"

    today = now.date()
    if published.date() == today:
        return f"今天 {published:%H:%M}"
    if published.date() == today - timedelta(days=1):
        return f"昨天 {published:%H:%M}"

    return f"{published.month}/{published.day} {published:%H:%M}"


def trim_summary(value: str | None, limit: int = 120) -> str:
    if not value:
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def format_item(
    index: int,
    title: str,
    source: str,
    summary: str | None,
    url: str | None,
    time_label: str = "",
) -> str:
    lines = [f"{index}. **{title}** — {source}"]
    if time_label:
        lines.append(f"   {time_label}")
    if summary:
        lines.append(f"   {trim_summary(summary)}")
    if url:
        lines.append(f"   {url}")
    return "\n".join(lines)


def selected_digest(hours: int, take: int, tz: ZoneInfo) -> str:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    since = (now_utc - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    data = fetch_json(
        "/api/public/items",
        {
            "mode": "selected",
            "since": since,
            "take": min(max(take, 1), 100),
        },
    )
    items = data.get("items", [])

    start_local = (now_utc - timedelta(hours=hours)).astimezone(tz)
    lines = [
        f"**AI HOT · 最近 {hours} 小时精选**",
        "",
        f"时间窗：{start_local:%Y-%m-%d %H:%M} ~ {now_local:%Y-%m-%d %H:%M}（北京时间）",
        f"共 {len(items)} 条，按发布时间倒序。",
        "",
    ]

    if not items:
        lines.append("这段时间暂时没有新的精选条目。")
        return "\n".join(lines).strip() + "\n"

    grouped: OrderedDict[str | None, list[dict[str, Any]]] = OrderedDict((key, []) for key in CATEGORY_LABELS)
    for item in items:
        grouped.setdefault(item.get("category"), []).append(item)

    index = 1
    for category, category_items in grouped.items():
        if not category_items:
            continue

        lines.append(f"## {CATEGORY_LABELS.get(category, '其他')}")
        for item in category_items:
            lines.append(
                format_item(
                    index,
                    item.get("title") or item.get("title_en") or "未命名条目",
                    item.get("source") or "未知来源",
                    item.get("summary"),
                    item.get("url"),
                    human_time(item.get("publishedAt"), tz, now_local),
                )
            )
            lines.append("")
            index += 1

    return "\n".join(lines).strip() + "\n"


def daily_digest(tz: ZoneInfo) -> str:
    data = fetch_json("/api/public/daily")
    date = data.get("date") or datetime.now(tz).strftime("%Y-%m-%d")

    lines = [f"**AI HOT 日报 · {date}**", ""]
    lead = data.get("lead")
    if lead:
        if lead.get("title"):
            lines.extend([f"## {lead['title']}", ""])
        if lead.get("leadParagraph"):
            lines.extend([textwrap.fill(lead["leadParagraph"], width=100), ""])

    index = 1
    for section in data.get("sections", []):
        section_items = section.get("items", [])
        if not section_items:
            continue
        lines.append(f"## {section.get('label') or '未分类'}")
        for item in section_items:
            lines.append(
                format_item(
                    index,
                    item.get("title") or "未命名条目",
                    item.get("sourceName") or "未知来源",
                    item.get("summary"),
                    item.get("sourceUrl"),
                )
            )
            lines.append("")
            index += 1

    flashes = data.get("flashes") or []
    if flashes:
        now_local = datetime.now(timezone.utc).astimezone(tz)
        lines.append("## 快讯")
        for flash in flashes:
            time_label = human_time(flash.get("publishedAt"), tz, now_local)
            source = flash.get("sourceName") or "未知来源"
            url = flash.get("sourceUrl")
            suffix = f"（{time_label}）" if time_label else ""
            if url:
                lines.append(f"- **{flash.get('title') or '未命名快讯'}** — {source}{suffix}\n  {url}")
            else:
                lines.append(f"- **{flash.get('title') or '未命名快讯'}** — {source}{suffix}")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an AI HOT Markdown digest.")
    parser.add_argument("--mode", choices=["selected24h", "daily"], default="selected24h")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--take", type=int, default=50)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--output", default="digest.md")
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)
    if args.mode == "daily":
        digest = daily_digest(tz)
    else:
        digest = selected_digest(args.hours, args.take, tz)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(digest, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

