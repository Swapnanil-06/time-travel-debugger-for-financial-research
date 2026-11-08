"""Contract tests for the synchronous Durable Object SQLite adapter."""
import sqlite3
import unittest

from app.cloud_api import dispatch
from app.cloud_store import CloudStore
from app.engine import ResearchError
from app.store import dataset, seed


class NativeCursor:
    def __init__(self, rows):
        self.rows = rows

    def toArray(self):
        return self.rows


class NativeSql:
    """The DO SQL API shape backed by SQLite for storage contract tests."""
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    def exec(self, statement, *bindings):
        if "CREATE TABLE IF NOT EXISTS blobs" in statement:
            self.connection.executescript(statement)
            return NativeCursor([])
        cursor = self.connection.execute(statement, bindings)
        return NativeCursor([dict(row) for row in cursor.fetchall()])


class CloudStoreTests(unittest.TestCase):
    def setUp(self):
        self.sql = NativeSql()
        self.addCleanup(self.sql.connection.close)
        self.store = CloudStore(self.sql)
        seed(self.store)

    def test_seed_and_lossless_byte_transport(self):
        self.assertEqual(len(self.store.list()), 4)
        for run in self.store.list():
            self.assertTrue(self.store.replay(run["id"])["matched"])
        content = self.sql.connection.execute("SELECT content FROM blobs LIMIT 1").fetchone()[0]
        self.assertTrue(content.startswith("b64:"))

    def test_shared_comparison_and_release_logic(self):
        runs = list(reversed(self.store.list()))
        before, after = runs[0]["id"], runs[1]["id"]
        self.assertEqual(len(self.store.compare(before, after)["data_changes"]), 1)
        self.store.approve(before)
        self.assertFalse(self.store.release(before, after)["allowed"])
        self.assertTrue(self.store.release(before, before)["allowed"])

    def test_cloud_observation_limit(self):
        rows, provenance = dataset()
        with self.assertRaisesRegex(ResearchError, "1000 observations"):
            self.store.create(label="Too large", model="equal-weight", config={"lookback": 20, "horizon": 5}, rows=rows * 10, provenance=provenance)

    def test_dispatch_import_validation_and_export(self):
        run = self.store.list()[0]
        status, result, headers = dispatch(self.store, "GET", f"/api/runs/{run['id']}/export")
        self.assertEqual(status, 200)
        self.assertEqual(result["format"], "research-replay-bundle-v1")
        self.assertIn("attachment", headers["Content-Disposition"])
        with self.assertRaises(ResearchError):
            dispatch(self.store, "POST", "/api/import", {"rows": []})

    def test_cloud_records_still_reject_updates(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql.exec("UPDATE runs SET sha='edited'")
