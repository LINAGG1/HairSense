"""Transaction and router regression tests against an isolated in-memory DB.

The adapter implements the SQL used by sensor persistence, not MySQL locking.
Run the optional MySQL check separately for actual DB permissions/schema.
"""
import copy
import asyncio
import json
import os
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import real_capture_storage as storage
from real_sensor_service import NextCapture, make_real_sensor_router, record_capture
from sensor_pipeline_api import register_session
from test_analyze_real_sessions import fixture


class Reading(BaseModel):
    schema_version: int
    boot_id: str
    seq: int
    timestamp_ms: int
    optical: float | None
    gyro_x: float | None
    gyro_y: float | None
    gyro_z: float | None


class Cursor:
    def __init__(self, db, dictionary=False):
        self.cursor = db.cursor()
        self.dictionary = dictionary

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace(" FOR UPDATE", "")
        sql = sql.replace("ON DUPLICATE KEY UPDATE user_id=user_id", "ON CONFLICT DO NOTHING")
        sql = sql.replace("ON DUPLICATE KEY UPDATE device_id=device_id", "ON CONFLICT DO NOTHING")
        return self.cursor.execute(sql, params)

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None and self.dictionary else row

    def fetchall(self):
        return [dict(row) if self.dictionary else row for row in self.cursor.fetchall()]


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE sensor_sessions (device_id TEXT, session_id TEXT, boot_id TEXT,
                user_id TEXT, status TEXT DEFAULT 'receiving', metadata_json TEXT,
                PRIMARY KEY(device_id,session_id,boot_id));
            CREATE TABLE sensor_readings (device_id TEXT, session_id TEXT, user_id TEXT,
                is_synthetic INTEGER, schema_version INTEGER, boot_id TEXT, seq INTEGER,
                timestamp_ms INTEGER, optical REAL, gyro_x REAL, gyro_y REAL, gyro_z REAL);
            CREATE TABLE sensor_analysis_runs (device_id TEXT, session_id TEXT, boot_id TEXT,
                pipeline_version TEXT, status TEXT, result_json TEXT,
                PRIMARY KEY(device_id,session_id,boot_id));
            CREATE TABLE real_sensor_profiles (user_id TEXT, device_id TEXT, sensor TEXT,
                state_json TEXT, next_role TEXT DEFAULT 'measurement',
                PRIMARY KEY(user_id,device_id,sensor));
            CREATE TABLE real_sensor_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, device_id TEXT, sensor TEXT, session_id TEXT, boot_id TEXT,
                result_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(device_id,session_id,boot_id));
        """)

    def cursor(self, dictionary=False):
        return Cursor(self.db, dictionary)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


@contextmanager
def no_lock(*args):
    yield


class SensorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        self.addCleanup(self.connection.db.close)
        @contextmanager
        def database():
            try:
                yield self.connection
            finally:
                self.connection.rollback()
        self.database = database
        self.client = RouterClient(make_real_sensor_router(database))
        self.binding = patch.dict(os.environ, HAIRSENSE_REAL_USER_ID="user", HAIRSENSE_REAL_DEVICE_ID="device")
        self.binding.start()
        self.addCleanup(self.binding.stop)
        self.lock = patch.object(storage, "session_lock", no_lock)
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.params = {"user_id": "user", "device_id": "device"}

    def capture(self, sensor, name, invalid=False):
        meta, rows = fixture(sensor, name)
        if invalid:
            rows = rows[:5]
        return storage.store_real_capture(self.database, meta, rows, None, Reading)

    def latest(self):
        response = self.client.get("/real-sensors/latest", params=self.params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_saved_results_replay_and_independent_sensor_history(self):
        for sensor in ("optical", "gyro"):
            self.capture(sensor, sensor+"1")
            self.capture(sensor, sensor+"2")
        before = self.latest()
        second = copy.deepcopy(before["captures"]["optical"])
        self.assertEqual(second["compared_baseline"]["reference_windows"], 2)
        self.assertTrue(self.capture("optical", "optical2")["cached"])
        self.assertEqual(self.latest(), before)
        self.capture("optical", "optical3")
        latest = self.latest()
        self.assertEqual(latest["captures"]["optical"]["compared_baseline"]["reference_windows"], 4)
        self.assertEqual(latest["captures"]["gyro"], before["captures"]["gyro"])
        stored = self.connection.db.execute("SELECT result_json FROM real_sensor_events WHERE session_id='optical2'").fetchone()[0]
        self.assertEqual(json.loads(stored), second)

    def test_rollback_retries_without_double_learning(self):
        self.capture("optical", "first")
        original = record_capture
        def fail_after_result(*args):
            original(*args)
            raise RuntimeError("simulated failure before commit")
        with patch.object(storage, "record_capture", fail_after_result), self.assertRaises(RuntimeError):
            self.capture("optical", "second")
        self.assertEqual(self.connection.db.execute("SELECT COUNT(*) FROM sensor_readings WHERE session_id='second'").fetchone()[0], 0)
        self.assertEqual(self.latest()["captures"]["optical"]["metadata"]["session_id"], "first")
        self.capture("optical", "second")
        self.assertEqual(self.latest()["captures"]["optical"]["compared_baseline"]["reference_windows"], 2)

    def test_historical_feedback_is_derived_without_rewriting_or_learning(self):
        self.capture("optical", "first")
        self.capture("optical", "second")
        record = self.latest()["captures"]["optical"]
        record["feedback"] = None
        for key in ("finding", "message", "rule_reason_windows"):
            record["summary"]["sensors"]["optical"].pop(key)
        self.connection.db.execute("UPDATE real_sensor_events SET result_json=? WHERE session_id='second'", (json.dumps(record),))
        self.connection.commit()
        before = self.connection.db.execute("SELECT state_json FROM real_sensor_profiles").fetchone()[0]
        latest = self.latest()
        self.assertTrue(latest["summary"]["sensors"]["optical"]["message"])
        self.assertEqual(latest["feedback"]["source"], "rules")
        saved = self.connection.db.execute("SELECT result_json FROM real_sensor_events WHERE session_id='second'").fetchone()[0]
        self.assertEqual(json.loads(saved), record)
        self.assertEqual(self.connection.db.execute("SELECT state_json FROM real_sensor_profiles").fetchone()[0], before)

    def test_calibration_api_invalid_capture_and_binding(self):
        url = "/real-sensors/optical/next-capture"
        body = {**self.params, "role": "calibration"}
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        self.capture("optical", "first")
        self.assertEqual(self.client.post(url, json=body).status_code, 200)
        self.capture("optical", "bad-calibration", invalid=True)
        profile = self.client.get("/real-sensors/baseline", params=self.params).json()["sensors"]["optical"]
        self.assertEqual(profile["next_role"], "calibration")
        self.capture("optical", "calibration")
        profile = self.client.get("/real-sensors/baseline", params=self.params).json()["sensors"]["optical"]
        self.assertEqual(profile["next_role"], "measurement")
        self.assertEqual(profile["baseline"]["reference_windows"], 2)
        self.assertEqual(profile["baseline"]["threshold_status"], "calibrated")
        self.assertEqual(self.client.get("/real-sensors/latest", params={**self.params, "user_id": "other"}).status_code, 404)

    def test_old_real_and_synthetic_records_never_backfilled(self):
        # Seed historical records without passing through the newly installed hook.
        for source in (False, True):
            meta, rows = fixture("optical", "historical-"+str(source))
            key = (meta["device_id"], meta["session_id"], meta["boot_id"])
            with self.connection.cursor(dictionary=True) as cursor:
                register_session(cursor, key, meta["user_id"], is_synthetic=source)
                cursor.execute("UPDATE sensor_sessions SET status='sealed',metadata_json=%s WHERE device_id=%s AND session_id=%s AND boot_id=%s",
                               (json.dumps({**meta, "is_synthetic": source}), *key))
            self.connection.commit()
        self.assertEqual(self.latest()["status"], "awaiting_first_capture")
        self.capture("optical", "first-real")
        result = self.latest()["captures"]["optical"]
        self.assertIsNone(result["compared_baseline"])

    def test_http_routes_and_invalid_request_contract(self):
        app = FastAPI()
        app.include_router(make_real_sensor_router(self.database))
        async def request(method, path, query=b"", body=None):
            messages = []
            async def receive():
                return {"type": "http.request", "body": json.dumps(body).encode() if body else b"", "more_body": False}
            async def send(message):
                messages.append(message)
            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                "query_string": query, "root_path": "", "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 1), "server": ("testserver", 80)}, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            content = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
            return status, content
        status, body = asyncio.run(request("GET", "/real-sensors/latest", b"user_id=user&device_id=device"))
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "awaiting_first_capture")
        status, _ = asyncio.run(request("POST", "/real-sensors/not-a-sensor/next-capture",
                                      body={**self.params, "role": "calibration"}))
        self.assertEqual(status, 422)
        status, _ = asyncio.run(request("POST", "/real-sensors/gyro/next-capture",
                                      body={**self.params, "role": "not-a-role"}))
        self.assertEqual(status, 422)


class RouterClient:
    """Exercise actual route handlers without optional HTTP test dependencies."""
    def __init__(self, app):
        self.routes = {r.path: r.endpoint for r in app.routes}

    def get(self, path, params):
        return self.call(self.routes[path], **params)

    def post(self, path, json):
        return self.call(self.routes["/real-sensors/{sensor}/next-capture"],
                         sensor=path.split("/")[2], request=NextCapture(**json))

    @staticmethod
    def call(endpoint, **kwargs):
        from types import SimpleNamespace
        try:
            payload = endpoint(**kwargs)
            return SimpleNamespace(status_code=200, json=lambda: payload)
        except HTTPException as exc:
            return SimpleNamespace(status_code=exc.status_code)


if __name__ == "__main__":
    unittest.main()
