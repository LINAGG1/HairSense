"""Real frozen-model inference plus mocked DB/provider lifecycle checks."""
import copy
import csv
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

import sensor_pipeline as pipeline
import sensor_pipeline_api as api
from sensor_feedback import fallback_messages, build_evidence, FeedbackResult


class MemoryConnection:
    """Narrow SQL test double, not a substitute for MySQL integration testing."""
    def __init__(self, rows):
        self.rows, self.run, self.commits = rows, None, 0

    def cursor(self, **kwargs):
        owner = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, args=()):
                self.sql = sql
                if sql.startswith("INSERT INTO sensor_analysis_runs"):
                    owner.run = json.loads(args[-1])
                if sql.startswith("UPDATE sensor_analysis_runs"):
                    owner.run = json.loads(args[1])
            def fetchall(self): return owner.rows
            def fetchone(self):
                if "GET_LOCK" in self.sql or "RELEASE_LOCK" in self.sql: return (1,)
                if "SELECT result_json" in self.sql:
                    return {"result_json": json.dumps(owner.run)} if owner.run else None
                if "SELECT user_id" in self.sql: return {"user_id": "mock-multi-user-001"}
                raise AssertionError(self.sql)
        return Cursor()

    def commit(self): self.commits += 1


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(__file__).resolve().parent.parent / "mock_sessions/holdout_002/test/multi-session-102"
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        cls.request = api.Completion(**{k: metadata[k] for k in api.Completion.model_fields})
        cls.meta = {**cls.request.model_dump(), "session_id": metadata["session_id"]}
        context = {k: cls.meta[k] for k in ("user_id", "device_id", "session_id", "is_synthetic")}
        cls.rows = [{**context, **json.loads(line)} for line in (folder / "sensor_mock.jsonl").read_text().splitlines()]
        cls.bundles, cls.provenance = pipeline.load_models(cls.meta)
        cls.result = pipeline.analyze_rows(cls.rows, cls.meta, cls.bundles, cls.provenance)

    def test_reproduces_file_summary_and_scores(self):
        old = json.loads((pipeline.SOURCE / "session_summaries.json").read_text(encoding="utf-8"))
        expected = next(s for s in old["sessions"] if s["session_id"] == self.meta["session_id"])
        self.assertEqual(self.result["summary"], expected)
        self.assertEqual(len(self.result["features"]), 30)
        self.assertEqual(len(self.result["scores"]), 60)
        self.assertNotIn("expected_anomaly", self.result["scores"][0])
        with (pipeline.SOURCE / "holdout_results.csv").open(encoding="utf-8-sig", newline="") as source:
            saved_scores = {(r["sensor"], int(r["window_id"])): r for r in csv.DictReader(source)
                            if r["session_id"] == self.meta["session_id"]}
        for row in self.result["scores"]:
            reference = saved_scores[(row["sensor"], row["window_id"])]
            self.assertEqual(row["anomaly_score"], float(reference["anomaly_score"]))
            self.assertEqual(row["combined_flag"], reference["combined_flag"] == "True")

    def test_missing_and_null_data_not_summarized(self):
        for rows in (self.rows[:-1], [{**self.rows[0], "optical": None}, *self.rows[1:]]):
            result = pipeline.analyze_rows(rows, self.meta, self.bundles, self.provenance)
            self.assertEqual(result["status"], "insufficient_data")
            self.assertIsNone(result["summary"])

    def test_owner_and_baseline_mismatch(self):
        with self.assertRaises(ValueError):
            pipeline.analyze_rows([{**self.rows[0], "user_id": "wrong"}], self.meta, self.bundles, self.provenance)
        with self.assertRaises(ValueError):
            pipeline.load_models({**self.meta, "user_id": "wrong"})
        with self.assertRaises(ValueError):
            pipeline.load_models({**self.meta, "session_id": "multi-session-001"})

    def endpoints(self, connection):
        @contextmanager
        def database(): yield connection
        router = api.make_router(database)
        return router.routes[0].endpoint, router.routes[1].endpoint

    def feedback(self):
        return FeedbackResult(source="fallback", fallback_reason="missing_api_key", model=None,
                              messages=fallback_messages(build_evidence(self.result["summary"])))

    def test_complete_repeat_and_get_only_call_provider_once(self):
        connection = MemoryConnection(self.rows)
        complete, get = self.endpoints(connection)
        session = {"status": "receiving", "metadata_json": None}
        with patch.object(api, "register_session", return_value=session), \
                patch.object(api, "load_models", return_value=(self.bundles, self.provenance)), \
                patch.object(api, "analyze_rows", return_value=copy.deepcopy(self.result)) as analyze, \
                patch.object(api, "generate_feedback", return_value=self.feedback()) as feedback:
            first = complete("multi-session-102", self.request)
            second = complete("multi-session-102", self.request)
            third = get("multi-session-102", self.request.device_id, self.request.boot_id, self.request.user_id)
        self.assertEqual(first["status"], "completed")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["feedback"], third["feedback"])
        analyze.assert_called_once()
        feedback.assert_called_once()

    def test_pending_run_never_retries_provider(self):
        connection = MemoryConnection(self.rows)
        connection.run = {"status": "feedback_pending"}
        complete, _ = self.endpoints(connection)
        with patch.object(api, "register_session", return_value={"status": "sealed", "metadata_json": self.meta}), \
                patch.object(api, "generate_feedback") as feedback:
            with self.assertRaises(HTTPException) as error:
                complete("multi-session-102", self.request)
        self.assertEqual(error.exception.status_code, 409)
        feedback.assert_not_called()

    def test_metadata_conflict(self):
        connection = MemoryConnection(self.rows)
        with connection.cursor() as cursor, self.assertRaises(HTTPException):
            api.replay_result(cursor, ("d", "s", "b"), {"status": "sealed", "metadata_json": {**self.meta, "start_timestamp_ms": 2000}}, self.meta)

    def test_sealed_session_rejects_batches(self):
        with patch.object(api, "register_session", return_value={"status": "sealed"}):
            with self.assertRaises(HTTPException) as error:
                api.require_receiving(MemoryConnection([]), ("d", "s", "b"), "u")
        self.assertEqual(error.exception.status_code, 409)

    def test_busy_session_lock_rejected(self):
        connection = Mock()
        cursor = Mock()
        cursor.fetchone.return_value = (0,)
        context = Mock()
        context.__enter__ = Mock(return_value=cursor)
        context.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = context
        with self.assertRaises(HTTPException) as error:
            with api.session_lock(connection, ("d", "s", "b")):
                self.fail("Busy lock was acquired")
        self.assertEqual(error.exception.status_code, 409)

    def test_quality_failure_does_not_call_provider(self):
        result = {"status": "insufficient_data", "feedback": None}
        with patch.object(api, "analyze_rows", return_value=result), patch.object(api, "generate_feedback") as feedback:
            output = api.finish_analysis(MemoryConnection([]), ("d", "s", "b"), [], self.meta, {}, {})
        self.assertEqual(output["status"], "insufficient_data")
        feedback.assert_not_called()

    def test_provider_exception_is_persisted_without_secret(self):
        connection = MemoryConnection([])
        with patch.object(api, "analyze_rows", return_value=copy.deepcopy(self.result)), \
                patch.object(api, "generate_feedback", side_effect=RuntimeError("secret-key")):
            output = api.finish_analysis(connection, ("d", "s", "b"), [], self.meta, {}, {})
        self.assertEqual(output["status"], "feedback_failed")
        self.assertNotIn("secret-key", str(connection.run))

    def retry_fixture(self):
        connection = MemoryConnection(self.rows)
        connection.run = {**copy.deepcopy(self.result), "status": "completed", "feedback": self.feedback().model_dump()}
        @contextmanager
        def database(): yield connection
        endpoint = api.make_router(database).routes[2].endpoint
        request = api.FeedbackRetry(user_id=self.request.user_id, device_id=self.request.device_id,
                                    boot_id=self.request.boot_id, retry_id="retry-001")
        return connection, endpoint, request

    def test_retry_success_persists_only_feedback_and_replay_is_cached(self):
        connection, endpoint, request = self.retry_fixture()
        before = copy.deepcopy(connection.run)
        gemini = self.feedback().model_copy(update={"source": "gemini", "fallback_reason": None})
        with patch.object(api, "generate_feedback", return_value=gemini) as generate, \
                patch.object(api, "analyze_rows", side_effect=AssertionError("Do not reanalyze")), \
                patch.object(api, "load_models", side_effect=AssertionError("Do not reload models")):
            first = endpoint("multi-session-102", request)
            second = endpoint("multi-session-102", request)
            third = endpoint("multi-session-102", request.model_copy(update={"retry_id": "retry-002"}))
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(third["status"], "already_gemini")
        generate.assert_called_once_with(before["summary"])
        self.assertEqual(connection.run["feedback"]["source"], "gemini")
        for field in before.keys() - {"feedback"}:
            self.assertEqual(connection.run[field], before[field])
        self.assertEqual(connection.run["feedback_retries"]["retry-001"]["previous_feedback"], before["feedback"])

    def test_failed_retry_is_cached_and_new_id_allows_explicit_attempt(self):
        connection, endpoint, request = self.retry_fixture()
        with patch.object(api, "generate_feedback", return_value=self.feedback()) as generate:
            endpoint("multi-session-102", request)
            repeat = endpoint("multi-session-102", request)
            endpoint("multi-session-102", request.model_copy(update={"retry_id": "retry-002"}))
        self.assertTrue(repeat["cached"])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(len(connection.run["feedback_retries"]), 2)

    def test_retry_pending_saved_before_call_and_blocks_reentry(self):
        connection, endpoint, request = self.retry_fixture()
        def check_pending(summary):
            self.assertEqual(connection.run["status"], "feedback_retry_pending")
            self.assertEqual(connection.run["feedback_retries"]["retry-001"]["status"], "pending")
            self.assertGreater(connection.commits, 0)
            raise KeyboardInterrupt()  # Simulate termination after request dispatch.
        with patch.object(api, "generate_feedback", side_effect=check_pending):
            with self.assertRaises(KeyboardInterrupt):
                endpoint("multi-session-102", request)
        with patch.object(api, "generate_feedback") as generate:
            for retry_id in ("retry-001", "retry-002"):
                with self.assertRaises(HTTPException) as error:
                    endpoint("multi-session-102", request.model_copy(update={"retry_id": retry_id}))
                self.assertEqual(error.exception.status_code, 409)
        generate.assert_not_called()

    def test_retry_exception_keeps_previous_feedback_and_is_not_repeated(self):
        connection, endpoint, request = self.retry_fixture()
        previous = copy.deepcopy(connection.run["feedback"])
        with patch.object(api, "generate_feedback", side_effect=RuntimeError("secret-key")) as generate:
            first = endpoint("multi-session-102", request)
            second = endpoint("multi-session-102", request)
        self.assertEqual(first["status"], "failed")
        self.assertTrue(second["cached"])
        self.assertEqual(connection.run["feedback"], previous)
        self.assertNotIn("secret-key", str(connection.run))
        generate.assert_called_once()

    def test_retry_wrong_owner_and_missing_summary_are_rejected(self):
        connection, endpoint, request = self.retry_fixture()
        with patch.object(api, "generate_feedback") as generate:
            with self.assertRaises(HTTPException) as error:
                endpoint("multi-session-102", request.model_copy(update={"user_id": "other"}))
            self.assertEqual(error.exception.status_code, 404)
            connection.run["summary"] = None
            with self.assertRaises(HTTPException) as error:
                endpoint("multi-session-102", request)
            self.assertEqual(error.exception.status_code, 409)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
