"""Standalone integrity verification and replay of an exported run bundle."""
from .engine import IntegrityError, canonical, digest, environment, execute

MANIFEST_FIELDS = (
    "schema", "id", "label", "created_at", "model", "artifacts", "parent",
    "provenance", "observations", "as_of",
)


def verify_bundle(bundle):
    try:
        if bundle["format"] != "research-replay-bundle-v1":
            raise IntegrityError("Unsupported bundle format.")
        run = bundle["run"]
        manifest = {key: run[key] for key in MANIFEST_FIELDS}
        if digest(canonical(manifest)) != run["manifest_sha256"]:
            raise IntegrityError("Exported manifest checksum does not match.")
        if set(run["artifacts"]) != {"source", "data", "config", "environment", "output"}:
            raise IntegrityError("Bundle must contain all five artifacts.")
        for name, expected in run["artifacts"].items():
            content = run[name].encode("utf-8") if name == "source" else canonical(run[name])
            if digest(content) != expected:
                raise IntegrityError(f"Exported {name} artifact checksum does not match.")
        if run["environment"] != environment():
            raise IntegrityError("Bundle environment differs. Use the recorded Python version, platform and engine source.")
        output = execute(run["source"].encode("utf-8"), run["data"], run["config"])
        actual = digest(canonical(output))
        if actual != run["artifacts"]["output"]:
            raise IntegrityError("Re-executed bundle output differs from the captured output.")
        return {"matched": True, "run_id": run["id"], "output_sha256": actual, "output": output}
    except (KeyError, TypeError, AttributeError) as error:
        raise IntegrityError("Bundle is incomplete or malformed.") from error
