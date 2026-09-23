"""Inference on DB rows with frozen synthetic models; no training or label input."""
import hashlib
import io
import json

import joblib

from analyze import extract_features, GYRO_RAPID_CHANGE_THRESHOLD_DPS2
from analyze_by_sensor import FEATURE_GROUPS
from evaluate_sensor_rules import score_rows
from preprocess import process, FIELDS
from summarize_sensor_sessions import SOURCE, summarize_sensor

VERSION = "db_synthetic_session_1"


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def load_models(meta):
    policy_bytes = (SOURCE / "rule_policy.json").read_bytes()
    policy = json.loads(policy_bytes)
    if policy.get("is_synthetic") is not True:
        raise ValueError("Synthetic policy required")
    bundles, hashes = {}, {}
    for sensor in ("optical", "gyro"):
        # Only server-owned fixed paths; never load a client-supplied pickle.
        raw = (SOURCE / f"{sensor}_model_with_rule.joblib").read_bytes()
        bundle = joblib.load(io.BytesIO(raw))
        baseline = bundle["baseline"]
        if baseline.get("is_synthetic") is not True or baseline["sensor"] != sensor:
            raise ValueError("Invalid baseline")
        for key in ("user_id", "device_id", "schema_version", "optical_unit", "gyro_unit", "sample_rate_hz"):
            if baseline[key] != meta[key]:
                raise ValueError("Baseline identity/configuration mismatch")
        if baseline["feature_order"] != FEATURE_GROUPS[sensor] or baseline["gyro_rapid_change_threshold_dps2"] != GYRO_RAPID_CHANGE_THRESHOLD_DPS2:
            raise ValueError("Feature definition mismatch")
        if baseline.get("feature_definition_version") != "mean_std_gyro_events_v1" or baseline.get("std_ddof") != 0:
            raise ValueError("Unsupported feature statistics version")
        if bundle["additional_rule"] != policy["rules"][sensor]:
            raise ValueError("Rule policy mismatch")
        if meta["session_id"] in {s["session_id"] for s in baseline["train_sessions"]} | set(bundle["threshold_policy"]["calibration_session_ids"]):
            raise ValueError("Cannot evaluate a training/calibration session")
        bundles[sensor] = bundle
        hashes[sensor] = hashlib.sha256(raw).hexdigest()
    return bundles, {"rule_policy_sha256": hashlib.sha256(policy_bytes).hexdigest(), "model_sha256": hashes}


def analyze_rows(rows, meta, bundles, provenance):
    if meta.get("is_synthetic") is not True or meta["duration_seconds"] != 60:
        raise ValueError("Only synthetic 60-second sessions are supported")
    for row in rows:
        if any(row[k] != meta[k] for k in ("device_id", "session_id", "boot_id", "user_id")) or row["is_synthetic"] not in (True, 1):
            raise ValueError("DB identity mismatch")
    canonical = [{k: row[k] for k in FIELDS} for row in sorted(rows, key=lambda r: r["seq"])]
    quality, usable, report = process(rows, meta)
    result = {"mode": "synthetic_db_analysis", "pipeline_version": VERSION,
              "metadata": meta, "provenance": provenance,
              "input_sha256": hashlib.sha256(json_text(canonical).encode()).hexdigest(),
              "quality": report, "window_quality": quality, "feedback": None,
              "limitations": ["Synthetic data only; not validated on real sensors",
                              "Flagged duration is not a health risk probability",
                              "Window standard deviations are not a whole-session standard deviation",
                              "Rapid events are counted within each 2-second window"]}
    if report["excluded_windows"] or len(rows) != 3000 or report["usable_windows"] != 30:
        return {**result, "status": "insufficient_data", "summary": None, "features": [], "scores": []}
    features = [{"session_id": meta["session_id"], "boot_id": meta["boot_id"], **row}
                for row in extract_features(usable, [{k: str(v) for k, v in w.items()} for w in quality])]
    summary = {k: meta[k] for k in ("user_id", "device_id", "session_id", "boot_id", "is_synthetic")}
    summary["sensors"] = {}
    scores = []
    for sensor, bundle in bundles.items():
        predictions = score_rows(features, bundle, bundle["additional_rule"])
        scores.extend({"sensor": sensor, **row} for row in predictions)
        csv_rows = [{k: str(v) for k, v in row.items()} for row in predictions]
        summary["sensors"][sensor] = summarize_sensor(csv_rows, features, meta, bundle)
    return {**result, "status": "analyzed", "features": features, "scores": scores, "summary": summary}
