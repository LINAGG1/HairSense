"""Preprocess one completed mock session; never change database records.

Default: read MySQL records through the existing FastAPI GET endpoint.
--source-file: test the same rules against the local mock file without a server.
Exact 20 ms timing and known capture bounds apply to this mock exercise only.
"""
import argparse
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENSORS = ("optical", "gyro_x", "gyro_y", "gyro_z")
FIELDS = ("schema_version", "seq", "timestamp_ms", *SENSORS)
WINDOW_MS = 2000


def fetch(meta):
    import requests

    rows, after = [], -1
    with requests.Session() as client:
        while True:
            response = client.get(
                "http://127.0.0.1:8000/readings",
                params={"device_id": meta["device_id"],
                        "session_id": meta["session_id"],
                        "after_seq": after, "limit": 500},
                timeout=30,
            )
            response.raise_for_status()
            page = response.json()["items"]
            if not page:
                break
            if page[-1]["seq"] <= after:
                raise ValueError("API pagination did not advance")
            rows.extend(page)
            after = page[-1]["seq"]
    return rows


def process(rows, meta):
    if meta.get("is_synthetic") is not True:
        raise ValueError("These strict timing rules are for the mock exercise only")
    interval = meta["interval_ms"]
    start = meta["start_timestamp_ms"]
    duration = int(meta["duration_seconds"] * 1000)
    if interval != 20 or duration % WINDOW_MS:
        raise ValueError("Expected 20 ms sampling and whole 2-second windows")
    total_expected = duration // interval
    unique, duplicate_count = {}, 0
    for source in rows:
        # Missing keys are schema errors, not null measurements.
        row = {key: source[key] for key in FIELDS}
        for key in ("seq", "timestamp_ms"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError(f"Invalid integer field: {key}")
        if row["schema_version"] != "0.1":
            raise ValueError("Unsupported schema version")
        seq = row["seq"]
        if seq in unique:
            if unique[seq] != row:
                raise ValueError(f"Conflicting duplicate seq={seq}; inspect source")
            duplicate_count += 1
        else:
            unique[seq] = row

    by_seq = sorted(unique.values(), key=lambda row: row["seq"])
    # Do not silently sort a reboot back into the same session.
    if any(b["timestamp_ms"] <= a["timestamp_ms"]
           for a, b in zip(by_seq, by_seq[1:])):
        raise ValueError("Repeated/backward timestamp: inspect reboot or session separation")
    ordered = sorted(by_seq, key=lambda row: row["timestamp_ms"])
    buckets = [[] for _ in range(duration // WINDOW_MS)]
    for row in ordered:
        offset = row["timestamp_ms"] - start
        if not 0 <= offset < duration:
            raise ValueError("Timestamp outside metadata capture bounds")
        buckets[offset // WINDOW_MS].append(row)

    summary, usable_rows = [], []
    null_counts = {key: 0 for key in SENSORS}
    invalid_counts = {key: 0 for key in SENSORS}
    for window_id, group in enumerate(buckets):
        begin = start + window_id * WINDOW_MS
        expected_times = set(range(begin, begin + WINDOW_MS, interval))
        actual_times = {row["timestamp_ms"] for row in group}
        missing = len(expected_times - actual_times)
        off_grid = len(actual_times - expected_times)
        seq_mismatch = sum(
            row["seq"] != (row["timestamp_ms"] - start) // interval + 1
            for row in group
        )
        nulls = invalid = 0
        for row in group:
            for key in SENSORS:
                value = row[key]
                if value is None:
                    nulls += 1
                    null_counts[key] += 1
                elif (type(value) not in (int, float)
                      or not math.isfinite(value)):
                    invalid += 1
                    invalid_counts[key] += 1
        reasons = []
        if missing:
            reasons.append("missing_samples")
        if off_grid:
            reasons.append("off_grid_timestamp")
        if seq_mismatch:
            reasons.append("seq_time_mismatch")
        if nulls:
            reasons.append("null_sensor_value")
        if invalid:
            reasons.append("invalid_sensor_value")
        usable = not reasons
        summary.append({
            "window_id": window_id,
            "elapsed_start_s": window_id * 2,
            "elapsed_end_s": window_id * 2 + 2,
            "start_timestamp_ms": begin,
            "end_timestamp_ms_exclusive": begin + WINDOW_MS,
            "expected_count": WINDOW_MS // interval,
            "actual_count": len(group),
            "missing_count": missing,
            "null_value_count": nulls,
            "invalid_value_count": invalid,
            "usable": usable,
            "reason": "|".join(reasons) if reasons else "ok",
        })
        if usable:
            usable_rows.extend({**row, "window_id": window_id} for row in group)

    report = {
        "device_id": meta["device_id"], "session_id": meta["session_id"],
        "user_id": meta["user_id"], "is_synthetic": True,
        "policy": "mock_strict_2s_no_imputation_v1",
        "expected_count": total_expected,
        "input_count": len(rows), "unique_count": len(ordered),
        "identical_duplicates_removed": duplicate_count,
        "missing_timestamp_slots": sum(w["missing_count"] for w in summary),
        "null_counts": null_counts, "invalid_counts": invalid_counts,
        "non_20ms_adjacent_intervals": sum(
            b["timestamp_ms"] - a["timestamp_ms"] != interval
            for a, b in zip(ordered, ordered[1:])
        ),
        "window_count": len(summary),
        "usable_windows": sum(w["usable"] for w in summary),
        "excluded_windows": sum(not w["usable"] for w in summary),
        "usable_rows": len(usable_rows),
        "excluded_existing_rows": len(ordered) - len(usable_rows),
    }
    return summary, usable_rows, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-file", action="store_true")
    args = parser.parse_args()
    meta = json.loads((ROOT / "mock_data/metadata.json").read_text(encoding="utf-8"))
    if args.source_file:
        rows = [json.loads(line) for line in
                (ROOT / "mock_data/sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        source = "local_file"
    else:
        rows = fetch(meta)
        source = "mysql_via_fastapi"
    summary, usable, report = process(rows, meta)
    report["source"] = source
    output = ROOT / "preprocessed" / source
    output.mkdir(parents=True, exist_ok=True)
    with (output / "window_quality.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    with (output / "usable_readings.jsonl").open("w", encoding="utf-8", newline="\n") as file:
        for row in usable:
            file.write(json.dumps(row, allow_nan=False) + "\n")
    (output / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (output / "source_metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Source: {source}")
    for key in ("input_count", "identical_duplicates_removed", "missing_timestamp_slots",
                "usable_windows", "excluded_windows", "usable_rows", "excluded_existing_rows"):
        print(f"{key}: {report[key]}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
