"""Generate synthetic HairSense messages using the agreed sensor format."""

import csv
import json
import math
import random
from pathlib import Path

# Confirmed with the electronics team.
SAMPLE_RATE_HZ = 50
INTERVAL_MS = 20
OPTICAL_UNIT = "V"
GYRO_UNIT = "deg/s"

# Test choices, not measured physical limits or personal normal ranges.
SCHEMA_VERSION = "0.1"  # Proposed version value; confirm before integration.
DURATION_SECONDS = 60
START_TIMESTAMP_MS = 1000  # Capture starts one second after simulated boot.
OPTICAL_MEAN = 1.2
OPTICAL_NOISE_STD = 0.02
GYRO_NOISE_STD = 0.5


def sensor_value(value):
    """Keep zero; convert missing and non-finite readings to JSON null."""
    if value is None:
        return None
    value = float(value)
    return round(value, 4) if math.isfinite(value) else None


def main():
    rng = random.Random(42)
    output_dir = Path(__file__).resolve().parent / "mock_data"
    output_dir.mkdir(exist_ok=True)
    total_samples = SAMPLE_RATE_HZ * DURATION_SECONDS
    written_count = null_count = dropped_count = 0

    with (
        (output_dir / "sensor_mock.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as sensor_file,
        (output_dir / "scenario_labels.csv").open(
            "w", encoding="utf-8", newline=""
        ) as label_file,
    ):
        writer = csv.writer(label_file)
        writer.writerow(
            ["seq", "timestamp_ms", "scenario", "written_to_sensor_file"]
        )
        for index in range(total_samples):
            elapsed_ms = index * INTERVAL_MS
            seq = index + 1
            timestamp_ms = START_TIMESTAMP_MS + elapsed_ms
            optical = rng.gauss(OPTICAL_MEAN, OPTICAL_NOISE_STD)
            gyro_x = rng.gauss(0.0, GYRO_NOISE_STD)
            gyro_y = rng.gauss(0.0, GYRO_NOISE_STD)
            gyro_z = rng.gauss(0.0, GYRO_NOISE_STD)
            scenario = "normal"

            if 30000 <= elapsed_ms < 35000:
                optical += 0.6
                scenario = "optical_shift"
            elif 35000 <= elapsed_ms < 40000:
                gyro_x = rng.gauss(0.0, 30.0)
                gyro_y = rng.gauss(0.0, 20.0)
                gyro_z = rng.gauss(0.0, 15.0)
                scenario = "gyro_motion"
            elif 45000 <= elapsed_ms < 46000:
                optical = None
                scenario = "optical_missing"
            elif 50000 <= elapsed_ms < 50200:
                scenario = "message_drop"

            should_write = scenario != "message_drop"
            writer.writerow([seq, timestamp_ms, scenario, should_write])
            if not should_write:
                dropped_count += 1
                continue

            message = {
                "schema_version": SCHEMA_VERSION,
                "seq": seq,
                "timestamp_ms": timestamp_ms,
                "optical": sensor_value(optical),
                "gyro_x": sensor_value(gyro_x),
                "gyro_y": sensor_value(gyro_y),
                "gyro_z": sensor_value(gyro_z),
            }
            null_count += int(message["optical"] is None)
            sensor_file.write(
                json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n"
            )
            written_count += 1

    metadata = {
        "is_synthetic": True,
        "purpose": "수신·저장·전처리 기능 테스트",
        "schema_version": SCHEMA_VERSION,
        "schema_version_status": "필드는 합의됨; 버전 문자열 0.1은 제안값",
        "device_id": "mock-device-01",
        "session_id": "mock-session-001",
        "user_id": "mock-user-001",
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "interval_ms": INTERVAL_MS,
        "duration_seconds": DURATION_SECONDS,
        "start_timestamp_ms": START_TIMESTAMP_MS,
        "timestamp_unit": "ms",
        "timestamp_reference": "ESP32 부팅 후 경과 시간",
        "optical_unit": OPTICAL_UNIT,
        "gyro_unit": GYRO_UNIT,
        "adc_resolution_bits": None,
        "adc_input_range_v": None,
        "gyro_full_scale_dps": None,
        "hardware_settings_status": "ADC 설정과 자이로 측정 범위는 추가 확인 필요",
        "reboot_simulated": False,
        "reboot_note": "재부팅 시 timestamp_ms와 seq 초기화; boot_id 추가는 미합의",
        "random_seed": 42,
        "expected_samples": total_samples,
        "written_samples": written_count,
        "dropped_messages": dropped_count,
        "optical_null_samples": null_count,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"생성 위치: {output_dir}")
    print(f"예상 측정 수: {total_samples}")
    print(f"저장 메시지 수: {written_count}")
    print(f"누락 메시지 수: {dropped_count}")
    print(f"optical이 null인 메시지 수: {null_count}")


if __name__ == "__main__":
    main()
