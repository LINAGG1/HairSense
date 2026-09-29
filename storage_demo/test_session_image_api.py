"""Session image isolation and binary HTTP response checks; no external DB."""
import io
import unittest
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from sensor_pipeline_api import make_router


class SessionImageTests(unittest.TestCase):
    def setUp(self):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buffer, format="PNG")
        self.image = buffer.getvalue()
        self.key = ("device-1", "session-1", "boot-1")
        self.has_image = True
        owner = self

        class Connection:
            def cursor(self, **kwargs): return self
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, args): self.sql, self.key = sql, args
            def fetchone(self):
                if self.key != owner.key:
                    return None
                if "FROM sensor_sessions" in self.sql:
                    return {"user_id": "user-1"}
                if "FROM sensor_capture_images" in self.sql:
                    return {"image_bytes": owner.image, "image_mime": "image/png"} if owner.has_image else None
                if "FROM sensor_analysis_runs" in self.sql:
                    return {"result_json": {"status": "awaiting_real_baseline"}}
                raise AssertionError(self.sql)

        @contextmanager
        def database(): yield Connection()

        app = FastAPI()
        app.include_router(make_router(database))
        self.client = TestClient(app)
        self.params = dict(device_id="device-1", boot_id="boot-1", user_id="user-1")

    def test_analysis_and_image_same_session(self):
        self.assertEqual(self.client.get("/sensor-sessions/session-1/analysis", params=self.params).status_code, 200)
        response = self.client.get("/sensor-sessions/session-1/image", params=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.image)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_wrong_session_device_boot_or_user_never_returns_image(self):
        cases = [("other-session", self.params)] + [
            ("session-1", {**self.params, name: "other"}) for name in self.params
        ]
        for session, params in cases:
            with self.subTest(session=session, params=params):
                response = self.client.get(f"/sensor-sessions/{session}/image", params=params)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"], "session_not_found")

    def test_missing_image(self):
        self.has_image = False
        response = self.client.get("/sensor-sessions/session-1/image", params=self.params)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "image_not_found")

    def test_identifiers_required(self):
        self.assertEqual(self.client.get("/sensor-sessions/session-1/image").status_code, 422)


if __name__ == "__main__":
    unittest.main()
