#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BASE_URL = "https://aihot.news"
USER_AGENT = "aihot-daily-digest/2.0"
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60

CATEGORY_LABELS = OrderedDict(
    [
        ("ai-models", "模型发布/更新"),
        ("ai-products", "产品发布/更新"),
        ("industry", "行业动态"),
        ("paper", "论文研究"),
        ("tip", "技巧与观点"),
        ("opinion", "观点"),
        (None, "其他"),
    ]
)


class APIError(RuntimeError):
    def __init__(self, status: int, problem: dict[str, Any], retry_after: str | None = None):
        self.status = status
        self.code = problem.get("code")
        detail = problem.get("detail") or problem.get("title") or "Unknown API error"
        context = [f"code={self.code}"] if self.code else []
        if problem.get("requestId"):
            context.append(f"requestId={problem['requestId']}")
        if retry_after:
            context.append(f"Retry-After={retry_after}")
        suffix = f" ({', '.join(context)})" if context else ""
        super().__init__(f"AI HOT request failed: HTTP {status}: {detail}{suffix}")


def retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return max(0.0, float(int(value)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2 ** attempt)


def fetch_json(path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    if query:
        clean_query = {k: v for k, v in query.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean_query)}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"},
    )
    edge_retried = False
    for attempt in range(MAX_ATTEMPTS):
        delay = float(2 ** attempt)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                encoding = response.headers.get("Content-Encoding", "").lower()
            try:
                if encoding == "gzip":
                    body = gzip.decompress(body)
                data = json.loads(body.decode("utf-8"))
            except (ValueError, OSError, EOFError, zlib.error) as exc:
                raise RuntimeError("AI HOT returned invalid JSON or gzip data") from exc
            if not isinstance(data, dict):
                raise RuntimeError("AI HOT returned a JSON value that is not an object")
            return data
        except urllib.error.HTTPError as exc:
            body = exc.read()
            exc.close()
            try:
                if exc.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                problem = json.loads(body.decode("utf-8"))
                if not isinstance(problem, dict):
                    problem = {}
            except (ValueError, OSError, EOFError, zlib.error):
                problem = {"detail": body.decode("utf-8", errors="replace")[:500]}
            retry_after = exc.headers.get("Retry-After")
            if retry_after is None and isinstance(problem.get("retryAfter"), int):
                retry_after = str(problem["retryAfter"])
            error = APIError(exc.code, problem, retry_after)
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if exc.code in {566, 567}:
                retryable = not edge_retried
                edge_retried = True
            delay = retry_delay(retry_after, attempt)
            if not retryable or attempt == MAX_ATTEMPTS - 1 or delay > MAX_RETRY_DELAY:
                raise error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = RuntimeError(f"AI HOT connection failed: {exc}")
            if attempt == MAX_ATTEMPTS - 1:
                raise error from exc
        print(f"{error}; retrying in {delay:g}s", file=sys.stderr)
        time.sleep(delay)
    raise AssertionError("unreachable")


def published_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published.tzinfo is None:
            return None
        return published.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def object_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError(f"AI HOT response field '{field}' must be an array of objects")
    return value


def text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def source_name(item: dict[str, Any]) -> str:
    source = item.get("source")
    name = text_value(source.get("name")) if isinstance(source, dict) else ""
    return name or "未知来源"


def item_url(item: dict[str, Any]) -> str:
    links = item.get("links")
    if not isinstance(links, dict):
        return ""
    return text_value(links.get("original")) or text_value(links.get("aihot"))


def human_time(value: str | None, tz: ZoneInfo, now: datetime) -> str:
    published_utc = published_time(value)
    if published_utc is None:
        return ""

    published = published_utc.astimezone(tz)
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
    if not 1 <= hours <= 168:
        raise ValueError("--hours must be between 1 and 168 (the API retains 7 days)")
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    since = now_utc - timedelta(hours=hours)
    limit = min(max(take, 1), 100)
    query: dict[str, Any] = {
        "mode": "selected",
        "window": "24h" if hours <= 24 else "7d",
        "by": "published",
        "limit": limit,
    }
    selected: list[tuple[datetime, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    restarted = False
    missing_dates = 0
    while True:
        try:
            data = fetch_json("/api/v1/items", query)
        except APIError as exc:
            if exc.status != 400 or exc.code != "invalid_cursor" or restarted or "cursor" not in query:
                raise
            # A rolling-window cursor may become invalid while paging. Restart once;
            # never publish a partially fetched digest or reuse a cursor across runs.
            query.pop("cursor")
            selected.clear()
            seen_ids.clear()
            seen_cursors.clear()
            missing_dates = 0
            restarted = True
            continue
        page_items = object_list(data.get("items"), "items")
        page = data.get("page")
        if not isinstance(page, dict) or not isinstance(page.get("hasMore"), bool):
            raise RuntimeError("AI HOT response is missing valid page.hasMore metadata")
        reached_start = False
        for item in page_items:
            published = published_time(item.get("publishedAt"))
            if published is None:
                missing_dates += 1
                continue
            if published < since:
                reached_start = True
                continue
            if published > now_utc:
                continue
            item_id = text_value(item.get("id")) or item_url(item)
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            selected.append((published, item))
        if len(selected) >= limit or reached_start or not page["hasMore"]:
            break
        cursor = page.get("nextCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise RuntimeError("AI HOT returned a missing or repeated page.nextCursor")
        seen_cursors.add(cursor)
        query["cursor"] = cursor
    if missing_dates:
        print(f"Skipped {missing_dates} items without a valid publishedAt; receipt time is not publication time.", file=sys.stderr)
    selected.sort(key=lambda entry: entry[0], reverse=True)
    items = [item for _, item in selected[:limit]]

    start_local = since.astimezone(tz)
    timezone_label = "北京时间" if tz.key == "Asia/Shanghai" else tz.key
    lines = [
        f"**AI HOT · 最近 {hours} 小时精选**",
        "",
        f"时间窗：{start_local:%Y-%m-%d %H:%M} ~ {now_local:%Y-%m-%d %H:%M}（{timezone_label}）",
        f"共 {len(items)} 条，按类别分组，组内按发布时间倒序。",
        "",
    ]

    if not items:
        lines.append("这段时间暂时没有新的精选条目。")
        return "\n".join(lines).strip() + "\n"

    grouped: OrderedDict[str | None, list[dict[str, Any]]] = OrderedDict((key, []) for key in CATEGORY_LABELS)
    for item in items:
        category = item.get("category")
        if not isinstance(category, str) or category not in CATEGORY_LABELS:
            category = None
        grouped[category].append(item)

    index = 1
    for category, category_items in grouped.items():
        if not category_items:
            continue

        lines.append(f"## {CATEGORY_LABELS.get(category, '其他')}")
        for item in category_items:
            lines.append(
                format_item(
                    index,
                    text_value(item.get("title")) or text_value(item.get("originalTitle")) or "未命名条目",
                    source_name(item),
                    text_value(item.get("summary")),
                    item_url(item),
                    human_time(item.get("publishedAt"), tz, now_local),
                )
            )
            lines.append("")
            index += 1

    return "\n".join(lines).strip() + "\n"


def daily_digest(tz: ZoneInfo) -> str:
    response = fetch_json("/api/v1/dailies/latest")
    data = response.get("report")
    if not isinstance(data, dict):
        raise RuntimeError("AI HOT response is missing the daily report object")
    date = text_value(data.get("date"))
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError("AI HOT daily report is missing a valid date") from exc
    sections = object_list(data.get("sections"), "report.sections")
    flashes = object_list(data.get("flashes"), "report.flashes")

    lines = [f"**AI HOT 日报 · {date}**", ""]
    lead = data.get("lead")
    if lead is not None and not isinstance(lead, dict):
        raise RuntimeError("AI HOT daily report lead must be an object or null")
    if lead:
        if title := text_value(lead.get("title")):
            lines.extend([f"## {title}", ""])
        if paragraph := text_value(lead.get("leadParagraph")):
            lines.extend([textwrap.fill(paragraph, width=100), ""])

    index = 1
    for section in sections:
        section_items = object_list(section.get("items"), "report.sections[].items")
        if not section_items:
            continue
        lines.append(f"## {text_value(section.get('label')) or '未分类'}")
        for item in section_items:
            lines.append(
                format_item(
                    index,
                    text_value(item.get("title")) or "未命名条目",
                    source_name(item),
                    text_value(item.get("summary")),
                    item_url(item),
                )
            )
            lines.append("")
            index += 1

    if flashes:
        now_local = datetime.now(timezone.utc).astimezone(tz)
        lines.append("## 快讯")
        for flash in flashes:
            time_label = human_time(flash.get("publishedAt"), tz, now_local)
            source = source_name(flash)
            url = item_url(flash)
            suffix = f"（{time_label}）" if time_label else ""
            if url:
                lines.append(f"- **{text_value(flash.get('title')) or '未命名快讯'}** — {source}{suffix}\n  {url}")
            else:
                lines.append(f"- **{text_value(flash.get('title')) or '未命名快讯'}** — {source}{suffix}")

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
    try:
        if args.mode == "daily":
            digest = daily_digest(tz)
        else:
            digest = selected_digest(args.hours, args.take, tz)
    except (RuntimeError, ValueError) as exc:
        print(f"Digest generation failed: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(digest, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

