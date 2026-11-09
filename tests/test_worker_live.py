"""Run against real workerd with REPLAY_WORKER_URL=http://127.0.0.1:8787.

No Cloudflare account is needed. These checks exercise SDK/FFI behavior and actual
SQLite-backed Durable Objects; the regular CPython suite skips them by default.
"""
import http.cookiejar
import json
import os
import unittest
import urllib.error
import urllib.request

BASE = os.environ.get("REPLAY_WORKER_URL")


def browser():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


@unittest.skipUnless(BASE, "Set REPLAY_WORKER_URL to test the actual Wrangler runtime")
class LiveWorkerTests(unittest.TestCase):
    def setUp(self):
        self.client = browser()

    def request(self, method, path, payload=None, client=None, headers=None):
        content = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Content-Type": "application/json"} if content is not None else {}
        request_headers.update(headers or {})
        request = urllib.request.Request(BASE + path, data=content, method=method, headers=request_headers)
        try:
            result = (client or self.client).open(request, timeout=30)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            body = result.read()
            return result.status, result.headers, json.loads(body) if "application/json" in result.headers.get("Content-Type", "") else body

    def test_full_capture_compare_replay_approval_release_export(self):
        status, _, result = self.request("GET", "/api/runs")
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result["runs"]), 4)
        runs = list(reversed(result["runs"]))
        before, after = runs[0]["id"], runs[1]["id"]
        status, _, result = self.request("GET", f"/api/compare?before={before}&after={after}")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["waterfall"][1]["change_bps"], "-6.2500")
        for action in ["replay", "approve"]:
            status, _, result = self.request("POST", f"/api/runs/{before}/{action}", {})
            self.assertEqual(status, 200, result)
        for candidate, allowed in [(after, False), (before, True)]:
            status, _, result = self.request("POST", "/api/release", {"approved_id": before, "candidate_id": candidate})
            self.assertEqual(status, 200, result)
            self.assertEqual(result["allowed"], allowed)
        status, _, result = self.request("POST", "/api/runs", {"label": "Captured in workerd", "model": "recency-weighted"})
        self.assertEqual(status, 201, result)
        run_id = result["id"]
        status, headers, result = self.request("GET", f"/api/runs/{run_id}/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(result["format"], "research-replay-bundle-v1")
        status, _, result = self.request("GET", "/api/runs")
        self.assertEqual(len(result["runs"]), 5)

    def test_browser_workspaces_are_isolated(self):
        _, _, first = self.request("GET", "/api/runs")
        other = browser()
        _, _, second = self.request("GET", "/api/runs", client=other)
        self.assertNotEqual(first["runs"][0]["id"], second["runs"][0]["id"])
        status, _, _ = self.request("GET", f"/api/runs/{first['runs'][0]['id']}", client=other)
        self.assertEqual(status, 404)

    def test_invalid_requests_and_cross_origin_writes(self):
        for payload in [{"model": "unknown"}, {"config": {"lookback": 0, "horizon": 5}}, []]:
            status, _, result = self.request("POST", "/api/runs", payload)
            self.assertEqual(status, 400, result)
        status, _, _ = self.request("POST", "/api/runs", {}, headers={"Origin": "https://other.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/runs", {}, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_cloud_import_and_bound(self):
        rows = [{"date": f"2024-01-{i:02}", "value": str(4 + i / 100)} for i in range(1, 9)]
        status, _, result = self.request("POST", "/api/import", {"rows": rows, "config": {"lookback": 3, "horizon": 2}})
        self.assertEqual(status, 201, result)
        status, _, result = self.request("POST", "/api/import", {"rows": rows * 126})
        self.assertEqual(status, 400, result)

    def test_assets_and_health(self):
        for path in ["/", "/app.js", "/style.css", "/favicon.svg", "/instrument-serif.woff2", "/manrope.woff2"]:
            status, headers, content = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(content)
            self.assertIn("Content-Security-Policy", headers)
        status, _, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["deployment"], "cloudflare")
