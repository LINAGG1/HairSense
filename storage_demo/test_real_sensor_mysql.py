"""Opt-in MySQL integration test; every generated capture is rolled back.

Run only after migration_real_baseline.sql, with HAIRSENSE_TEST_MYSQL=1.
No existing measurements/profiles are modified and no test data is committed.
"""
import os
import unittest
from contextlib import contextmanager
from uuid import uuid4

from app import Reading, database
from real_capture_storage import store_real_capture
from sensor_pipeline_api import decode
from test_analyze_real_sessions import fixture


@unittest.skipUnless(os.getenv("HAIRSENSE_TEST_MYSQL") == "1", "opt-in MySQL integration")
class MySQLSensorTests(unittest.TestCase):
    def test_real_schema_capture_replay_and_rollback(self):
        identifier = "verify-" + uuid4().hex
        with database() as raw:
            class Uncommitted:
                def cursor(self, **kwargs):
                    return raw.cursor(**kwargs)
                def commit(self):
                    pass
                def rollback(self):
                    # Replay intentionally calls rollback, but the verification
                    # transaction must keep its earlier uncommitted captures.
                    pass
            @contextmanager
            def isolated_database():
                yield Uncommitted()
            try:
                for sensor in ("optical", "gyro"):
                    for number in range(1, 5):
                        meta, rows = fixture(sensor, f"{sensor}-{number}")
                        meta.update(user_id=identifier, device_id=identifier)
                        result = store_real_capture(isolated_database, meta, rows, None, Reading)
                        self.assertEqual(result["sensor_analysis_status"], "baseline_created" if number == 1 else "completed")
                        replay = store_real_capture(isolated_database, meta, rows, None, Reading)
                        self.assertTrue(replay["cached"])
                        self.assertEqual(replay["sensor_analysis_status"], result["sensor_analysis_status"])
                    with raw.cursor(dictionary=True) as cursor:
                        cursor.execute("SELECT state_json FROM real_sensor_profiles WHERE user_id=%s AND device_id=%s AND sensor=%s",
                                       (identifier, identifier, sensor))
                        self.assertEqual(decode(cursor.fetchone()["state_json"])["baseline"]["reference_windows"], 8)
                        cursor.execute("SELECT result_json FROM real_sensor_events WHERE user_id=%s AND sensor=%s ORDER BY id",
                                       (identifier, sensor))
                        results = [decode(row["result_json"]) for row in cursor.fetchall()]
                        self.assertEqual(len(results), 4)
                        self.assertEqual(results[2]["compared_baseline"]["reference_windows"], 4)
            finally:
                raw.rollback()
        with database() as check, check.cursor() as cursor:
            for table in ("sensor_sessions", "sensor_readings", "sensor_analysis_runs", "real_sensor_profiles", "real_sensor_events"):
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE device_id=%s", (identifier,))
                self.assertEqual(cursor.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
