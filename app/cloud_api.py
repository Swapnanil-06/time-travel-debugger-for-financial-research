"""Transport-independent API dispatch shared by the Cloudflare Worker and tests."""
from urllib.parse import parse_qs, urlparse

from .engine import ResearchError
from .store import dataset

MAX_BODY_BYTES = 1_000_000
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def dispatch(store, method, url, payload=None):
    """Return status, JSON data and additional headers. All work is synchronous."""
    parsed = urlparse(url)
    path, query = parsed.path, parse_qs(parsed.query)
    if method == "GET":
        if path == "/api/runs":
            return 200, {"runs": store.list()}, {}
        if path == "/api/compare":
            return 200, store.compare(query["before"][0], query["after"][0]), {}
        parts = path.strip("/").split("/")
        if len(parts) in {3, 4} and parts[:2] == ["api", "runs"]:
            run = store.get(parts[2])
            if len(parts) == 3:
                return 200, run, {}
            if parts[3] == "export":
                return 200, {"format": "research-replay-bundle-v1", "run": run}, {"Content-Disposition": f'attachment; filename="research-run-{run["id"]}.json"'}
    elif method == "POST":
        if not isinstance(payload, dict):
            raise ResearchError("Request body must be an object.")
        if path in {"/api/runs", "/api/import"}:
            if path == "/api/import":
                rows = payload.get("rows")
                provenance = {"source": "User-imported observations", "scenario": "User supplied; source and publication history not independently verified"}
            else:
                revision = payload.get("dataset", "original")
                if revision not in {"original", "corrected"}:
                    raise ResearchError("Unknown dataset snapshot.")
                rows, provenance = dataset(revision == "corrected")
            result = store.create(label=payload.get("label", "Research experiment"), model=payload.get("model", "equal-weight"), config=payload.get("config", {"lookback": 20, "horizon": 5}), rows=rows, provenance=provenance, parent=payload.get("parent"))
            return 201, result, {}
        if path == "/api/release":
            return 200, store.release(payload["approved_id"], payload["candidate_id"]), {}
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "runs"]:
            if parts[3] == "replay":
                return 200, store.replay(parts[2]), {}
            if parts[3] == "approve":
                return 200, store.approve(parts[2]), {}
    raise KeyError("Run, required field, or route not found.")
