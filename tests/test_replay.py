import copy
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from app.engine import MODELS, ROOT, IntegrityError, ResearchError, canonical, digest, execute, normalise_rows
from app.store import Store, dataset, seed


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "runs.sqlite3")
        self.rows, self.provenance = dataset()

    def create(self, **overrides):
        args = dict(label="Test", model="equal-weight", config={"lookback": 20, "horizon": 5}, rows=self.rows, provenance=self.provenance)
        args.update(overrides)
        return self.store.create(**args)

    def test_real_data_matches_recorded_provenance(self):
        self.assertEqual(digest((ROOT / "data/dgs10.csv").read_bytes()), self.provenance["raw_csv_sha256"])
        self.assertEqual(self.rows[0], {"date": "2024-01-02", "value": "3.95"})
        self.assertEqual(self.rows[-1], {"date": "2024-06-28", "value": "4.36"})

    def test_model_known_answer(self):
        rows = [{"date": f"2024-01-0{i+1}", "value": str(value)} for i, value in enumerate([4, 4.1, 4.2, 4.3])]
        output = execute(MODELS["equal-weight"].read_bytes(), rows, {"lookback": 2, "horizon": 2})
        self.assertEqual([Decimal(v) for v in output["forecast"]], [Decimal("4.4"), Decimal("4.5")])
        self.assertEqual(Decimal(output["drift_bps"]), 10)
        self.assertEqual(Decimal(output["daily_vol_bps"]), 0)

    def test_weighted_model_known_answer(self):
        rows = [{"date": f"2024-01-0{i+1}", "value": str(value)} for i, value in enumerate([4, 4.1, 4.2, 4.5])]
        output = execute(MODELS["recency-weighted"].read_bytes(), rows, {"lookback": 2, "horizon": 1})
        with localcontext() as ctx:
            ctx.prec = 34
            self.assertEqual(Decimal(output["forecast"][0]), Decimal("4.5") + Decimal("0.7") / 3)

    def test_replay_matches_exact_bytes(self):
        run = self.create()
        result = self.store.replay(run["id"])
        self.assertTrue(result["matched"])
        self.assertEqual(result["actual_sha256"], run["artifacts"]["output"])

    def test_ambient_decimal_precision_does_not_change_output(self):
        run = self.create()
        with localcontext() as context:
            context.prec = 5
            self.assertTrue(self.store.replay(run["id"])["matched"])

    def test_same_inputs_same_artifacts_different_run_ids(self):
        first, second = self.create(), self.create(label="Another label")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["artifacts"], second["artifacts"])

    def test_injected_correction_changes_forecast_without_changing_last_yield(self):
        first = self.create()
        rows, provenance = dataset(True)
        second = self.create(rows=rows, provenance=provenance)
        self.assertEqual(first["output"]["last_yield"], second["output"]["last_yield"])
        result = self.store.compare(first["id"], second["id"])
        self.assertEqual(len(result["data_changes"]), 1)
        self.assertEqual(set(result["changed_artifacts"]), {"data", "output"})
        self.assertEqual(Decimal(result["waterfall"][1]["change_bps"]), Decimal("-6.25"))
        self.assertTrue(self.store.replay(first["id"])["matched"])

    def test_ordered_counterfactual_contributions_sum(self):
        first = self.create()
        rows, provenance = dataset(True)
        second = self.create(rows=rows, provenance=provenance, model="recency-weighted", config={"lookback": 10, "horizon": 3})
        result = self.store.compare(first["id"], second["id"])
        self.assertEqual(result["common_step"], 3)
        with localcontext() as ctx:
            ctx.prec = 34
            total = sum(Decimal(row["change_bps"]) for row in result["waterfall"][1:])
            expected = (Decimal(second["output"]["forecast"][2]) - Decimal(first["output"]["forecast"][2])) * 100
            self.assertLess(abs(total - expected), Decimal("1e-28"))

    def test_incompatible_counterfactual_is_explained(self):
        first = self.create()
        second = self.create(rows=self.rows[-5:], config={"lookback": 3, "horizon": 2})
        result = self.store.compare(first["id"], second["id"])
        self.assertEqual(result["waterfall"], [])
        self.assertIn("Lookback", result["attribution_error"])

    def test_release_requires_approval(self):
        first = self.create()
        with self.assertRaisesRegex(ResearchError, "Approve"):
            self.store.release(first["id"], first["id"])

    def test_release_accepts_same_artifacts_in_distinct_run(self):
        first, second = self.create(), self.create(label="Production candidate")
        self.store.approve(first["id"])
        result = self.store.release(first["id"], second["id"])
        self.assertTrue(result["allowed"])

    def test_release_rejects_changed_data_code_and_configuration(self):
        first = self.create()
        self.store.approve(first["id"])
        rows, provenance = dataset(True)
        for second, artifact in [(self.create(rows=rows, provenance=provenance), "data"), (self.create(model="recency-weighted"), "source"), (self.create(config={"lookback": 10, "horizon": 5}), "config")]:
            with self.subTest(artifact=artifact):
                result = self.store.release(first["id"], second["id"])
                self.assertFalse(result["allowed"])
                self.assertIn(artifact, result["mismatches"])

    def test_sql_triggers_reject_mutation(self):
        run = self.create()
        for statement in ["UPDATE runs SET sha='tampered'", "DELETE FROM runs", "UPDATE blobs SET content=x'00'", "DELETE FROM blobs"]:
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                with self.store.connect() as db:
                    db.execute(statement)
        self.assertTrue(self.store.replay(run["id"])["matched"])

    def test_corruption_detected_even_if_triggers_bypassed(self):
        run = self.create()
        with self.store.connect() as db:
            db.execute("DROP TRIGGER immutable_blobs_update")
            db.execute("UPDATE blobs SET content=? WHERE sha=?", (b"corrupt", run["artifacts"]["data"]))
        with self.assertRaisesRegex(IntegrityError, "corrupted artifact"):
            self.store.replay(run["id"])

    def test_environment_mismatch_blocks_replay(self):
        run = self.create()
        with patch("app.store.environment", return_value={"python": "changed"}):
            with self.assertRaisesRegex(IntegrityError, "environment has changed"):
                self.store.replay(run["id"])

    def test_arbitrary_model_source_is_not_executed(self):
        with self.assertRaisesRegex(IntegrityError, "reviewed"):
            execute(b"raise RuntimeError('not allowed')", self.rows, {"lookback": 20, "horizon": 5})

    def test_invalid_observations_rejected(self):
        cases = [self.rows[:2], self.rows[::-1], [self.rows[0]] * 4]
        for value in ["NaN", "Infinity", "1e999999999", "1e-999999999", "not-a-number", "101"]:
            rows = copy.deepcopy(self.rows)
            rows[0]["value"] = value
            cases.append(rows)
        for rows in cases:
            with self.subTest(rows=repr(rows)[:80]), self.assertRaises(ResearchError):
                self.create(rows=rows)

    def test_invalid_config_rejected(self):
        for config in [{"lookback": True, "horizon": 5}, {"lookback": 20, "horizon": 0}, {"lookback": 20.5, "horizon": 5}, {"lookback": 200, "horizon": 5}, {"lookback": 20, "horizon": 5, "extra": 1}]:
            with self.subTest(config=config), self.assertRaises(ResearchError):
                self.create(config=config)

    def test_normalisation_ignores_decimal_formatting(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["value"] += "00"
        self.assertEqual(normalise_rows(rows), normalise_rows(self.rows))

    def test_seed_is_idempotent_and_all_runs_replay(self):
        seed(self.store)
        seed(self.store)
        self.assertEqual(len(self.store.list()), 4)
        for run in self.store.list():
            self.assertTrue(self.store.replay(run["id"])["matched"])


if __name__ == "__main__":
    unittest.main()
