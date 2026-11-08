"""Cloudflare Python Worker: static assets + browser-isolated persistent SQLite."""
import json
import logging
import re
import secrets
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from workers import DurableObject, Response, WorkerEntrypoint

from app.cloud_api import CSP, MAX_BODY_BYTES, dispatch
from app.cloud_store import CloudStore
from app.engine import IntegrityError, ResearchError
from app.store import seed

COOKIE = "rr_workspace"
TOKEN = re.compile(r"^[a-f0-9]{64}$")


def response(status, body, headers=None):
    return Response(json.dumps(body, ensure_ascii=False, allow_nan=False), status=status, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": CSP,
        **(headers or {}),
    })


class ResearchWorkspace(DurableObject):
    """One SQLite-backed actor per unguessable browser workspace cookie."""

    def execute_request(self, method, url, payload):
        # Keep everything synchronous inside transactionSync: no cursor, partial
        # capture or approval can interleave with another request to this object.
        store = CloudStore(self.ctx.storage.sql)
        seed(store)
        status, body, headers = dispatch(store, method, url, payload)
        return json.dumps({"status": status, "body": body, "headers": headers}, ensure_ascii=False)

    async def fetch(self, request):
        try:
            payload = json.loads(await request.text()) if request.method == "POST" else None
            result = self.ctx.storage.transactionSync(lambda: self.execute_request(request.method, request.url, payload))
            decoded = json.loads(result)
            return response(decoded["status"], decoded["body"], decoded["headers"])
        except IntegrityError as error:
            return response(409, {"error": str(error)})
        except ResearchError as error:
            return response(400, {"error": str(error)})
        except (KeyError, IndexError):
            return response(404, {"error": "Run, required field, or route not found."})
        except (ValueError, TypeError):
            return response(400, {"error": "Invalid request body."})
        except Exception:
            logging.exception("Workspace request failed")
            return response(500, {"error": "The research workspace could not complete this request."})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        if not url.path.startswith("/api/"):
            if request.method not in {"GET", "HEAD"}:
                return response(405, {"error": "Method not allowed."}, {"Allow": "GET, HEAD"})
            # Headers are declared in static/_headers and handled by Assets.
            return await self.env.ASSETS.fetch(request)
        if request.method not in {"GET", "POST"}:
            return response(405, {"error": "Method not allowed."}, {"Allow": "GET, POST"})
        if url.path == "/api/health":
            return response(200, {"ok": True, "deployment": "cloudflare", "storage": "Durable Object SQLite", "workspace": "browser-isolated"})
        if request.method == "POST":
            if request.headers.get("Origin") not in {None, "", f"{url.scheme}://{url.netloc}"} or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return response(403, {"error": "Cross-origin writes are not allowed."})
            if (request.headers.get("Content-Type") or "").split(";")[0] != "application/json":
                return response(415, {"error": "Send application/json."})
            try:
                if int(request.headers.get("Content-Length") or "0") > MAX_BODY_BYTES:
                    return response(413, {"error": "Request is too large."})
                if len((await request.clone().text()).encode("utf-8")) > MAX_BODY_BYTES:
                    return response(413, {"error": "Request is too large."})
            except (ValueError, TypeError):
                return response(400, {"error": "Invalid request body."})
        cookies = SimpleCookie()
        try:
            cookies.load(request.headers.get("Cookie") or "")
        except Exception:
            pass
        token = cookies[COOKIE].value if COOKIE in cookies else ""
        fresh = not TOKEN.fullmatch(token)
        if fresh:
            token = secrets.token_hex(32)
        object_id = self.env.WORKSPACES.idFromName(token)
        result = await self.env.WORKSPACES.get(object_id).fetch(request)
        if fresh:
            # Reconstruct the response to attach a cookie without mutating an
            # immutable response returned by a service binding.
            secure = "; Secure" if url.scheme == "https" else ""
            return Response(await result.text(), status=result.status, headers={
                "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
                "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                "Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000{secure}",
                **({"Content-Disposition": result.headers.get("Content-Disposition")} if result.headers.get("Content-Disposition") else {}),
            })
        return result
