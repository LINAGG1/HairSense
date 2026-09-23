"""Diagnostics must not expose credentials or raw non-JSON responses."""

import unittest
import json
from unittest.mock import Mock, patch

import diagnose_gemini as diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_model_lookup_uses_get_only_and_no_sensor_files(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.json.return_value = {"name": "models/gemini-test", "supportedGenerationMethods": ["generateContent"]}
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-secret", "GEMINI_MODEL": "gemini-test"}), \
                patch.object(diagnostic.requests, "get", return_value=response) as get, \
                patch.object(diagnostic.requests, "post") as post, \
                patch.object(diagnostic, "get_session") as session, patch("builtins.print") as output:
            diagnostic.main(check_model=True)
        get.assert_called_once()
        post.assert_not_called()
        session.assert_not_called()
        self.assertNotIn("test-secret", get.call_args.args[0])
        self.assertNotIn("json", get.call_args.kwargs)
        printed = output.call_args.args[0]
        self.assertNotIn("test-secret", printed)
        result = json.loads(printed)
        self.assertTrue(result["model_matches"])
        self.assertTrue(result["supports_generate_content"])

    def test_error_redacts_credentials_and_identifiers(self):
        response = Mock(status_code=400)
        response.json.return_value = {"error": {"message":
            "bad test-secret AIzaAnotherKey https://example.com?key=secret projects/private user@example.com",
            "status": "INVALID_ARGUMENT"}}
        result = diagnostic.response_diagnostics(response, "test-secret")
        for secret in ("test-secret", "AIzaAnotherKey", "example.com", "projects/private"):
            self.assertNotIn(secret, str(result))
        self.assertEqual(result["provider_error_status"], "INVALID_ARGUMENT")

    def test_non_json_body_is_not_exposed(self):
        response = Mock(status_code=400, text="<!DOCTYPE html>private-secret")
        response.json.side_effect = ValueError()
        result = diagnostic.response_diagnostics(response, "key")
        self.assertEqual(result["body_format"], "non_json")
        self.assertTrue(result["looks_like_html"])
        self.assertNotIn("private-secret", str(result))

    def test_success_body_is_not_exposed(self):
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": ["private"]}
        self.assertNotIn("private", str(diagnostic.response_diagnostics(response, "key")))

    def test_missing_key_does_not_call_provider(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}), patch.object(diagnostic.requests, "post") as post, patch("builtins.print"):
            diagnostic.main()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
