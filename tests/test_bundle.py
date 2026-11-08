import copy
import tempfile
import unittest
from pathlib import Path

from app.bundle import verify_bundle
from app.engine import IntegrityError
from app.store import Store, dataset


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = Store(Path(self.temp.name) / "runs.sqlite3")
        rows, provenance = dataset()
        run = store.create(label="Bundle", model="equal-weight", config={"lookback": 20, "horizon": 5}, rows=rows, provenance=provenance)
        self.bundle = {"format": "research-replay-bundle-v1", "run": run}

    def test_export_replays_without_database(self):
        self.assertTrue(verify_bundle(self.bundle)["matched"])

    def test_artifact_edit_is_detected(self):
        for name, value in [("data", []), ("source", "print('tampered')"), ("config", {"horizon": 1}), ("output", {}), ("environment", {})]:
            with self.subTest(name=name):
                bundle = copy.deepcopy(self.bundle)
                bundle["run"][name] = value
                with self.assertRaises(IntegrityError):
                    verify_bundle(bundle)

    def test_manifest_edit_is_detected(self):
        self.bundle["run"]["label"] = "Edited after capture"
        with self.assertRaisesRegex(IntegrityError, "manifest"):
            verify_bundle(self.bundle)

    def test_incomplete_bundle_is_rejected(self):
        with self.assertRaises(IntegrityError):
            verify_bundle({})
