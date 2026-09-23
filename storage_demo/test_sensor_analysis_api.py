"""센서 요약 API 계약 검사. 추가 HTTP 클라이언트 설치 없이 ASGI로 호출합니다."""

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
import sensor_analysis_api as api


async def asgi_get(app, path):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    }, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class SensorAnalysisAPITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        self.summary_path = folder / "summary.json"
        self.policy_path = folder / "policy.json"
        self.policy_path.write_text("{}", encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(api, "SUMMARY_PATH", self.summary_path).start()
        patch.object(api, "POLICY_PATH", self.policy_path).start()
        sensor = {
            "status": "summary_only", "session_verdict": None,
            "expected_windows": 30, "valid_windows": 30, "valid_duration_s": 60,
            "flagged_windows": 5, "flagged_duration_s": 10,
            "flagged_duration_fraction": 1/6, "flagged_duration_percent": 16.67,
            "longest_flagged_run_s": 10, "rule_reason_windows": {"optical_mean_increased": 5},
        }
        self.document = {
            "summary_version": "session_summary_demo_1", "is_synthetic": True,
            "rule_policy_sha256": hashlib.sha256(self.policy_path.read_bytes()).hexdigest(),
            "limitations": ["Synthetic preview only"],
            "sessions": [{"session_id": "demo-001", "boot_id": "boot-001",
                          "user_id": "user-001", "device_id": "device-001", "is_synthetic": True,
                          "sensors": {"optical": copy.deepcopy(sensor), "gyro": copy.deepcopy(sensor)}}],
        }
        self.save()
        self.app = FastAPI()
        self.app.include_router(api.router)

    def save(self):
        self.summary_path.write_text(json.dumps(self.document), encoding="utf-8")

    def get(self, suffix):
        return asyncio.run(asgi_get(self.app, "/sensor-analysis/sessions" + suffix))

    def test_list_and_detail_preserve_summary(self):
        status, body = self.get("")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["mode"], "synthetic_file_preview")
        self.assertNotIn("sensors", body["items"][0])
        status, body = self.get("/demo-001")
        self.assertEqual(status, 200)
        self.assertEqual(body["session"], self.document["sessions"][0])
        self.assertEqual(body["limitations"], self.document["limitations"])

    def test_unknown_session(self):
        status, body = self.get("/missing")
        self.assertEqual(status, 404)
        self.assertEqual(body["detail"]["code"], "session_not_found")

    def test_invalid_identifier(self):
        self.assertEqual(self.get("/bad.id")[0], 422)

    def test_missing_file(self):
        self.summary_path.unlink()
        status, body = self.get("")
        self.assertEqual(status, 503)
        self.assertEqual(body["detail"]["code"], "summary_not_ready")

    def test_invalid_json_and_nonfinite(self):
        for contents in ("{", '{"unexpected": NaN}'):
            self.summary_path.write_text(contents, encoding="utf-8")
            status, body = self.get("")
            self.assertEqual(status, 503)
            self.assertEqual(body["detail"]["code"], "summary_invalid")

    def test_stale_policy(self):
        self.policy_path.write_text('{"changed":true}', encoding="utf-8")
        self.assertEqual(self.get("")[0], 503)

    def test_real_data_duplicate_sessions_and_bad_counts_rejected(self):
        original = copy.deepcopy(self.document)
        self.document["sessions"][0]["is_synthetic"] = False
        self.save()
        self.assertEqual(self.get("")[0], 503)
        self.document = copy.deepcopy(original)
        self.document["sessions"].append(copy.deepcopy(self.document["sessions"][0]))
        self.save()
        self.assertEqual(self.get("")[0], 503)
        self.document = copy.deepcopy(original)
        self.document["sessions"][0]["sensors"]["optical"]["flagged_windows"] = 31
        self.save()
        self.assertEqual(self.get("")[0], 503)

    def test_file_refresh_without_restart(self):
        self.assertEqual(self.get("/demo-001")[0], 200)
        self.document["sessions"][0]["session_id"] = "demo-002"
        self.save()
        self.assertEqual(self.get("/demo-001")[0], 404)
        self.assertEqual(self.get("/demo-002")[0], 200)


if __name__ == "__main__":
    unittest.main()
