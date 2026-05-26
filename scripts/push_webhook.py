#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import base64
import hashlib
import hmac
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "aihot-daily-digest"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        print(f"Webhook sent: {response.status}")


def post_form(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "aihot-daily-digest"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        print(f"Webhook sent: {response.status}")


def with_dingtalk_sign(url: str, secret: str | None) -> str:
    if not secret:
        return url

    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}timestamp={timestamp}&sign={sign}"


def main() -> int:
    url = os.getenv("AIHOT_WEBHOOK_URL")
    if not url:
        print("AIHOT_WEBHOOK_URL is not set; skipping webhook.")
        return 0

    digest_path = Path(sys.argv[1] if len(sys.argv) > 1 else "digest.md")
    content = digest_path.read_text(encoding="utf-8")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    title = os.getenv("AIHOT_WEBHOOK_TITLE") or f"AI HOT · {today}"
    webhook_type = (os.getenv("AIHOT_WEBHOOK_TYPE") or "generic").strip().lower()

    if webhook_type == "dingtalk":
        url = with_dingtalk_sign(url, os.getenv("AIHOT_DINGTALK_SECRET"))
        post_json(url, {"msgtype": "markdown", "markdown": {"title": title, "text": content}})
    elif webhook_type == "serverchan":
        post_form(url, {"title": title, "desp": content})
    else:
        post_json(url, {"title": title, "content": content})
    return 0


if __name__ == "__main__":
    sys.exit(main())
