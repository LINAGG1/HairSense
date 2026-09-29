"""Check the real measurement button without an image or physical sensors."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def snapshot(session="second", baseline=1):
    sensors = {sensor: {"status": "completed", "session_id": f"{sensor}-{session}", "boot_id": "boot",
        "recorded_at": "2026-09-30", "usable_windows": 2, "excluded_windows": 0,
        "compared_baseline_version": baseline, "reference_windows": baseline*2,
        "threshold_status": "provisional", "threshold": .1, "rule_threshold": 2.0,
        "mean_anomaly_score": .2, "flagged_windows": 1, "evaluated_windows": 2,
        "feature_comparison": {}} for sensor in ("optical", "gyro")}
    return {"status": "available", "summary": {"sensors": sensors}, "captures": {}}


class SensorUITests(unittest.TestCase):
    def test_button_displays_both_sensors_without_image_and_freezes_snapshot(self):
        current = snapshot()
        def get(url, **kwargs):
            if url.endswith("/real-sensors/latest"):
                self.assertEqual(kwargs["params"], {"user_id": "real-user", "device_id": "real-device"})
                return Mock(json=lambda: copy.deepcopy(current), raise_for_status=lambda: None)
            # Existing image/test-session flow has no data in this scenario.
            raise requests.HTTPError("No historical image/test session")
        with patch.dict(os.environ, API_BASE_URL="http://sensor-test.invalid", HAIRSENSE_REAL_USER_ID="real-user",
                        HAIRSENSE_REAL_DEVICE_ID="real-device"), patch("requests.get", side_effect=get):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run(timeout=30)
            self.assertFalse(app.exception)
            button = next(b for b in app.button if b.label == "측정 결과 확인")
            button.click().run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.metric), 2)
            self.assertIn("광학 센서 분석 결과", [m.value for m in app.markdown])
            self.assertIn("자이로 센서 분석 결과", [m.value for m in app.markdown])
            current = snapshot("third", 2)
            app.run(timeout=30)
            self.assertEqual(app.session_state["real_sensor_snapshot"]["summary"]["sensors"]["optical"]["session_id"], "optical-second")
            next(b for b in app.button if b.label == "측정 결과 확인").click().run(timeout=30)
            self.assertEqual(app.session_state["real_sensor_snapshot"]["summary"]["sensors"]["optical"]["session_id"], "optical-third")

    def test_calibration_button_targets_selected_sensor(self):
        with patch.dict(os.environ, API_BASE_URL="http://sensor-test.invalid"), patch("requests.post") as post:
            post.return_value.status_code = 200
            app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run(timeout=30)
            app.selectbox[0].select("gyro").run(timeout=30)
            next(b for b in app.button if b.label == "다음 센서 측정에 적용").click().run(timeout=30)
            self.assertFalse(app.exception)
            self.assertTrue(post.call_args.args[0].endswith("/real-sensors/gyro/next-capture"))
            self.assertEqual(post.call_args.kwargs["json"]["role"], "calibration")


if __name__ == "__main__":
    unittest.main()
