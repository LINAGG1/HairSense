"""개발용 다중 세션 데이터. 기존 mock_data와 DB는 변경하지 않습니다."""

import csv
import json
import math
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "mock_sessions" / "experiment_001"
SEED = 20260923
SAMPLE_RATE_HZ = 50
INTERVAL_MS = 20
DURATION_SECONDS = 60
START_TIMESTAMP_MS = 1000
FIELDS = {"schema_version", "boot_id", "seq", "timestamp_ms",
          "optical", "gyro_x", "gyro_y", "gyro_z"}
TEST_SCENARIOS = [
    "normal", "optical_high", "optical_low", "optical_variability",
    "gyro_variability", "gyro_spikes",
]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False),
                    encoding="utf-8")


def make_session(split, ordinal, scenario):
    seed = SEED + ordinal
    rng = random.Random(seed)
    # 추가 이상 신호의 난수는 기본 신호와 분리합니다.
    anomaly_rng = random.Random(seed + 100000)
    session_id = f"multi-session-{ordinal:03d}"
    boot_id = f"multi-boot-{ordinal:03d}"
    folder = OUTPUT / split / session_id
    folder.mkdir(parents=True, exist_ok=True)
    offset = rng.uniform(-0.015, 0.015)
    frequency = rng.uniform(0.75, 1.05)
    amplitude_scale = rng.uniform(0.9, 1.1)
    phase = rng.uniform(0, 2 * math.pi)
    sample_count = SAMPLE_RATE_HZ * DURATION_SECONDS
    with (folder / "sensor_mock.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for index in range(sample_count):
            t = index / SAMPLE_RATE_HZ
            angle = 2 * math.pi * frequency * t + phase
            optical = 1.2 + offset + 0.008 * math.sin(angle) + rng.gauss(0, 0.02)
            gyro = [
                amplitude_scale * amplitude * math.sin(angle + axis_phase)
                + rng.gauss(0, 0.5)
                for amplitude, axis_phase in [(18, 0), (12, 0.4), (9, -0.3)]
            ]
            # 평가 세션에서만 30~40초에 이상 신호를 주입합니다.
            if split == "test" and 30 <= t < 40:
                if scenario == "optical_high":
                    optical += 0.3
                elif scenario == "optical_low":
                    optical -= 0.3
                elif scenario == "optical_variability":
                    optical += anomaly_rng.gauss(0, 0.12)
                elif scenario == "gyro_variability":
                    gyro = [value + anomaly_rng.gauss(0, noise)
                            for value, noise in zip(gyro, [20, 15, 10])]
                elif scenario == "gyro_spikes" and index % 50 == 25:
                    gyro = [value + jump for value, jump in zip(gyro, [45, -30, 20])]
            row = {
                "schema_version": 1, "boot_id": boot_id, "seq": index + 1,
                "timestamp_ms": START_TIMESTAMP_MS + index * INTERVAL_MS,
                "optical": round(optical, 4), "gyro_x": round(gyro[0], 4),
                "gyro_y": round(gyro[1], 4), "gyro_z": round(gyro[2], 4),
            }
            stream.write(json.dumps(row, allow_nan=False) + "\n")

    # 정답 라벨은 센서 JSON에 섞지 않고 평가 전용 파일에 저장합니다.
    with (folder / "window_labels.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "window_id", "elapsed_start_s", "elapsed_end_s", "scenario",
            "optical_injected_anomaly", "gyro_injected_anomaly",
        ])
        writer.writeheader()
        for wid in range(30):
            active = split == "test" and 15 <= wid < 20 and scenario != "normal"
            writer.writerow({
                "window_id": wid, "elapsed_start_s": wid * 2,
                "elapsed_end_s": wid * 2 + 2,
                "scenario": scenario if active else "normal",
                "optical_injected_anomaly": int(active and scenario.startswith("optical_")),
                "gyro_injected_anomaly": int(active and scenario.startswith("gyro_")),
            })
    meta = {
        "is_synthetic": True, "schema_version": 1,
        "purpose": "개인별 분석 로직의 개발용 시뮬레이션; 실제 센서 검증 자료 아님",
        "user_id": "mock-multi-user-001", "device_id": "mock-multi-device-001",
        "session_id": session_id, "boot_id": boot_id, "split": split,
        "session_order": ordinal, "random_seed": seed,
        "sample_rate_hz": SAMPLE_RATE_HZ, "interval_ms": INTERVAL_MS,
        "duration_seconds": DURATION_SECONDS, "start_timestamp_ms": START_TIMESTAMP_MS,
        "timestamp_unit": "ms", "timestamp_reference": "elapsed_since_boot",
        "optical_unit": "V", "gyro_unit": "deg/s",
        "adc_resolution_bits": 12, "adc_input_range_v": None,
        "gyro_full_scale_dps": None, "expected_samples": sample_count,
        "written_samples": sample_count, "dropped_messages": 0,
        "optical_null_samples": 0, "reboot_simulated": False,
        "boot_note": "각 세션 전 새 부팅을 가정; 세션 내부 재부팅 없음",
        "quality_note": "모델 분리 실험을 위한 완전한 데이터; 결측·통신 장애는 기존 mock_data로 검사",
        "simulation_settings": {"optical_offset_v": offset, "frequency_hz": frequency,
                                "gyro_amplitude_scale": amplitude_scale, "phase_rad": phase},
    }
    write_json(folder / "metadata.json", meta)
    return {"split": split, "session_id": session_id, "boot_id": boot_id,
            "directory": folder.relative_to(OUTPUT).as_posix(), "scenario": scenario,
            "session_order": ordinal}


def validate_dataset(entries):
    """생성 파일을 다시 읽어 규격, 분할, 라벨 수를 검증합니다."""
    if len({e["session_id"] for e in entries}) != len(entries):
        raise ValueError("Duplicate session ID")
    if len({e["boot_id"] for e in entries}) != len(entries):
        raise ValueError("Duplicate boot ID")
    counts = {"train": 0, "calibration": 0, "test": 0}
    total = 0
    for entry in entries:
        folder = OUTPUT / entry["directory"]
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in
                (folder / "sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()]
        if len(rows) != 3000 or meta["split"] != entry["split"]:
            raise ValueError("Session count or partition mismatch")
        for index, row in enumerate(rows):
            if set(row) != FIELDS or type(row["schema_version"]) is not int or row["schema_version"] != 1:
                raise ValueError("Sensor schema mismatch")
            if row["boot_id"] != entry["boot_id"] or row["seq"] != index + 1:
                raise ValueError("Boot or sequence mismatch")
            if row["timestamp_ms"] != START_TIMESTAMP_MS + index * INTERVAL_MS:
                raise ValueError("Sampling interval mismatch")
            if not all(isinstance(row[k], float) and math.isfinite(row[k])
                       for k in ("optical", "gyro_x", "gyro_y", "gyro_z")):
                raise ValueError("Invalid synthetic sensor value")
        with (folder / "window_labels.csv").open(encoding="utf-8-sig", newline="") as file:
            labels = list(csv.DictReader(file))
        expected = 5 if entry["scenario"] != "normal" else 0
        actual = sum(int(r["optical_injected_anomaly"]) + int(r["gyro_injected_anomaly"])
                     for r in labels)
        if len(labels) != 30 or actual != expected:
            raise ValueError("Scenario labels mismatch")
        if entry["split"] != "test" and actual:
            raise ValueError("Injected anomalies outside test split")
        counts[entry["split"]] += 1
        total += len(rows)
    if counts != {"train": 12, "calibration": 6, "test": 6}:
        raise ValueError("Partition counts mismatch")
    return counts, total


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []
    plan = [("train", "normal")] * 12 + [("calibration", "normal")] * 6
    plan += [("test", scenario) for scenario in TEST_SCENARIOS]
    for ordinal, (split, scenario) in enumerate(plan, start=1):
        entries.append(make_session(split, ordinal, scenario))
    counts, total = validate_dataset(entries)
    write_json(OUTPUT / "manifest.json", {
        "dataset_version": "multi_session_demo_1", "is_synthetic": True,
        "seed": SEED, "sessions": entries, "session_counts": counts,
        "total_readings": total, "window_seconds": 2,
        "split_policy": "Fixed disjoint sessions: train 1-12, calibration 13-18, test 19-24",
        "usage": "Fit baseline/models only on train; choose thresholds on calibration; evaluate test last. Labels are not input features.",
        "limitations": "One simulated user. No real-world accuracy claim. Tail percentiles from 180 calibration windows remain unstable.",
    })
    for split, count in counts.items():
        print(f"{split}_sessions: {count}")
    print(f"total_readings: {total}")
    print("schema_and_split_validation: PASS")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
