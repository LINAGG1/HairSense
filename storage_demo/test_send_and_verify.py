"""Verify upload selection, replay, comparison and pagination without a live DB."""

import copy
import unittest
from unittest.mock import Mock, patch

import send_and_verify as sender


class UploadTests(unittest.TestCase):
    def test_selected_holdout_is_complete(self):
        meta, rows = sender.load_source("multi-session-102")
        self.assertEqual(meta["boot_id"], "multi-boot-102")
        self.assertEqual(len(rows), 3000)
        self.assertEqual(rows[-1]["seq"], 3000)

    def test_unknown_session_rejected(self):
        with self.assertRaises(ValueError):
            sender.load_source("unknown-session")

    def test_dry_run_never_calls_http(self):
        with patch.object(sender.requests, "Session") as client, patch("builtins.print"):
            sender.main(["--session-id", "multi-session-102", "--dry-run"])
        client.assert_not_called()

    def run_mock_upload(self, duplicate=False, mismatch=False, stalled=False):
        meta, rows = sender.load_source("multi-session-102")
        rows = rows[:2]
        context = {key: meta[key] for key in ("device_id", "session_id", "user_id", "is_synthetic")}
        stored = [{**context, **row} for row in copy.deepcopy(rows)]
        if mismatch:
            stored[0]["optical"] += 1
        def response(data):
            result = Mock(ok=True)
            result.json.return_value = data
            return result
        client = Mock()
        client.post.return_value = response({"inserted": 0 if duplicate else 2, "duplicates": 2 if duplicate else 0})
        client.get.side_effect = [response({"status": "ok"}), response({"items": stored}),
                                  response({"items": stored if stalled else []})]
        manager = Mock()
        manager.__enter__ = Mock(return_value=client)
        manager.__exit__ = Mock(return_value=False)
        with patch.object(sender, "load_source", return_value=(meta, rows)), \
                patch.object(sender.requests, "Session", return_value=manager), patch("builtins.print") as output:
            sender.main(["--session-id", "multi-session-102"])
        sent = client.post.call_args.kwargs["json"]
        self.assertEqual(sent["session_id"], "multi-session-102")
        self.assertEqual(sent["readings"], rows)
        self.assertEqual(client.get.call_args.kwargs["params"]["boot_id"], "multi-boot-102")
        self.assertTrue(any("PASS: all 2 records" in str(call) for call in output.call_args_list))

    def test_insert_and_replay(self):
        self.run_mock_upload()
        self.run_mock_upload(duplicate=True)

    def test_wrong_stored_value_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Value mismatch"):
            self.run_mock_upload(mismatch=True)

    def test_stalled_pagination_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            self.run_mock_upload(stalled=True)


if __name__ == "__main__":
    unittest.main()
