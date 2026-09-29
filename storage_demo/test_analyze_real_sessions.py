"""Generated fixtures verify contracts, not real-world accuracy."""
import json
import tempfile
import unittest
from pathlib import Path
from analyze_real_sessions import features, run


def fixture(sensor="gyro", session="s1"):
    meta = dict(device_id="device", user_id="user", session_id=session, boot_id="same-boot",
                is_synthetic=False, schema_version=1, sample_rate_hz=50,
                optical_unit="V", gyro_unit="deg/s", start_timestamp_ms=1000,
                end_timestamp_ms=5000, sample_type="gyro" if sensor == "gyro" else "optical")
    rows = [dict(schema_version=1, boot_id="same-boot", seq=i+1,
                 timestamp_ms=1000+i*20+(i % 2), optical=None,
                 gyro_x=None, gyro_y=None, gyro_z=None) for i in range(200)]
    for i, row in enumerate(rows):
        if sensor == "optical":
            row["optical"] = 0.5+i/1000
        else:
            row.update(gyro_x=i/100, gyro_y=0.1, gyro_z=-0.1)
    return meta, rows


class RealTrainingTests(unittest.TestCase):
    def test_separate_channels_and_jitter(self):
        for sensor in ("optical", "gyro"):
            meta, rows = fixture(sensor)
            result, report = features(rows, meta, sensor)
            self.assertEqual(len(result), 2)
            self.assertEqual(report["excluded_windows"], 0)

    def test_missing_sample_and_null_exclude_windows(self):
        meta, rows = fixture()
        rows.pop(4)
        rows[-1]["gyro_x"] = None
        result, report = features(rows, meta, "gyro")
        self.assertEqual(result, [])
        self.assertEqual(report["excluded_windows"], 2)

    def test_reject_mixed_source_and_reboot(self):
        for field, value in (("is_synthetic", True), ("boot_id", "other"), ("timestamp_ms", 0)):
            meta, rows = fixture()
            rows[0][field] = value
            with self.assertRaises(ValueError):
                features(rows, meta, "gyro")

    def test_training_and_split_leakage(self):
        for sensor in ("optical", "gyro"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                entries = []
                for order, split in enumerate(("train", "calibration", "test")):
                    folder = root / split
                    folder.mkdir()
                    meta, rows = fixture(sensor, split)
                    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
                    (folder / "sensor_readings.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
                    entries.append(dict(directory=split, session_id=split, boot_id="same-boot",
                                        split=split, session_order=order, baseline_normal=True))
                manifest = dict(is_synthetic=False, sensor=sensor, sessions=entries)
                path = root / "manifest.json"
                path.write_text(json.dumps(manifest), encoding="utf-8")
                output = root / "output"
                run(root, output)
                self.assertTrue((output / f"{sensor}_model.joblib").exists())
                summary = json.loads((output / "run_summary.json").read_text())
                self.assertIsNone(summary["test_metrics"])
                self.assertFalse(summary["is_synthetic"])
                with self.assertRaises(ValueError):
                    run(root, output)
                entries[-1].update(directory="train", session_id="train")
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "reused"):
                    run(root, root / "invalid-output")
                self.assertFalse((root / "invalid-output").exists())


if __name__ == "__main__":
    unittest.main()
