#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def disabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"0", "false", "no", "off"}


def main() -> int:
    if disabled(os.getenv("AIHOT_CREATE_GITHUB_ISSUE")):
        print("GitHub issue publishing is disabled.")
        return 0

    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    if not token or not repository:
        print("GITHUB_TOKEN or GITHUB_REPOSITORY is missing; skipping issue publishing.")
        return 0

    digest_path = Path(sys.argv[1] if len(sys.argv) > 1 else "digest.md")
    body = digest_path.read_text(encoding="utf-8")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    title = os.getenv("AIHOT_ISSUE_TITLE") or f"AI HOT · {today}"

    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aihot-daily-digest",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    print(f"Created issue: {result.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

