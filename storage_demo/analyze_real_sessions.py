"""Real, single-user/device baseline training from exported capture sessions.

No synthetic rapid-change threshold, imputation, or inferred ground truth.
"""
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import sklearn

from analyze import write_csv
from analyze_by_sensor import fit_sensor, write_json

CHANNELS = {"optical": ("optical",), "gyro": ("gyro_x", "gyro_y", "gyro_z")}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Session path outside dataset")
    return path


def features(rows, meta, sensor):
    """Conservative 2 s windows: 100 samples, each within 5 ms of its 50 Hz slot."""
    start, end = meta["start_timestamp_ms"], meta["end_timestamp_ms"]
    if any(type(v) is not int or v < 0 for v in (start, end)) or not 0 < end-start <= 600000:
        raise ValueError("Invalid capture bounds")
    if meta.get("is_synthetic") is not False or meta.get("schema_version") != 1:
        raise ValueError("Real schema version 1 metadata required")
    if meta.get("sample_rate_hz") != 50:
        raise ValueError("Real preprocessing currently supports 50 Hz only")
    if meta.get("sample_type") != {"optical": "camera_optical", "gyro": "gyro"}[sensor]:
        raise ValueError("Sensor/sample_type mismatch")
    if meta.get("optical_unit" if sensor == "optical" else "gyro_unit") != ("V" if sensor == "optical" else "deg/s"):
        raise ValueError("Unsupported sensor unit")
    unique = {}
    for row in rows:
        if row.get("boot_id") != meta["boot_id"] or row.get("schema_version") != 1:
            raise ValueError("Reading boot/schema mismatch")
        for key in ("device_id", "session_id", "user_id"):
            if key in row and row[key] != meta[key]:
                raise ValueError(f"Reading identity mismatch: {key}")
        if "is_synthetic" in row and row["is_synthetic"] not in (False, 0):
            raise ValueError("Synthetic reading in real dataset")
        for key in ("seq", "timestamp_ms"):
            if type(row.get(key)) is not int or row[key] < 0:
                raise ValueError(f"Invalid {key}")
        if not start <= row["timestamp_ms"] < end:
            raise ValueError("Reading outside capture bounds")
        if row["seq"] in unique and unique[row["seq"]] != row:
            raise ValueError("Conflicting duplicate sequence")
        unique[row["seq"]] = row
    ordered = sorted(unique.values(), key=lambda r: r["seq"])
    if any(b["timestamp_ms"] <= a["timestamp_ms"] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Repeated or backward timestamps")
    buckets = {}
    for row in ordered:
        buckets.setdefault((row["timestamp_ms"]-start)//2000, []).append(row)
    result, quality = [], []
    for wid in range(math.ceil((end-start)/2000)):
        group = buckets.get(wid, [])
        reasons = []
        if start+(wid+1)*2000 > end:
            reasons.append("partial_window")
        if len(group) != 100:
            reasons.append("sample_count")
        elif any(abs(r["timestamp_ms"]-(start+wid*2000+i*20)) > 5 for i, r in enumerate(group)):
            reasons.append("timestamp_jitter_or_missing_slot")
        for channel in CHANNELS[sensor]:
            if any(type(r.get(channel)) not in (int, float) or not math.isfinite(r[channel]) for r in group):
                reasons.append(f"invalid_or_missing_{channel}")
        quality.append({"window_id": wid, "sample_count": len(group), "reasons": reasons})
        if reasons:
            continue
        record = {"session_id": meta["session_id"], "boot_id": meta["boot_id"], "window_id": wid}
        for channel in CHANNELS[sensor]:
            values = np.array([r[channel] for r in group])
            record[f"{channel}_mean"] = float(values.mean())
            record[f"{channel}_std"] = float(values.std())
        result.append(record)
    return result, {"session_id": meta["session_id"], "boot_id": meta["boot_id"],
                    "input_count": len(rows), "duplicates_removed": len(rows)-len(ordered),
                    "usable_windows": len(result), "excluded_windows": len(quality)-len(result),
                    "windows": quality}


def run(dataset, output):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new output directory; existing results are preserved")
    manifest_path = dataset / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("is_synthetic") is not False:
        raise ValueError("Real manifest must contain is_synthetic: false")
    sensor = manifest.get("sensor")
    if sensor not in CHANNELS:
        raise ValueError("Manifest sensor must be optical or gyro")
    entries = manifest["sessions"]
    parts = {name: [] for name in ("train", "calibration", "test")}
    identities, seen, folders, orders, quality = set(), set(), set(), set(), []
    for entry in entries:
        split = entry["split"]
        if split not in parts:
            raise ValueError("Invalid split")
        if type(entry["session_order"]) is not int or entry["session_order"] in orders:
            raise ValueError("Session order must be a unique integer")
        orders.add(entry["session_order"])
        folder = inside(dataset, entry["directory"])
        meta = read_json(folder / "metadata.json")
        identity = tuple(meta[k] for k in ("user_id", "device_id"))
        identities.add(identity)
        key = (meta["device_id"], meta["session_id"], meta["boot_id"])
        if key in seen or folder in folders:
            raise ValueError("Session reused across dataset splits")
        seen.add(key)
        folders.add(folder)
        for field in ("session_id", "boot_id"):
            if entry[field] != meta[field]:
                raise ValueError(f"Manifest/metadata mismatch: {field}")
        if split in ("train", "calibration") and entry.get("baseline_normal") is not True:
            raise ValueError("Train/calibration sessions must be confirmed baseline_normal")
        rows = [json.loads(line) for line in (folder / "sensor_readings.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        session_features, report = features(rows, meta, sensor)
        if not session_features:
            raise ValueError(f"No usable windows in {meta['session_id']}: {report}")
        parts[split].extend({**r, "split": split} for r in session_features)
        quality.append(report)
    if len(identities) != 1:
        raise ValueError("Use one user/device per real baseline")
    if not all(parts.values()):
        raise ValueError("At least one usable session in each split is required")
    split_orders = {name: [e["session_order"] for e in entries if e["split"] == name] for name in parts}
    if not max(split_orders["train"]) < min(split_orders["calibration"]) <= max(split_orders["calibration"]) < min(split_orders["test"]):
        raise ValueError("Expected chronological train, calibration, test sessions")
    keys = [f"{c}_{stat}" for c in CHANNELS[sensor] for stat in ("mean", "std")]
    if len(parts["train"]) < 2:
        raise ValueError("At least two training windows required")
    model, means, stds = fit_sensor(parts["train"], keys)
    matrix = lambda rows: np.array([[r[k] for k in keys] for r in rows])
    calibration_scores = -model.decision_function(matrix(parts["calibration"]))
    threshold = float(np.quantile(calibration_scores, 0.95, method="higher"))
    user, device = next(iter(identities))
    baseline = {"is_synthetic": False, "user_id": user, "device_id": device,
                "sensor": sensor, "schema_version": 1, "sample_rate_hz": 50,
                "unit": "V" if sensor == "optical" else "deg/s",
                "feature_order": keys, "feature_definition_version": "real_mean_std_v1",
                "feature_mean": dict(zip(keys, means.tolist())),
                "feature_std": dict(zip(keys, stds.tolist())),
                "reference_windows": len(parts["train"])}
    policy = {"threshold": threshold, "quantile": 0.95, "quantile_method": "higher",
              "comparison": "strictly_greater_than", "anomaly_score_definition": "-decision_function"}
    scores = []
    for split in ("calibration", "test"):
        for row, score in zip(parts[split], -model.decision_function(matrix(parts[split]))):
            scores.append({**{k: row[k] for k in ("session_id", "boot_id", "window_id", "split")},
                           "anomaly_score": float(score), "threshold": threshold,
                           "is_anomaly": bool(score > threshold)})
    output.mkdir(parents=True, exist_ok=False)
    joblib.dump({"model": model, "baseline": baseline, "threshold_policy": policy,
                 "sklearn_version": sklearn.__version__}, output / f"{sensor}_model.joblib")
    write_json(output / f"{sensor}_baseline.json", baseline)
    write_json(output / "thresholds.json", policy)
    write_json(output / "quality_reports.json", quality)
    write_json(output / "run_summary.json", {
        "is_synthetic": False, "sensor": sensor,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "sessions": entries, "windows": {k: len(v) for k, v in parts.items()},
        "test_metrics": None,
        "limitations": ["No verified test labels: accuracy is not calculated",
                        "Single user/device baseline; not validated for deployment",
                        "50 Hz, 2 second windows, maximum 5 ms slot deviation; no imputation",
                        "Small or correlated baseline samples cannot establish generalization",
                        "Existing synthetic inference API does not load this real model"]})
    all_features = sum(parts.values(), [])
    write_csv(output / "features.csv", all_features, list(all_features[0]))
    write_csv(output / "scores.csv", scores, list(scores[0]))
    print(f"Real {sensor} model saved: {output}")
