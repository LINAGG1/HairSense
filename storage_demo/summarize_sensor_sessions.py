"""가상 세션의 구간 판정을 수치로 요약합니다. 세션 위험 등급은 만들지 않습니다.

현재 입력은 누락 없는 60초/단일 부팅 세션 전용입니다.
평가 정답(expected_anomaly/scenario)은 요약 계산에 사용하지 않습니다.
"""

import hashlib
import json
import math
from collections import Counter

import joblib

from analyze import ROOT, write_csv
from analyze_by_sensor import write_json
from analyze_sessions import read_csv


SOURCE = ROOT / "analysis_results" / "multi_session" / "rules_holdout_002"
DATASET = ROOT / "mock_sessions" / "holdout_002"


def flag(value):
    if value not in ("True", "False"):
        raise ValueError(f"Invalid boolean: {value}")
    return value == "True"


def anomaly_intervals(rows):
    """인접한 이상 구간만 연결합니다. 시간 공백이나 부팅 경계에서는 분리합니다."""
    spans = []
    for row in sorted(rows, key=lambda r: float(r["elapsed_start_s"])):
        if not flag(row["combined_flag"]):
            continue
        start, end = float(row["elapsed_start_s"]), float(row["elapsed_end_s"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("Invalid interval")
        if spans and spans[-1]["boot_id"] == row["boot_id"] and spans[-1]["end_s"] == start:
            spans[-1]["end_s"] = end
            spans[-1]["duration_s"] = end - spans[-1]["start_s"]
        else:
            spans.append({"boot_id": row["boot_id"], "start_s": start, "end_s": end,
                          "duration_s": end - start})
    return spans


def summarize_sensor(rows, features, meta, bundle):
    duration = meta["duration_seconds"]
    expected = int(duration / 2)
    # 부분 데이터의 비율을 전체 세션의 비율로 오해하지 않도록 현재 범위를 제한합니다.
    if duration != 60 or len(rows) != expected or len(features) != expected:
        raise ValueError("This demo requires a complete 60-second session")
    for collection in (rows, features):
        if {int(r["window_id"]) for r in collection} != set(range(expected)):
            raise ValueError("Missing or duplicate windows")
        for row in collection:
            wid = int(row["window_id"])
            if row["session_id"] != meta["session_id"] or row["boot_id"] != meta["boot_id"]:
                raise ValueError("Session/boot mismatch")
            if float(row["elapsed_start_s"]) != wid * 2 or float(row["elapsed_end_s"]) != wid * 2 + 2:
                raise ValueError("Unexpected window timing")
    for row in rows:
        if flag(row["combined_flag"]) != (flag(row["model_flag"]) or flag(row["rule_flag"])):
            raise ValueError("Inconsistent combined flag")

    baseline = bundle["baseline"]
    aggregates = {}
    for key in baseline["feature_order"]:
        values = [float(r[key]) for r in features]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite feature")
        current = sum(values) / len(values)
        reference = float(baseline["feature_mean"][key])
        aggregates[key] = {"mean_of_window_values": current,
                           "baseline_mean_of_window_values": reference,
                           "delta": current - reference}
    spans = anomaly_intervals(rows)
    anomalous = sum(flag(r["combined_flag"]) for r in rows)
    reasons = Counter(r["rule_reason"] for r in rows if flag(r["rule_flag"]))
    allowed = {"optical_mean_increased", "optical_mean_decreased"} if baseline["sensor"] == "optical" else {"gyro_rapid_changes_increased"}
    if set(reasons) - allowed:
        raise ValueError("Unknown rule reason")
    result = {
        "status": "summary_only", "session_verdict": None,
        "expected_windows": expected, "valid_windows": len(rows), "valid_duration_s": duration,
        "flagged_windows": anomalous, "flagged_duration_s": anomalous * 2,
        "flagged_duration_fraction": anomalous / len(rows),
        "flagged_duration_percent": round(anomalous / len(rows) * 100, 2),
        "longest_flagged_run_s": max((s["duration_s"] for s in spans), default=0.0),
        "flagged_intervals": spans,
        "model_flagged_windows": sum(flag(r["model_flag"]) for r in rows),
        "rule_flagged_windows": sum(flag(r["rule_flag"]) for r in rows),
        "rule_reason_windows": dict(reasons),
        "window_feature_aggregates": aggregates,
        "model_threshold": bundle["threshold_policy"]["threshold"],
        "additional_rule": bundle["additional_rule"],
    }
    if baseline["sensor"] == "gyro":
        result["within_window_rapid_event_count_sum"] = int(sum(
            float(row["gyro_rapid_change_count"]) for row in features))
        result["rapid_event_count_note"] = "Sum of within-window counts; a boundary-spanning event may be counted twice"
    return result


def main():
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("is_synthetic") is not True:
        raise ValueError("Synthetic holdout required")
    policy_path = SOURCE / "rule_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    evaluation = json.loads((SOURCE / "run_summary.json").read_text(encoding="utf-8"))
    policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    if evaluation["rule_policy_sha256"] != policy_hash:
        raise ValueError("Evaluation policy has changed")
    results = read_csv(SOURCE / "holdout_results.csv")
    features = read_csv(SOURCE / "holdout_features.csv")
    bundles = {sensor: joblib.load(SOURCE / f"{sensor}_model_with_rule.joblib")
               for sensor in ("optical", "gyro")}
    for sensor, bundle in bundles.items():
        if bundle["additional_rule"] != policy["rules"][sensor]:
            raise ValueError("Rule bundle mismatch")
    expected_ids = {e["session_id"] for e in manifest["sessions"]}
    if len(expected_ids) != len(manifest["sessions"]):
        raise ValueError("Duplicate manifest session")
    if {r["session_id"] for r in results} != expected_ids or {r["session_id"] for r in features} != expected_ids:
        raise ValueError("Manifest/data mismatch")
    if {r["sensor"] for r in results} != set(bundles):
        raise ValueError("Unexpected sensor results")
    reports, flat_rows = [], []
    for entry in manifest["sessions"]:
        folder = (DATASET / entry["directory"]).resolve()
        if not folder.is_relative_to(DATASET.resolve()):
            raise ValueError("Session path outside dataset")
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        if meta.get("is_synthetic") is not True or any(meta[k] != entry[k] for k in ("session_id", "boot_id")):
            raise ValueError("Invalid session metadata")
        report = {key: meta[key] for key in ("session_id", "boot_id", "user_id", "device_id", "is_synthetic")}
        report["sensors"] = {}
        for sensor, bundle in bundles.items():
            baseline = bundle["baseline"]
            if any(meta[key] != baseline[key] for key in ("user_id", "device_id", "schema_version", "optical_unit", "gyro_unit", "sample_rate_hz")):
                raise ValueError("Baseline identity mismatch")
            selected = [r for r in results if r["session_id"] == entry["session_id"] and r["sensor"] == sensor]
            values = [r for r in features if r["session_id"] == entry["session_id"]]
            if any(float(r["model_threshold"]) != bundle["threshold_policy"]["threshold"] or
                   float(r["rule_threshold"]) != bundle["additional_rule"]["threshold"] for r in selected):
                raise ValueError("Result threshold mismatch")
            sensor_report = summarize_sensor(selected, values, meta, bundle)
            report["sensors"][sensor] = sensor_report
            flat_rows.append({"session_id": meta["session_id"], "sensor": sensor,
                              **{k: sensor_report[k] for k in (
                                  "valid_windows", "valid_duration_s", "flagged_windows",
                                  "flagged_duration_s", "flagged_duration_percent", "longest_flagged_run_s",
                                  "model_flagged_windows", "rule_flagged_windows")}})
        reports.append(report)
    write_json(SOURCE / "session_summaries.json", {
        "summary_version": "session_summary_demo_1", "is_synthetic": True,
        "rule_policy_sha256": policy_hash, "sessions": reports,
        "limitations": [
            "No validated session verdict; flagged duration is not a health risk probability",
            "Complete 60-second single-boot synthetic sessions only; missing-data summaries not implemented",
            "Mean of window standard deviations is not a whole-session standard deviation",
            "Feature deltas compare session-average window features to training window averages; no session z-score",
            "Ground truth and scenario labels are not used to create summaries",
        ],
    })
    write_csv(SOURCE / "session_summary.csv", flat_rows, list(flat_rows[0]))
    print(f"sessions_summarized: {len(reports)}")
    print(f"sensor_summaries: {len(flat_rows)}")
    for row in flat_rows:
        print(f"{row['session_id']} {row['sensor']}: flagged={row['flagged_windows']}/30, "
              f"duration={row['flagged_duration_percent']:.2f}%, longest={row['longest_flagged_run_s']:.0f}s")
    print(f"Output: {SOURCE}")


if __name__ == "__main__":
    main()
