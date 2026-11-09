import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.server import handler_for
from app.store import Store, seed


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "api.sqlite3")
        seed(cls.store)
        class QuietHandler(handler_for(cls.store)):
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        body = json.dumps(payload).encode() if payload is not None else None
        all_headers = {"Content-Type": "application/json"} if body else {}
        all_headers.update(headers or {})
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        content, result_headers, status = response.read(), dict(response.getheaders()), response.status
        connection.close()
        return status, result_headers, content

    def test_static_assets_serve_with_security_headers(self):
        for path, mime in [("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css"), ("/favicon.svg", "image/svg+xml"),
                           ("/instrument-serif.woff2", "font/woff2"), ("/manrope.woff2", "font/woff2")]:
            with self.subTest(path=path):
                status, headers, content = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(mime, headers["Content-Type"])
                self.assertIn("Content-Security-Policy", headers)
                self.assertTrue(content)

    def test_http_capture_compare_replay_approve_release_and_export(self):
        status, _, content = self.request("POST", "/api/runs", {"label": "HTTP reference"})
        self.assertEqual(status, 201)
        before = json.loads(content)["id"]
        _, _, content = self.request("POST", "/api/runs", {"label": "HTTP candidate", "dataset": "corrected"})
        after = json.loads(content)["id"]
        status, _, content = self.request("GET", f"/api/compare?before={before}&after={after}")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(content)["data_changes"]), 1)
        for operation in ["replay", "approve"]:
            status, _, _ = self.request("POST", f"/api/runs/{before}/{operation}", {})
            self.assertEqual(status, 200)
        status, _, content = self.request("POST", "/api/release", {"approved_id": before, "candidate_id": after})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(content)["allowed"])
        status, headers, content = self.request("GET", f"/api/runs/{before}/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(content)["format"], "research-replay-bundle-v1")

    def test_import_validation_and_missing_routes(self):
        status, _, _ = self.request("POST", "/api/import", {"rows": []})
        self.assertEqual(status, 400)
        for path in ["/api/runs/missing", "/api/runs", "/../../app/store.py"]:
            status, _, _ = self.request("POST" if path == "/api/runs" else "GET", path, {"model": "unknown"} if path == "/api/runs" else None)
            self.assertEqual(status, 400 if path == "/api/runs" else 404)

    def test_cross_origin_and_rebound_hosts_are_rejected(self):
        status, _, _ = self.request("POST", "/api/runs", {}, {"Origin": "https://other.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/runs", headers={"Host": "other.example"})
        self.assertEqual(status, 403)

    def test_invalid_json_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("POST", "/api/runs", body=b"not json", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        connection.close()
