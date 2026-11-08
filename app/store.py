"""SQLite-backed immutable run records and SHA-256-addressed artifacts."""
import csv
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from .engine import ROOT, MODELS, IntegrityError, ResearchError, canonical, digest, environment, execute, normalise_config, normalise_rows

SCHEMA = """
    CREATE TABLE IF NOT EXISTS blobs (sha TEXT PRIMARY KEY, content BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, manifest BLOB NOT NULL, sha TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS approvals (run_id TEXT PRIMARY KEY REFERENCES runs(id), manifest_sha TEXT NOT NULL, approved_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS immutable_runs_update BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT, 'Run records are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_runs_delete BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT, 'Run records are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_blobs_update BEFORE UPDATE ON blobs BEGIN SELECT RAISE(ABORT, 'Artifacts are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_blobs_delete BEFORE DELETE ON blobs BEGIN SELECT RAISE(ABORT, 'Artifacts are immutable'); END;
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def blob(self, sha):
        with self.connect() as db:
            row = db.execute("SELECT content FROM blobs WHERE sha=?", (sha,)).fetchone()
        if row is None or digest(row["content"]) != sha:
            raise IntegrityError("Missing or corrupted artifact: " + sha[:12])
        return row["content"]

    def create(self, *, label, model, config, rows, provenance, parent=None):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
            raise ResearchError("Give the run a label of 1 to 100 characters.")
        if model not in MODELS:
            raise ResearchError("Unknown model implementation.")
        if parent:
            self.get(parent)
        rows = normalise_rows(rows)
        config = normalise_config(config, len(rows))
        source = MODELS[model].read_bytes()
        output = execute(source, rows, config)
        payloads = {"data": canonical(rows), "config": canonical(config), "source": source, "environment": canonical(environment()), "output": canonical(output)}
        manifest = {
            "schema": 1, "id": uuid.uuid4().hex[:12], "label": label.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(), "model": model,
            "artifacts": {name: digest(content) for name, content in payloads.items()},
            "parent": parent, "provenance": provenance, "observations": len(rows),
            "as_of": rows[-1]["date"],
        }
        raw = canonical(manifest)
        with self.connect() as db:
            for content in payloads.values():
                db.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest(content), content))
            db.execute("INSERT INTO runs VALUES (?, ?, ?)", (manifest["id"], raw, digest(raw)))
        return self.get(manifest["id"])

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            approval = db.execute("SELECT * FROM approvals WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Run not found.")
        if digest(row["manifest"]) != row["sha"]:
            raise IntegrityError("Run manifest failed its integrity check.")
        manifest = json.loads(row["manifest"])
        artifacts = {name: self.blob(sha) for name, sha in manifest["artifacts"].items()}
        return {**manifest, "manifest_sha256": row["sha"], "approved": bool(approval),
                "approval": dict(approval) if approval else None,
                "data": json.loads(artifacts["data"]), "config": json.loads(artifacts["config"]),
                "environment": json.loads(artifacts["environment"]), "output": json.loads(artifacts["output"]),
                "source": artifacts["source"].decode("utf-8")}

    def list(self):
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 200")]
        return [{key: value for key, value in self.get(i).items() if key not in {"data", "source", "environment"}} for i in ids]

    def replay(self, run_id):
        run = self.get(run_id)
        if run["environment"] != environment():
            raise IntegrityError("Execution environment has changed. Use the original Python runtime and engine to replay this run.")
        output = execute(run["source"].encode(), run["data"], run["config"])
        actual = digest(canonical(output))
        if actual != run["artifacts"]["output"]:
            raise IntegrityError("Replay output does not match the captured output.")
        return {"matched": True, "run_id": run_id, "expected_sha256": run["artifacts"]["output"], "actual_sha256": actual, "output": output, "checks": ["Manifest integrity", "All five artifact hashes", "Execution environment", "Reviewed model source", "Re-executed output hash"]}

    def approve(self, run_id):
        self.replay(run_id)
        run = self.get(run_id)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO approvals VALUES (?, ?, ?)", (run_id, run["manifest_sha256"], datetime.now(timezone.utc).isoformat()))
        return self.get(run_id)

    def release(self, approved_id, candidate_id):
        approved, candidate = self.get(approved_id), self.get(candidate_id)
        if not approved["approved"]:
            raise ResearchError("Approve the reference run before checking a release.")
        if approved["approval"]["manifest_sha"] != approved["manifest_sha256"]:
            raise IntegrityError("Approval does not match the reference manifest.")
        # Identity, timestamps and labels may differ; all executable artifacts must match.
        mismatches = [name for name in approved["artifacts"] if approved["artifacts"][name] != candidate["artifacts"][name]]
        self.replay(approved_id)
        self.replay(candidate_id)
        return {"allowed": not mismatches, "approved_id": approved_id, "candidate_id": candidate_id, "mismatches": mismatches,
                "message": "All approved artifacts match. Release check passed." if not mismatches else "Release blocked: " + ", ".join(mismatches) + " differs from the approved run."}

    def compare(self, before_id, after_id):
        before, after = self.get(before_id), self.get(after_id)
        old = {r["date"]: r["value"] for r in before["data"]}
        new = {r["date"]: r["value"] for r in after["data"]}
        changes = [{"date": day, "before": old.get(day), "after": new.get(day),
                    "kind": "added" if day not in old else "removed" if day not in new else "modified"}
                   for day in sorted(set(old) | set(new)) if old.get(day) != new.get(day)]
        config_changes = [{"key": key, "before": before["config"].get(key), "after": after["config"].get(key)}
                          for key in sorted(set(before["config"]) | set(after["config"])) if before["config"].get(key) != after["config"].get(key)]
        # Ordered counterfactuals on a COMMON forecast step. Contributions depend
        # on this stated order and must not be interpreted as causal attribution.
        stages = [("Reference", before["source"], before["data"], before["config"]),
                  ("Data", before["source"], after["data"], before["config"]),
                  ("Model", after["source"], after["data"], before["config"]),
                  ("Config", after["source"], after["data"], after["config"])]
        waterfall, previous = [], None
        common_step = min(before["config"]["horizon"], after["config"]["horizon"])
        for name, source, rows, config in stages:
            try:
                output = execute(source.encode(), rows, config)
                with localcontext() as ctx:
                    ctx.prec = 34
                    value = Decimal(output["forecast"][common_step - 1])
                    contribution = "0" if previous is None else str((value - previous) * 100)
                    waterfall.append({"stage": name, "yield": str(value), "change_bps": contribution})
                    previous = value
            except ResearchError as error:
                waterfall = []
                attribution_error = str(error)
                break
        else:
            attribution_error = None
        return {"before": before, "after": after, "data_changes": changes, "config_changes": config_changes,
                "changed_artifacts": [name for name in before["artifacts"] if before["artifacts"][name] != after["artifacts"][name]],
                "waterfall": waterfall, "attribution_error": attribution_error, "common_step": common_step,
                "attribution_note": "Ordered counterfactual replay: data → model → config. Effects can interact; a different order can allocate changes differently. This is not causal attribution. Runs execute in the current reviewed engine."}


def dataset(revised=False):
    # Wrangler embeds the same verified source data in a generated Python module.
    # CPython local mode continues to read the original files from the repository.
    import sys
    if sys.platform == "emscripten":
        from ._cloud_data import CSV_BYTES, PROVENANCE
        metadata, raw = dict(PROVENANCE), CSV_BYTES
    else:
        metadata = json.loads((ROOT / "data/provenance.json").read_text("utf-8"))
        raw = (ROOT / "data/dgs10.csv").read_bytes()
    if digest(raw) != metadata["raw_csv_sha256"]:
        raise IntegrityError("Bundled CSV does not match its recorded provenance checksum.")
    rows = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        if row["DGS10"] and row["DGS10"] != ".":
            rows.append({"date": row.get("observation_date", row.get("DATE")), "value": row["DGS10"]})
    rows = normalise_rows(rows)
    if revised:
        # Deliberate injected vendor correction, NOT an actual FRED revision.
        target = rows[-21]
        original = target["value"]
        target["value"] = str(Decimal(original) + Decimal("0.25"))
        metadata = {**metadata, "scenario": "Injected correction (+25 bp); not an actual FRED revision", "injected_change": {"date": target["date"], "before": original, "after": target["value"]}}
    else:
        metadata = {**metadata, "scenario": "Unmodified downloaded snapshot"}
    return rows, metadata


def seed(store):
    if store.list():
        return
    rows, provenance = dataset()
    original = store.create(label="01 · Original research", model="equal-weight", config={"lookback": 20, "horizon": 5}, rows=rows, provenance=provenance)
    revised, revised_provenance = dataset(True)
    store.create(label="02 · Data correction", model="equal-weight", config={"lookback": 20, "horizon": 5}, rows=revised, provenance=revised_provenance, parent=original["id"])
    store.create(label="03 · Model change", model="recency-weighted", config={"lookback": 20, "horizon": 5}, rows=revised, provenance=revised_provenance, parent=original["id"])
    store.create(label="04 · Lookback change", model="recency-weighted", config={"lookback": 10, "horizon": 5}, rows=revised, provenance=revised_provenance, parent=original["id"])
