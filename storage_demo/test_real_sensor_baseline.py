"""Generated inputs test chronology/contracts, not clinical accuracy."""
import copy
import unittest

from real_sensor_baseline import advance, empty_state
from test_analyze_real_sessions import fixture


class OnlineBaselineTests(unittest.TestCase):
    def capture(self, state, number, sensor="optical", role="measurement", shift=0):
        meta, rows = fixture(sensor, f"session-{number}")
        if shift:
            for row in rows:
                row["optical" if sensor == "optical" else "gyro_x"] += shift
        return advance(state, rows, meta, sensor, role)

    def test_first_second_third_fourth_and_immutable_results(self):
        for sensor in ("optical", "gyro"):
            state, first = self.capture(empty_state(), 1, sensor)
            self.assertEqual(first["status"], "baseline_created")
            self.assertIsNone(first["compared_baseline"])
            self.assertEqual(state["baseline"]["reference_windows"], 2)
            state, second = self.capture(state, 2, sensor, shift=5)
            saved = copy.deepcopy(second)
            self.assertEqual(second["compared_baseline"]["reference_windows"], 2)
            self.assertEqual(second["summary"]["sensors"][sensor]["flagged_windows"], 2)
            state, third = self.capture(state, 3, sensor)
            self.assertEqual(third["compared_baseline"]["reference_windows"], 4)
            self.assertEqual([s["session_id"] for s in third["compared_baseline"]["train_sessions"]],
                             ["session-1", "session-2"])
            state, fourth = self.capture(state, 4, sensor)
            self.assertEqual(fourth["compared_baseline"]["reference_windows"], 6)
            self.assertEqual(second, saved)

    def test_calibration_is_held_out_and_recalibrates_updated_models(self):
        state, _ = self.capture(empty_state(), 1)
        state, result = self.capture(state, 99, role="calibration", shift=.05)
        self.assertEqual(result["status"], "calibration_completed")
        self.assertEqual(len(state["train"]), 1)
        self.assertEqual(state["baseline"]["threshold_status"], "calibrated")
        self.assertEqual(state["baseline"]["calibration_sessions"][0]["session_id"], "session-99")
        state, result = self.capture(state, 2)
        self.assertEqual(result["compared_baseline"]["threshold_status"], "calibrated")
        self.assertEqual(state["baseline"]["reference_windows"], 4)
        self.assertEqual(len(state["calibration"]), 1)

    def test_missing_and_partial_windows_do_not_train(self):
        meta, rows = fixture("optical")
        rows[0]["optical"] = None
        rows = rows[:-1]
        state = empty_state()
        after, result = advance(state, rows, meta, "optical")
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(after, state)

    def test_small_first_capture_accumulates_until_two_windows(self):
        meta, rows = fixture("optical")
        meta["end_timestamp_ms"] = 3000
        state, result = advance(empty_state(), rows[:100], meta, "optical")
        self.assertEqual(result["status"], "baseline_collecting")
        state, result = self.capture(state, 2)
        self.assertEqual(result["status"], "baseline_created")
        self.assertEqual(state["baseline"]["reference_windows"], 3)

    def test_no_synthetic_cross_user_or_duplicate_session(self):
        state, _ = self.capture(empty_state(), 1)
        with self.assertRaisesRegex(ValueError, "already admitted"):
            self.capture(state, 1)
        for updates in ({"is_synthetic": True}, {"user_id": "other"}, {"device_id": "other"}):
            meta, rows = fixture("optical", "session-2")
            meta.update(updates)
            with self.assertRaises(ValueError):
                advance(state, rows, meta, "optical")

    def test_calibration_requires_initial_reference(self):
        state, result = self.capture(empty_state(), 1, role="calibration")
        self.assertEqual(result["status"], "baseline_required")
        self.assertEqual(state, empty_state())


if __name__ == "__main__":
    unittest.main()
