"""Local HTTP API and static interface. No third-party dependencies required."""
import argparse
import json
import mimetypes
import os
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .engine import ROOT, IntegrityError, ResearchError
from .store import Store, dataset, seed


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ResearchReplay/0.1"

        def valid_host(self):
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def send(self, status, body, content_type="application/json; charset=utf-8", download=None):
            raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if content_type.startswith("application/json") else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Use the local workspace URL."})
            try:
                parsed = urlparse(self.path)
                path, query = parsed.path, parse_qs(parsed.query)
                if path == "/api/health":
                    return self.send(200, {"ok": True})
                if path == "/api/runs":
                    return self.send(200, {"runs": store.list()})
                if path == "/api/compare":
                    return self.send(200, store.compare(query["before"][0], query["after"][0]))
                if path.startswith("/api/runs/"):
                    parts = path.strip("/").split("/")
                    run = store.get(parts[2])
                    if len(parts) == 4 and parts[3] == "export":
                        return self.send(200, {"format": "research-replay-bundle-v1", "run": run}, download=f"research-run-{run['id']}.json")
                    if len(parts) == 3:
                        return self.send(200, run)
                    raise KeyError("Route not found.")
                files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
                if path not in files:
                    return self.send(404, {"error": "Not found."})
                file = ROOT / "static" / files[path]
                return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            except (KeyError, IndexError):
                self.send(404, {"error": "Run or route not found."})
            except ResearchError as error:
                self.send(409 if isinstance(error, IntegrityError) else 400, {"error": str(error)})
            except Exception:
                logging.exception("GET request failed")
                self.send(500, {"error": "The server could not complete this request. Check the local database and server terminal."})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {"error": "Use the local workspace URL."})
            # Local-only service. Browser writes must come from the same origin;
            # non-browser clients are supported without CORS or external binds.
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.send(403, {"error": "Cross-origin writes are not allowed."})
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.send(403, {"error": "Cross-site writes are not allowed."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 2_000_000:
                    return self.send(413, {"error": "Request is too large."})
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send(415, {"error": "Send application/json."})
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ResearchError("Request body must be an object.")
                path = urlparse(self.path).path
                if path == "/api/runs":
                    revision = payload.get("dataset", "original")
                    if revision not in {"original", "corrected"}:
                        raise ResearchError("Unknown dataset snapshot.")
                    rows, provenance = dataset(revision == "corrected")
                    result = store.create(label=payload.get("label", "Research experiment"), model=payload.get("model", "equal-weight"), config=payload.get("config", {"lookback": 20, "horizon": 5}), rows=rows, provenance=provenance, parent=payload.get("parent"))
                    return self.send(201, result)
                if path == "/api/import":
                    rows = payload.get("rows")
                    result = store.create(label=payload.get("label", "Imported observations"), model=payload.get("model", "equal-weight"), config=payload.get("config", {"lookback": 20, "horizon": 5}), rows=rows, provenance={"source": "User-imported observations", "scenario": "User supplied; source and publication history not independently verified"}, parent=payload.get("parent"))
                    return self.send(201, result)
                if path == "/api/release":
                    return self.send(200, store.release(payload["approved_id"], payload["candidate_id"]))
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    if parts[3] == "replay":
                        return self.send(200, store.replay(parts[2]))
                    if parts[3] == "approve":
                        return self.send(200, store.approve(parts[2]))
                raise KeyError("Unknown endpoint.")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as error:
                self.send(409 if isinstance(error, IntegrityError) else 400, {"error": str(error) if isinstance(error, ResearchError) else "Invalid request body."})
            except KeyError:
                self.send(404, {"error": "Run, required field, or route not found."})
            except Exception:
                logging.exception("POST request failed")
                self.send(500, {"error": "The server could not complete this request."})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Run the financial research replay workbench locally.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=os.environ.get("REPLAY_DB", str(ROOT / ".replay/research.sqlite3")))
    args = parser.parse_args()
    store = Store(args.db)
    seed(store)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(store))
    print(f"Research Replay is running at http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
