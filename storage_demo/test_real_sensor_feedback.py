import copy
import unittest

from real_sensor_feedback import with_real_feedback
from sensor_feedback import select_finding


def capture(sensor="optical", values=(10, 10), flagged=(False, False)):
    return {"is_synthetic": False, "sensor": sensor, "status": "completed", "feedback": None,
        "compared_baseline": {"feature_mean": {"optical_mean": 10}, "feature_std": {"optical_mean": 1}, "rule_threshold": 2},
        "features": [{"window_id": i, "optical_mean": value} for i, value in enumerate(values)],
        "scores": [{"window_id": i, "is_anomaly": value, "rule_threshold": 2} for i,value in enumerate(flagged)],
        "summary": {"sensors": {sensor: {"status": "completed"}}}}


class RealFeedbackTests(unittest.TestCase):
    def test_optical_direction_mixed_pattern_and_unflagged(self):
        for values, flags, expected in [
            ((13, 10), (True, False), "optical_high"),
            ((7, 10), (True, False), "optical_low"),
            ((13, 7), (True, True), "optical_mixed"),
            ((11, 10), (True, False), "optical_pattern"),
            ((13, 7), (False, False), "optical_unflagged"),
            ((12, 8), (True, True), "optical_pattern"),
        ]:
            with self.subTest(expected=expected, values=values):
                original = capture(values=values, flagged=flags)
                before = copy.deepcopy(original)
                result = with_real_feedback(original)
                self.assertEqual(result["summary"]["sensors"]["optical"]["finding"], expected)
                self.assertEqual(original, before)
                self.assertEqual(result["scores"], before["scores"])
                self.assertEqual(result["compared_baseline"], before["compared_baseline"])
                self.assertIn("유분 상태 참고", result["feedback"]["messages"]["optical"]["message"])

    def test_gyro_does_not_invent_rapid_event_counts(self):
        for flags, expected in [((True, False), "gyro_pattern"), ((False, False), "gyro_unflagged")]:
            result = with_real_feedback(capture("gyro", flagged=flags))
            self.assertEqual(result["summary"]["sensors"]["gyro"]["finding"], expected)
            self.assertNotIn("횟수", result["summary"]["sensors"]["gyro"]["message"])
        self.assertEqual(select_finding("gyro", 2, {"gyro_rapid_changes_increased": 1}), "gyro_rapid")

    def test_states_and_missing_evidence_are_not_normal_verdicts(self):
        for status in ("baseline_created", "baseline_collecting", "baseline_required", "insufficient_data", "calibration_completed"):
            record = capture()
            record.update(status=status, scores=[], compared_baseline=None)
            result = with_real_feedback(record)
            self.assertEqual(result["feedback"]["messages"]["optical"]["finding"], status)
        record = capture()
        record["scores"] = []
        self.assertEqual(with_real_feedback(record)["summary"]["sensors"]["optical"]["finding"], "comparison_unavailable")

    def test_constant_reference_missing_features_and_saved_feedback(self):
        record = capture(values=(11, 10), flagged=(True, False))
        record["compared_baseline"]["feature_std"]["optical_mean"] = 0
        result = with_real_feedback(record)
        self.assertEqual(result["summary"]["sensors"]["optical"]["finding"], "optical_high")
        self.assertEqual(with_real_feedback(result), result)
        record["features"] = []
        self.assertEqual(with_real_feedback(record)["summary"]["sensors"]["optical"]["finding"], "optical_pattern")

    def test_synthetic_is_rejected(self):
        record = capture()
        record["is_synthetic"] = True
        with self.assertRaises(ValueError):
            with_real_feedback(record)


if __name__ == "__main__":
    unittest.main()
