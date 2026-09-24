"""Real capture persistence checks; no real user data or external calls."""
import copy
import asyncio
import json
import os
import io
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from fastapi import HTTPException
from PIL import Image

from app import Reading
import app as api
import real_capture_storage as storage


class CaptureStorageTests(unittest.TestCase):
    def setUp(self):
        self.meta = dict(device_id="real-device", session_id="real-session", boot_id="real-boot",
                         user_id="real-user", is_synthetic=False, schema_version=1,
                         optical_unit="V", sample_rate_hz=50, start_timestamp_ms=1200, end_timestamp_ms=1240)
        self.rows = [dict(schema_version=1, boot_id="real-boot", seq=55+i, timestamp_ms=1200+i*20,
                          optical=value, gyro_x=None, gyro_y=None, gyro_z=None) for i, value in enumerate((0.0, None))]
        buffer = io.BytesIO()
        with Image.new("RGB", (8, 8)) as image:
            image.save(buffer, format="PNG")
        self.image = buffer.getvalue()

    def test_accepts_real_variable_length_nonzero_sequence_and_missing_gyro(self):
        metadata, rows, mime, _, _ = storage.validate_capture(self.meta, self.rows, self.image, Reading)
        self.assertFalse(metadata["is_synthetic"])
        self.assertEqual(rows[0]["seq"], 55)
        self.assertEqual(rows[0]["optical"], 0.0)
        self.assertIsNone(rows[1]["optical"])
        self.assertEqual(mime, "image/png")

    def test_bad_capture_rejected(self):
        cases = [({**self.meta, "is_synthetic": True}, self.rows, self.image),
                 (self.meta, self.rows, b"not an image"),
                 (self.meta, [self.rows[0], self.rows[0]], self.image),
                 (self.meta, [{**self.rows[0], "boot_id": "other"}], self.image),
                 (self.meta, [{**self.rows[0], "timestamp_ms": 1240}], self.image),
                 (self.meta, [{**self.rows[0], "optical": float("nan")}], self.image)]
        for meta, rows, image in cases:
            with self.subTest(meta=meta, rows=rows), self.assertRaises(ValueError):
                storage.validate_capture(meta, rows, image, Reading)

    def test_hash_changes_when_either_sensor_or_image_changes(self):
        original = storage.validate_capture(self.meta, self.rows, self.image, Reading)[-1]
        changed = copy.deepcopy(self.rows)
        changed[0]["optical"] = 0.1
        self.assertNotEqual(original, storage.validate_capture(self.meta, changed, self.image, Reading)[-1])
        self.assertEqual(original, storage.validate_capture(self.meta, list(reversed(self.rows)), self.image, Reading)[-1])

    def connection(self, replies):
        cursor = Mock()
        cursor.fetchone.side_effect = replies
        connection = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=cursor)
        context.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = context
        @contextmanager
        def database(): yield connection
        @contextmanager
        def lock(*args): yield
        return connection, cursor, database, lock

    def test_sensor_and_image_saved_in_one_commit(self):
        connection, cursor, database, lock = self.connection([None, {"row_count": 0}])
        with patch.object(storage, "session_lock", lock), patch.object(storage, "register_session", return_value={"status": "receiving"}) as register:
            result = storage.store_real_capture(database, self.meta, self.rows, self.image, Reading)
        register.assert_called_once_with(cursor, ("real-device", "real-session", "real-boot"), "real-user", is_synthetic=False)
        connection.commit.assert_called_once()
        inserts = [c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT INTO sensor_readings")]
        self.assertEqual(len(inserts), 2)
        self.assertIs(inserts[0].args[1][3], False)
        blob = next(c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT INTO sensor_capture_images"))
        self.assertEqual(blob.args[1][-1], self.image)
        self.assertFalse(result["cached"])

    def test_database_failure_rolls_back_entire_bundle(self):
        connection, cursor, database, lock = self.connection([None, {"row_count": 0}])
        def execute(sql, args):
            if sql.startswith("INSERT INTO sensor_capture_images"):
                raise RuntimeError("DB write failed")
        cursor.execute.side_effect = execute
        with patch.object(storage, "session_lock", lock), patch.object(storage, "register_session", return_value={"status": "receiving"}):
            with self.assertRaises(RuntimeError):
                storage.store_real_capture(database, self.meta, self.rows, self.image, Reading)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_replay_and_conflicting_payload(self):
        payload = storage.validate_capture(self.meta, self.rows, self.image, Reading)[-1]
        for digest, should_conflict in ((payload, False), ("other", True)):
            connection, cursor, database, lock = self.connection([dict(payload_sha256=digest, image_status="pending", image_result_json=None)])
            with patch.object(storage, "session_lock", lock), patch.object(storage, "register_session", return_value={"status": "sealed"}):
                if should_conflict:
                    with self.assertRaises(HTTPException) as error:
                        storage.store_real_capture(database, self.meta, self.rows, self.image, Reading)
                    self.assertEqual(error.exception.status_code, 409)
                else:
                    self.assertTrue(storage.store_real_capture(database, self.meta, self.rows, self.image, Reading)["cached"])
            connection.commit.assert_not_called()

    def test_image_inference_gets_image_only_and_failure_keeps_raw_data(self):
        for failure in (False, True):
            connection, cursor, database, lock = self.connection([{"user_id": "real-user"},
                {"image_bytes": self.image, "image_status": "pending", "image_result_json": None}])
            def infer(image):
                self.assertIsInstance(image, Image.Image)
                self.assertEqual(image.mode, "RGB")
                if failure: raise RuntimeError("failure")
                return {"results": []}
            with patch.object(storage, "session_lock", lock):
                result = storage.analyze_stored_image(database, "real-device", "real-session", "real-boot", "real-user", infer)
            self.assertEqual(result["image_status"], "failed" if failure else "completed")
            self.assertFalse(any("DELETE" in c.args[0] for c in cursor.execute.call_args_list))

    def wire(self, optical=True):
        common = dict(schema_version=1, sample_type="camera_optical" if optical else "gyro",
                      boot_id="3AC6BDB3", seq=2, timestamp_ms=18320)
        if optical:
            return storage.OpticalCapture(**common, optical=[{"timestamp_ms": 1000, "value": 0.0},
                {"timestamp_ms": 1020, "value": None}], gyro_x=None, gyro_y=None, gyro_z=None)
        return storage.GyroCapture(**common, optical=None, gyro=[
            dict(timestamp_ms=15000, gyro_x=0.12, gyro_y=-0.31, gyro_z=1.07),
            dict(timestamp_ms=15020, gyro_x=None, gyro_y=-0.28, gyro_z=1.12)])

    def normalize(self, wire):
        with patch.dict(os.environ, HAIRSENSE_REAL_DEVICE_ID="device", HAIRSENSE_REAL_USER_ID="user"):
            return storage.normalize_wire(wire)

    def test_wire_preserves_envelope_and_distinguishes_sample_ordinal(self):
        for optical in (True, False):
            wire = self.wire(optical)
            meta, rows = self.normalize(wire)
            self.assertEqual(meta["wire_payload"], wire.model_dump())
            self.assertEqual(rows[0]["seq"], 1)
            self.assertEqual(meta["wire_payload"]["seq"], 2)
            self.assertFalse(meta["is_synthetic"])
            self.assertEqual(meta["session_id"], "real-optical-2" if optical else "real-gyro-2")
            analysis = storage.describe_real_rows(meta, rows)
            self.assertIsNone(analysis["anomaly_score"])
            self.assertIsNone(analysis["feedback"])
            self.assertEqual(analysis["measured_channels"], ["optical"] if optical else ["gyro_x", "gyro_y", "gyro_z"])
            self.assertFalse(analysis["windows"][0]["complete_regular_window"])

    def test_missing_identity_and_duplicate_timestamps_rejected(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(HTTPException):
            storage.normalize_wire(self.wire())
        wire = self.wire()
        wire.optical[1].timestamp_ms = 1000
        with self.assertRaises(ValueError):
            self.normalize(wire)

    def test_gyro_persistence_replay_conflict_and_analysis(self):
        meta, rows = self.normalize(self.wire(False))
        digest = storage.validate_capture(meta, rows, None, Reading)[-1]
        connection, cursor, database, lock = self.connection([{"row_count": 0}])
        with patch.object(storage, "session_lock", lock), patch.object(storage, "register_session", return_value={"status": "receiving"}):
            result = storage.store_real_capture(database, meta, rows, None, Reading)
        connection.commit.assert_called_once()
        self.assertEqual(result["image_status"], "not_present")
        self.assertFalse(any("sensor_capture_images" in c.args[0] for c in cursor.execute.call_args_list))
        analysis_insert = next(c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT INTO sensor_analysis_runs"))
        self.assertEqual(json.loads(analysis_insert.args[1][-1])["windows"][0]["features"]["gyro_x"]["null_count"], 1)
        for value in (digest, "different"):
            connection, cursor, database, lock = self.connection([])
            with patch.object(storage, "session_lock", lock), patch.object(storage, "register_session", return_value={
                    "status": "sealed", "metadata_json": {"payload_sha256": value}}):
                if value == digest:
                    self.assertTrue(storage.store_real_capture(database, meta, rows, None, Reading)["cached"])
                else:
                    with self.assertRaises(HTTPException) as error:
                        storage.store_real_capture(database, meta, rows, None, Reading)
                    self.assertEqual(error.exception.status_code, 409)
            connection.commit.assert_not_called()

    def test_quality_windows_and_zero_mean(self):
        meta, _ = self.normalize(self.wire())
        rows = [dict(timestamp_ms=1000+i*20, optical=float(i%2)*2) for i in range(101)]
        result = storage.describe_real_rows(meta, rows)
        self.assertTrue(result["windows"][0]["complete_regular_window"])
        self.assertEqual(result["windows"][0]["features"]["optical"]["mean"], 1)
        self.assertEqual(result["windows"][0]["features"]["optical"]["std"], 1)
        self.assertFalse(result["windows"][1]["complete_regular_window"])
        rows[50]["timestamp_ms"] += 1
        result = storage.describe_real_rows(meta, rows)
        self.assertFalse(result["windows"][0]["complete_regular_window"])
        self.assertEqual(result["non_20ms_intervals"], 2)

    async def post(self, path, body, content_type):
        messages = []
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        async def send(message):
            messages.append(message)
        await api.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [(b"content-type", content_type.encode())],
            "client": ("127.0.0.1", 1), "server": ("testserver", 80)}, receive, send)
        return next(m["status"] for m in messages if m["type"] == "http.response.start")

    def test_actual_multipart_and_json_routes(self):
        body = (b'--sample\r\nContent-Disposition: form-data; name="file"; filename="sample.jpg"\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + self.image +
                b'\r\n--sample\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n' +
                self.wire().model_dump_json().encode() + b'\r\n--sample--\r\n')
        with patch.object(api, "receive_real_capture", return_value={"image_result": {"results": []}}) as receiver:
            self.assertEqual(asyncio.run(self.post("/ai/analyze", body, "multipart/form-data; boundary=sample")), 200)
            self.assertIsInstance(receiver.call_args.args[0], storage.OpticalCapture)
            self.assertEqual(receiver.call_args.args[1], self.image)
            self.assertEqual(asyncio.run(self.post("/gyro", self.wire(False).model_dump_json().encode(), "application/json")), 200)
            self.assertIsInstance(receiver.call_args.args[0], storage.GyroCapture)
            bad = body.replace(b'"sample_type":"camera_optical"', b'"sample_type":"invalid"')
            self.assertEqual(asyncio.run(self.post("/ai/analyze", bad, "multipart/form-data; boundary=sample")), 422)

    def test_legacy_image_only_route_still_uses_original_predictor(self):
        body = (b'--sample\r\nContent-Disposition: form-data; name="file"; filename="sample.png"\r\n'
                b'Content-Type: image/png\r\n\r\n' + self.image + b'\r\n--sample--\r\n')
        with patch.object(api, "get_ai_model", return_value=("model", "cpu")), \
             patch.object(api, "predict", return_value=[]) as predictor, \
             patch.object(api.Path, "mkdir"), patch.object(api.Path, "write_bytes"), \
             patch.object(api, "receive_real_capture") as real:
            self.assertEqual(asyncio.run(self.post("/ai/analyze", body, "multipart/form-data; boundary=sample")), 200)
            predictor.assert_called_once()
            self.assertEqual(predictor.call_args.args[1:], ("model", "cpu"))
            real.assert_not_called()


if __name__ == "__main__":
    unittest.main()
