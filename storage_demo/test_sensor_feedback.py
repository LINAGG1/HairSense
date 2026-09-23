"""외부 호출 없이 Gemini 요청/검증/실패 처리와 POST 경로를 검사합니다."""

import asyncio
import copy
import json
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi import FastAPI
import sensor_analysis_api as api
import sensor_feedback as service
from test_sensor_analysis_api import asgi_get


def session_fixture():
    optical = {
        "status": "summary_only", "session_verdict": None, "expected_windows": 30,
        "valid_windows": 30, "valid_duration_s": 60, "flagged_windows": 5,
        "flagged_duration_s": 10, "flagged_duration_fraction": 1/6,
        "flagged_duration_percent": 16.67, "longest_flagged_run_s": 10,
        "rule_reason_windows": {"optical_mean_increased": 5},
    }
    gyro = {**optical, "flagged_windows": 0, "flagged_duration_s": 0,
            "flagged_duration_fraction": 0, "flagged_duration_percent": 0,
            "longest_flagged_run_s": 0, "rule_reason_windows": {}}
    return {"session_id": "private-session", "boot_id": "private-boot",
            "user_id": "private-user", "device_id": "private-device", "is_synthetic": True,
            "sensors": {"optical": optical, "gyro": gyro}}


class SensorFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.session = session_fixture()
        self.env = patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": service.DEFAULT_MODEL})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network_patch = patch.object(service.requests, "post", side_effect=AssertionError("Unmocked network call"))
        self.network = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

    def mock_response(self, raw=None, status=200, finish="STOP"):
        if raw is None:
            raw = service.fallback_messages(service.build_evidence(self.session)).model_dump()
        response = Mock(status_code=status)
        response.json.return_value = {"candidates": [{"finishReason": finish,
            "content": {"parts": [{"text": json.dumps(raw, ensure_ascii=False)}]}}]}
        self.network.side_effect = None
        self.network.return_value = response
        return response

    def test_success_and_minimal_payload(self):
        before = copy.deepcopy(self.session)
        response = self.mock_response()
        result = service.generate_feedback(self.session)
        self.assertEqual(result.source, "gemini")
        self.assertIsNone(result.fallback_reason)
        url = self.network.call_args.args[0]
        kwargs = self.network.call_args.kwargs
        self.assertNotIn("test-key", url)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-key")
        body = json.dumps(kwargs["json"])
        for secret in ("private-session", "private-boot", "private-user", "private-device", "test-key"):
            self.assertNotIn(secret, body)
        self.assertEqual(kwargs["timeout"], (5, 25))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(self.session, before)
        response.close.assert_called_once()

    def test_missing_key_makes_no_request(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            result = service.generate_feedback(self.session)
        self.assertEqual(result.fallback_reason, "missing_api_key")
        self.network.assert_not_called()

    def test_invalid_model_makes_no_request(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "https://bad.example"}):
            result = service.generate_feedback(self.session)
        self.assertEqual(result.fallback_reason, "invalid_model_configuration")
        self.network.assert_not_called()

    def test_timeout_and_network_failure(self):
        for error, reason in [(requests.Timeout("secret"), "provider_timeout"),
                              (requests.ConnectionError("secret"), "provider_unavailable")]:
            self.network.side_effect = error
            result = service.generate_feedback(self.session)
            self.assertEqual(result.fallback_reason, reason)
            self.assertNotIn("secret", result.model_dump_json())

    def test_provider_failures(self):
        for status, reason in [(403, "provider_auth_error"), (429, "provider_rate_limited"),
                               (400, "provider_invalid_request"), (404, "provider_model_unavailable"),
                               (500, "provider_request_rejected")]:
            response = self.mock_response(status=status)
            response.text = "test-key sensitive upstream message"
            result = service.generate_feedback(self.session)
            self.assertEqual(result.source, "fallback")
            self.assertEqual(result.fallback_reason, reason)
            self.assertEqual(result.provider_http_status, status)
            self.assertNotIn("test-key", result.model_dump_json())
            response.json.assert_called_once()

    def test_provider_error_codes_without_sensitive_details(self):
        response = self.mock_response(status=400)
        response.json.return_value = {"error": {
            "status": "INVALID_ARGUMENT", "message": "API key test-key is invalid",
            "details": [{"reason": "API_KEY_INVALID", "metadata": {"key": "test-key"}}],
        }}
        result = service.generate_feedback(self.session)
        self.assertEqual(result.fallback_reason, "provider_auth_error")
        self.assertEqual(result.provider_http_status, 400)
        self.assertEqual(result.provider_error_status, "INVALID_ARGUMENT")
        self.assertEqual(result.provider_error_reason, "API_KEY_INVALID")
        self.assertNotIn("test-key", result.model_dump_json())

    def test_unrecognized_error_fields_not_exposed(self):
        response = self.mock_response(status=404)
        response.json.return_value = {"error": {
            "status": "test-key", "message": "test-key",
            "details": [{"reason": "test-key"}],
        }}
        result = service.generate_feedback(self.session)
        self.assertEqual(result.fallback_reason, "provider_model_unavailable")
        self.assertIsNone(result.provider_error_status)
        self.assertIsNone(result.provider_error_reason)
        self.assertNotIn("test-key", result.model_dump_json())

    def test_non_json_provider_error_keeps_http_status(self):
        response = self.mock_response(status=500)
        response.json.side_effect = ValueError("not JSON")
        result = service.generate_feedback(self.session)
        self.assertEqual(result.provider_http_status, 500)
        self.assertEqual(result.fallback_reason, "provider_request_rejected")

    def test_incomplete_or_malformed_response(self):
        self.mock_response(finish="MAX_TOKENS")
        self.assertEqual(service.generate_feedback(self.session).fallback_reason, "provider_response_incomplete")
        response = self.mock_response()
        response.json.return_value = {"candidates": []}
        self.assertEqual(service.generate_feedback(self.session).fallback_reason, "output_validation_failed")

    def test_reversed_facts_numbers_and_medical_claims_rejected(self):
        original = service.fallback_messages(service.build_evidence(self.session)).model_dump()
        bad = copy.deepcopy(original)
        bad["optical"]["finding"] = "optical_low"
        self.mock_response(raw=bad)
        self.assertEqual(service.generate_feedback(self.session).source, "fallback")
        for message in [service.FACTS["optical_low"],
                        service.FACTS["optical_high"] + " 90% 확률이에요.",
                        service.FACTS["optical_high"] + " 탈모 치료를 받으세요."]:
            bad = copy.deepcopy(original)
            bad["optical"]["message"] = message
            self.mock_response(raw=bad)
            self.assertEqual(service.generate_feedback(self.session).fallback_reason, "output_validation_failed")

    def test_low_mixed_rapid_and_no_flag_findings(self):
        cases = [("optical", {"optical_mean_decreased": 5}, "optical_low"),
                 ("optical", {"optical_mean_decreased": 2, "optical_mean_increased": 3}, "optical_mixed"),
                 ("gyro", {"gyro_rapid_changes_increased": 5}, "gyro_rapid")]
        for sensor, reasons, finding in cases:
            session = session_fixture()
            session["sensors"][sensor]["flagged_windows"] = 5
            session["sensors"][sensor]["rule_reason_windows"] = reasons
            self.assertEqual(service.build_evidence(session)["sensors"][sensor]["finding"], finding)
        self.assertEqual(service.build_evidence(self.session)["sensors"]["gyro"]["finding"], "gyro_unflagged")

    def test_post_route_fallback_and_get_does_not_call_gemini(self):
        document = api.SummaryDocument.model_validate({
            "summary_version": "session_summary_demo_1", "is_synthetic": True,
            "rule_policy_sha256": "0" * 64, "limitations": ["Synthetic only"], "sessions": [self.session],
        })
        app = FastAPI()
        app.include_router(api.router)
        async def post_app(scope, receive, send):
            await app({**scope, "method": "POST"}, receive, send)
        with patch.object(api, "load_document", return_value=document), patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            status, _ = asyncio.run(asgi_get(app, "/sensor-analysis/sessions/private-session"))
            self.assertEqual(status, 200)
            status, body = asyncio.run(asgi_get(post_app, "/sensor-analysis/sessions/private-session/feedback"))
            self.assertEqual(status, 200)
            self.assertEqual(body["feedback"]["source"], "fallback")
            self.assertEqual(body["feedback"]["fallback_reason"], "missing_api_key")
            status, _ = asyncio.run(asgi_get(post_app, "/sensor-analysis/sessions/missing/feedback"))
            self.assertEqual(status, 404)
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
