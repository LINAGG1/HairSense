"""Check the real measurement button without an image or physical sensors."""
import copy
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests
from PIL import Image
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def snapshot(session="second", baseline=1):
    sensors = {sensor: {"status": "completed", "session_id": f"{sensor}-{session}", "boot_id": "boot",
        "recorded_at": "2026-09-30", "usable_windows": 2, "excluded_windows": 0,
        "compared_baseline_version": baseline, "reference_windows": baseline*2,
        "threshold_status": "provisional", "threshold": .1, "rule_threshold": 2.0,
        "mean_anomaly_score": .2, "flagged_windows": 1, "evaluated_windows": 2,
        "feature_comparison": {}, "message": "유분 상태 참고: 평소와 다른 광학 반사 패턴이 나타났어요." if sensor == "optical" else "평소와 다른 움직임 패턴이 나타났어요."} for sensor in ("optical", "gyro")}
    captures = {sensor: {"metadata": {"user_id": "real-user", "device_id": "real-device",
                 "session_id": row["session_id"], "boot_id": row["boot_id"]}} for sensor, row in sensors.items()}
    return {"status": "available", "summary": {"sensors": sensors}, "captures": captures,
            "feedback": {"notice": "센서 신호 비교 안내", "messages": {s: {"message": r["message"]} for s,r in sensors.items()}}}


class SensorUITests(unittest.TestCase):
    def test_button_displays_both_sensors_without_image_and_freezes_snapshot(self):
        current = snapshot()
        def get(url, **kwargs):
            if url.endswith("/real-sensors/latest"):
                self.assertEqual(kwargs["params"], {"user_id": "real-user", "device_id": "real-device"})
                return Mock(json=lambda: copy.deepcopy(current), raise_for_status=lambda: None)
            if "/ai/history/" in url:
                return Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)
            self.assertIn("/sensor-sessions/optical-", url)
            self.assertEqual(kwargs["params"]["user_id"], "real-user")
            return Mock(status_code=404)
        with patch.dict(os.environ, API_BASE_URL="http://sensor-test.invalid", HAIRSENSE_REAL_USER_ID="real-user",
                        HAIRSENSE_REAL_DEVICE_ID="real-device"), patch("requests.get", side_effect=get):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run(timeout=30)
            self.assertFalse(app.exception)
            button = next(b for b in app.button if b.label == "측정 결과 확인")
            button.click().run(timeout=30)
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["measurement_done"])
            self.assertEqual(len(app.metric), 0)
            self.assertEqual(len(app.dataframe), 0)
            self.assertEqual(len(app.json), 0)
            self.assertEqual(len(app.code), 0)
            self.assertNotIn("센서 분석 상세", [e.label for e in app.expander])
            self.assertNotIn("센서 기준 조정 세션", [e.label for e in app.expander])
            self.assertNotIn("📊 촬영 당시 센서 상태", [e.value for e in app.subheader])
            self.assertFalse(any('st.subheader' in e.value for e in app.markdown))
            self.assertNotIn("광학 센서 분석 결과", [m.value for m in app.markdown])
            self.assertNotIn("자이로 센서 분석 결과", [m.value for m in app.markdown])
            rendered = [(e.type, getattr(e, "value", None)) for e in app]
            notice_index = rendered.index(("subheader", "💡 오늘의 안내"))
            optical_index = rendered.index(("markdown", current["summary"]["sensors"]["optical"]["message"]))
            gyro_index = rendered.index(("markdown", current["summary"]["sensors"]["gyro"]["message"]))
            self.assertLess(notice_index, optical_index)
            self.assertLess(optical_index, gyro_index)
            self.assertIn("이번 측정에서 수신된 이미지가 없습니다.", [m.value for m in app.info])
            self.assertNotIn("📊 실제 센서 분석", [m.value for m in app.subheader])
            for row in current["summary"]["sensors"].values():
                self.assertIn(row["message"], [m.value for m in app.markdown])
            self.assertFalse(app.error)
            current = snapshot("third", 2)
            app.run(timeout=30)
            self.assertEqual(app.session_state["real_sensor_snapshot"]["summary"]["sensors"]["optical"]["session_id"], "optical-second")
            next(b for b in app.button if b.label == "🔄 다시 측정하기").click().run(timeout=30)
            next(b for b in app.button if b.label == "측정 결과 확인").click().run(timeout=30)
            self.assertEqual(app.session_state["real_sensor_snapshot"]["summary"]["sensors"]["optical"]["session_id"], "optical-third")

    def test_optional_image_success_timeout_and_missing_sensor(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
        for mode in ("image", "timeout", "gyro_only", "no_sensor"):
            with self.subTest(mode=mode):
                current = snapshot()
                if mode == "gyro_only":
                    current["summary"]["sensors"].pop("optical")
                    current["captures"].pop("optical")
                if mode == "no_sensor":
                    current.update(summary={"sensors": {}}, captures={})
                def get(url, **kwargs):
                    if url.endswith("/real-sensors/latest"):
                        return Mock(json=lambda: current, raise_for_status=lambda: None)
                    if "/ai/history/" in url:
                        return Mock(json=lambda: {"items": []}, raise_for_status=lambda: None)
                    if mode == "timeout":
                        raise requests.Timeout()
                    self.assertEqual(mode, "image")
                    self.assertTrue(url.endswith("/sensor-sessions/optical-second/image"))
                    return Mock(status_code=200, content=buffer.getvalue(), headers={"Content-Type": "image/png"}, raise_for_status=lambda: None)
                with patch.dict(os.environ, API_BASE_URL="http://sensor-test.invalid"), patch("requests.get", side_effect=get):
                    app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run(timeout=30)
                    next(b for b in app.button if b.label == "측정 결과 확인").click().run(timeout=30)
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state["measurement_done"], mode != "no_sensor")
                    if mode == "image":
                        self.assertEqual(app.session_state["image_bytes"], buffer.getvalue())
                    elif mode == "timeout":
                        self.assertIn("이미지를 불러오지 못했습니다. 센서 결과는 확인할 수 있습니다.", [m.value for m in app.info])
                    elif mode == "gyro_only":
                        self.assertEqual(len(app.metric), 0)

    def test_calibration_controls_are_absent(self):
        with patch.dict(os.environ, API_BASE_URL="http://sensor-test.invalid"), patch("requests.post") as post:
            app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertNotIn("센서 기준 조정 세션", [e.label for e in app.expander])
            self.assertNotIn("다음 센서 측정에 적용", [b.label for b in app.button])
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
