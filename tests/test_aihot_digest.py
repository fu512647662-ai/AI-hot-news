from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import aihot_digest as digest
from scripts import publish_github_issue as publisher
from scripts import push_webhook as webhook


NOW = datetime(2026, 11, 4, 8, 30, tzinfo=timezone.utc)
TZ = ZoneInfo("Asia/Shanghai")


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def item(name="news", hours=1, **changes):
    value = {
        "id": name,
        "title": name,
        "originalTitle": "Original title",
        "summary": "A concise summary.",
        "source": {"name": "Official blog"},
        "links": {"original": f"https://example.com/{name}", "aihot": f"https://aihot.news/items/{name}"},
        "publishedAt": (NOW - timedelta(hours=hours)).isoformat(),
        "discoveredAt": NOW.isoformat(),
        "category": "ai-models",
        "score": 80,
        "selected": True,
    }
    value.update(changes)
    return value


def items_response(items=None, more=False, cursor=None):
    values = items if items is not None else []
    return {"schemaVersion": 1, "items": values, "page": {"count": len(values), "hasMore": more, "nextCursor": cursor}}


def daily_response():
    return {
        "schemaVersion": 1,
        "report": {
            "date": "2026-11-03",
            "generatedAt": "2026-11-03T00:00:00Z",
            "windowStart": "2026-11-02T00:00:00Z",
            "windowEnd": "2026-11-03T00:00:00Z",
            "links": {"aihot": "https://aihot.news/daily/2026-11-03"},
            "lead": {"title": "Daily lead", "leadParagraph": "Daily overview."},
            "sections": [{"label": "模型发布/更新", "items": [item("daily-item")]}],
            "flashes": [item("flash", 0.5)],
        },
    }


def http_response(data, *, compressed=False):
    response = io.BytesIO(json.dumps(data).encode("utf-8"))
    headers = Message()
    if compressed:
        response = io.BytesIO(gzip.compress(response.getvalue()))
        headers["Content-Encoding"] = "gzip"
    response.headers = headers
    return response


def http_error(status, code="server_error", retry_after=None, **fields):
    problem = {"type": "/problems/test", "title": "Test error", "status": status,
               "detail": "Diagnostic detail", "code": code, "requestId": "test-request", **fields}
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://aihot.news/api/v1/items", status, "test", headers,
                                  io.BytesIO(json.dumps(problem).encode()))


class SelectedDigestTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(digest, "datetime", FrozenDateTime)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def render(self, response, hours=24, take=50, tz=TZ):
        with patch.object(digest, "fetch_json", return_value=response) as fetch:
            text = digest.selected_digest(hours, take, tz)
        return text, fetch

    def test_v1_parameters_and_nested_fields(self):
        text, fetch = self.render(items_response([item()]))
        fetch.assert_called_once_with("/api/v1/items", {"mode": "selected", "window": "24h", "by": "published", "limit": 50})
        self.assertIn("1. **news** — Official blog", text)
        self.assertIn("https://example.com/news", text)
        self.assertIn("## 模型发布/更新", text)
        self.assertTrue(text.endswith("\n"))

    def test_exact_utc_boundaries_offsets_and_publication_order(self):
        response = items_response([
            item("boundary", 24), item("older", 24.001), item("newer", 1),
            item("future", -1), item("now", 0),
            item("offset", publishedAt="2026-11-04T15:00:00+08:00"),
        ])
        text, _ = self.render(response)
        self.assertIn("共 4 条", text)
        self.assertNotIn("**older**", text)
        self.assertNotIn("**future**", text)
        self.assertLess(text.index("**now**"), text.index("**newer**"))
        self.assertLess(text.index("**newer**"), text.index("**offset**"))
        self.assertLess(text.index("**offset**"), text.index("**boundary**"))

    def test_missing_or_invalid_publication_time_does_not_use_discovery(self):
        invalid = [None, "bad-date", "2026-11-04T08:00:00", 123]
        with redirect_stderr(io.StringIO()) as stderr:
            text, _ = self.render(items_response([item(str(i), publishedAt=value) for i, value in enumerate(invalid)]))
        self.assertIn("共 0 条", text)
        self.assertIn("暂时没有", text)
        self.assertIn("Skipped 4 items", stderr.getvalue())

    def test_window_selection_and_custom_timezone(self):
        for hours, window in [(1, "24h"), (24, "24h"), (25, "7d"), (168, "7d")]:
            with self.subTest(hours=hours):
                text, fetch = self.render(items_response([item("in", hours - 0.1), item("out", hours + 0.1)]), hours=hours, tz=ZoneInfo("UTC"))
                self.assertEqual(fetch.call_args.args[1]["window"], window)
                self.assertIn("共 1 条", text)
                self.assertIn("（UTC）", text)

    def test_unsupported_window_fails_before_network(self):
        for hours in [0, -1, 169]:
            with self.subTest(hours=hours), patch.object(digest, "fetch_json") as fetch:
                with self.assertRaisesRegex(ValueError, "1 and 168"):
                    digest.selected_digest(hours, 50, TZ)
                fetch.assert_not_called()

    def test_take_keeps_original_clamp(self):
        for take, expected in [(0, 1), (-1, 1), (500, 100)]:
            with self.subTest(take=take):
                _, fetch = self.render(items_response(), take=take)
                self.assertEqual(fetch.call_args.args[1]["limit"], expected)

    def test_pagination_filters_before_limiting_and_deduplicates(self):
        responses = [
            items_response([item("unknown", publishedAt=None), item("one")], True, "next/opaque+="),
            items_response([item("one"), item("two", 2)], False),
        ]
        requests = []

        def fetch(path, query):
            requests.append((path, dict(query)))
            return responses.pop(0)

        with patch.object(digest, "fetch_json", side_effect=fetch), redirect_stderr(io.StringIO()):
            text = digest.selected_digest(24, 2, TZ)
        self.assertIn("共 2 条", text)
        self.assertEqual(text.count("**one**"), 1)
        self.assertIn("**two**", text)
        self.assertNotIn("cursor", requests[0][1])
        self.assertEqual(requests[1][1]["cursor"], "next/opaque+=")

    def test_pagination_stops_at_cutoff_or_take(self):
        for values, take in [([item("old", 25)], 50), ([item("one"), item("two", 2)], 1)]:
            with self.subTest(take=take):
                text, fetch = self.render(items_response(values, True, "unused"), take=take)
                self.assertEqual(fetch.call_count, 1)
                self.assertIn("共 0 条" if take == 50 else "共 1 条", text)

    def test_unknown_categories_merge_and_new_title_and_link_fallbacks(self):
        text, _ = self.render(items_response([
            item("fallback", category="new-category", title="", links={"aihot": "https://aihot.news/items/fallback"}),
            item("null-category", category=None, source=None),
            item("opinion", category="opinion"),
        ]))
        self.assertIn("**Original title**", text)
        self.assertIn("https://aihot.news/items/fallback", text)
        self.assertIn("未知来源", text)
        self.assertEqual(text.count("## 其他"), 1)
        self.assertIn("## 观点", text)

    def test_malformed_envelope_and_cursor_fail(self):
        cases = [{}, {"items": None, "page": {}}, {"items": [None], "page": {}},
                 {"items": [], "page": {}}, items_response([], True),]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self.render(response)
        response = items_response([], True, "same")
        with patch.object(digest, "fetch_json", return_value=response) as fetch:
            with self.assertRaisesRegex(RuntimeError, "repeated"):
                digest.selected_digest(24, 50, TZ)
            self.assertEqual(fetch.call_count, 2)

    def test_invalid_cursor_restarts_once_without_partial_or_duplicate_output(self):
        responses = [items_response([item("before-restart")], True, "expired"),
                     digest.APIError(400, {"code": "invalid_cursor"}),
                     items_response([item("after-restart"), item("two", 2)])]
        requests = []

        def fetch(path, query):
            requests.append(dict(query))
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(digest, "fetch_json", side_effect=fetch):
            text = digest.selected_digest(24, 2, TZ)
        self.assertNotIn("**before-restart**", text)
        self.assertIn("**after-restart**", text)
        self.assertNotIn("cursor", requests[2])
        error = digest.APIError(400, {"code": "invalid_cursor"})
        page = items_response([], True, "expired")
        with patch.object(digest, "fetch_json", side_effect=[page, error, page, error]):
            with self.assertRaises(digest.APIError):
                digest.selected_digest(24, 50, TZ)


class DailyDigestTests(unittest.TestCase):
    def test_report_envelope_sections_flashes_and_real_edition_date(self):
        with patch.object(digest, "fetch_json", return_value=daily_response()) as fetch:
            text = digest.daily_digest(TZ)
        fetch.assert_called_once_with("/api/v1/dailies/latest")
        for expected in ["日报 · 2026-11-03", "## Daily lead", "Daily overview.", "## 模型发布/更新",
                         "**daily-item** — Official blog", "https://example.com/daily-item", "## 快讯", "**flash** — Official blog"]:
            self.assertIn(expected, text)

    def test_empty_edition_and_null_lead_are_valid(self):
        response = daily_response()
        response["report"].update(lead=None, sections=[], flashes=[])
        with patch.object(digest, "fetch_json", return_value=response):
            self.assertIn("日报 · 2026-11-03", digest.daily_digest(TZ))

    def test_flash_missing_date_source_or_link_does_not_crash(self):
        response = daily_response()
        response["report"]["flashes"] = [item("flash", publishedAt="bad", source=None, links={})]
        with patch.object(digest, "fetch_json", return_value=response):
            text = digest.daily_digest(TZ)
        self.assertIn("- **flash** — 未知来源", text)

    def test_malformed_report_does_not_publish_an_empty_success(self):
        cases = [{}, {"report": None}, {"report": {}}, daily_response(), daily_response(), daily_response(), daily_response()]
        cases[3]["report"]["date"] = "bad-date"
        cases[4]["report"]["sections"] = None
        cases[5]["report"]["flashes"] = "invalid"
        cases[6]["report"]["lead"] = "invalid"
        for response in cases:
            with self.subTest(response=response), patch.object(digest, "fetch_json", return_value=response):
                with self.assertRaises(RuntimeError):
                    digest.daily_digest(TZ)


class FetchJsonTests(unittest.TestCase):
    def test_url_encoding_headers_plain_and_gzip(self):
        for compressed in [False, True]:
            with self.subTest(compressed=compressed), patch.object(digest.urllib.request, "urlopen", return_value=http_response({"items": []}, compressed=compressed)) as open_url:
                self.assertEqual(digest.fetch_json("/api/v1/items", {"limit": 1, "cursor": "a/b+=", "omit": None}), {"items": []})
                request = open_url.call_args.args[0]
                self.assertEqual(request.full_url, "https://aihot.news/api/v1/items?limit=1&cursor=a%2Fb%2B%3D")
                self.assertEqual(request.get_header("Accept-encoding"), "gzip")
                self.assertEqual(request.get_header("User-agent"), "aihot-daily-digest/2.0")

    def test_http_errors_keep_problem_diagnostics_and_do_not_retry_permanent_errors(self):
        for status in [400, 403, 404]:
            with self.subTest(status=status), patch.object(digest.urllib.request, "urlopen", side_effect=http_error(status, "not_found")) as fetch, patch.object(digest.time, "sleep") as sleep:
                with self.assertRaisesRegex(digest.APIError, f"HTTP {status}.*Diagnostic detail.*code=not_found.*requestId=test-request"):
                    digest.fetch_json("/api/v1/dailies/latest")
                self.assertEqual(fetch.call_count, 1)
                sleep.assert_not_called()

    def test_transient_errors_retry_with_backoff_and_retry_after(self):
        cases = [(http_error(429, retry_after=2), 2), (http_error(503, retryAfter=3), 3),
                 (http_error(502), 1), (urllib.error.URLError("offline"), 1), (TimeoutError("timed out"), 1)]
        for error, delay in cases:
            with self.subTest(error=error), patch.object(digest.urllib.request, "urlopen", side_effect=[error, http_response({"ok": True})]) as fetch, patch.object(digest.time, "sleep") as sleep, redirect_stderr(io.StringIO()):
                self.assertEqual(digest.fetch_json("/api/v1/items"), {"ok": True})
                self.assertEqual(fetch.call_count, 2)
                sleep.assert_called_once_with(delay)

    def test_retry_after_http_date_and_long_delays(self):
        with patch.object(digest, "datetime", FrozenDateTime):
            self.assertEqual(digest.retry_delay("Wed, 04 Nov 2026 08:30:10 GMT", 0), 10)
            self.assertEqual(digest.retry_delay("invalid", 1), 2)
        with patch.object(digest.urllib.request, "urlopen", side_effect=http_error(429, retry_after=120)) as fetch, patch.object(digest.time, "sleep") as sleep:
            with self.assertRaisesRegex(digest.APIError, "Retry-After=120"):
                digest.fetch_json("/api/v1/items")
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()

    def test_retries_are_bounded_including_edge_errors(self):
        for status, attempts in [(503, 3), (566, 2), (567, 2)]:
            errors = [http_error(status) for _ in range(attempts)]
            with self.subTest(status=status), patch.object(digest.urllib.request, "urlopen", side_effect=errors) as fetch, patch.object(digest.time, "sleep"), redirect_stderr(io.StringIO()):
                with self.assertRaises(digest.APIError):
                    digest.fetch_json("/api/v1/items")
                self.assertEqual(fetch.call_count, attempts)

    def test_non_json_http_error_and_malformed_success(self):
        error = urllib.error.HTTPError("https://aihot.news", 404, "not found", Message(), io.BytesIO(b"<html>not found</html>"))
        with patch.object(digest.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(digest.APIError, "not found"):
                digest.fetch_json("/api/v1/items")
        for value in [[], None]:
            with self.subTest(value=value), patch.object(digest.urllib.request, "urlopen", return_value=http_response(value)):
                with self.assertRaisesRegex(RuntimeError, "not an object"):
                    digest.fetch_json("/api/v1/items")
        response = io.BytesIO(b"<html>not JSON</html>")
        response.headers = Message()
        with patch.object(digest.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                digest.fetch_json("/api/v1/items")

    def test_malformed_gzip_is_reported_without_network_retries(self):
        response = io.BytesIO(b"not gzip")
        response.headers = Message()
        response.headers["Content-Encoding"] = "gzip"
        with patch.object(digest.urllib.request, "urlopen", return_value=response) as fetch:
            with self.assertRaisesRegex(RuntimeError, "invalid JSON or gzip"):
                digest.fetch_json("/api/v1/items")
            self.assertEqual(fetch.call_count, 1)


class PipelineTests(unittest.TestCase):
    def generate_file(self, path, mode):
        response = items_response([item()]) if mode == "selected24h" else daily_response()
        with patch.object(digest.sys, "argv", ["aihot_digest.py", "--mode", mode, "--output", str(path)]), patch.object(digest, "fetch_json", return_value=response), patch.object(digest, "datetime", FrozenDateTime), redirect_stdout(io.StringIO()):
            self.assertEqual(digest.main(), 0)
        return path.read_text(encoding="utf-8")

    def test_both_modes_write_utf8_and_issue_body_is_unchanged(self):
        for mode in ["selected24h", "daily"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "nested" / "digest.md"
                content = self.generate_file(path, mode)
                env = {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "example/repo"}
                with patch.dict(os.environ, env, clear=True), patch.object(publisher.sys, "argv", ["publish", str(path)]), patch.object(publisher.urllib.request, "urlopen", return_value=http_response({"html_url": "https://example.com/issue"})) as fetch, redirect_stdout(io.StringIO()):
                    self.assertEqual(publisher.main(), 0)
                    self.assertEqual(json.loads(fetch.call_args.args[0].data)["body"], content)

    def test_webhook_payloads_retain_generated_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "digest.md"
            content = self.generate_file(path, "selected24h")
            for kind in ["generic", "dingtalk", "serverchan"]:
                env = {"AIHOT_WEBHOOK_URL": "https://example.com/hook", "AIHOT_WEBHOOK_TYPE": kind}
                with self.subTest(kind=kind), patch.dict(os.environ, env, clear=True), patch.object(webhook.sys, "argv", ["push", str(path)]), patch.object(webhook, "post_json", return_value={"errcode": 0}) as post_json, patch.object(webhook, "post_form") as post_form, redirect_stdout(io.StringIO()):
                    self.assertEqual(webhook.main(), 0)
                    if kind == "serverchan":
                        self.assertEqual(post_form.call_args.args[1]["desp"], content)
                    else:
                        payload = post_json.call_args.args[1]
                        self.assertEqual(payload["markdown"]["text"] if kind == "dingtalk" else payload["content"], content)

    def test_generation_failure_does_not_write_or_replace_output(self):
        for mode in ["selected24h", "daily"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "digest.md"
                for existing in [False, True]:
                    if existing:
                        path.write_text("previous digest", encoding="utf-8")
                    with patch.object(digest.sys, "argv", ["digest", "--mode", mode, "--output", str(path)]), patch.object(digest, "fetch_json", side_effect=RuntimeError("API unavailable")), redirect_stderr(io.StringIO()):
                        self.assertEqual(digest.main(), 1)
                    self.assertEqual(path.read_text() if path.exists() else None, "previous digest" if existing else None)


if __name__ == "__main__":
    unittest.main()
