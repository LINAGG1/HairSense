"""Chronological, single-owner sensor learning. No image or synthetic inference.

State contains reproducible training features instead of executable model pickles.
Isolation Forest is deterministically reconstructed from that state on each update.
"""
import copy

import numpy as np
import sklearn

from analyze_by_sensor import fit_sensor
from analyze_real_sessions import CHANNELS, features

VERSION = "real_online_mean_std_v1"


def empty_state():
    return {"version": 0, "train": [], "calibration": [], "baseline": None}


def feature_keys(sensor):
    return [f"{channel}_{stat}" for channel in CHANNELS[sensor] for stat in ("mean", "std")]


def matrix(rows, keys):
    return np.array([[row[key] for key in keys] for row in rows], dtype=float)


def deviation(values, means, stds):
    # A constant reference channel must still detect a new, nonconstant value.
    scale = np.maximum(stds, np.maximum(np.abs(means), 1.0) * 1e-6)
    return np.max(np.abs(values - means) / scale, axis=1)


def build_baseline(state, sensor):
    keys = feature_keys(sensor)
    train = [row for session in state["train"] for row in session["features"]]
    if len(train) < 2:
        return None, None
    model, means, stds = fit_sensor(train, keys)
    calibration = [row for session in state["calibration"] for row in session["features"]]
    reference = matrix(calibration or train, keys)
    threshold = float(np.quantile(-model.decision_function(reference), .95, method="higher"))
    rule_threshold = float(np.quantile(deviation(reference, means, stds), .95, method="higher"))
    baseline = {
        "version": state["version"], "sensor": sensor, "is_synthetic": False,
        "sklearn_version": sklearn.__version__, "model_parameters": model.get_params(),
        "feature_order": keys, "feature_mean": dict(zip(keys, means.tolist())),
        "feature_std": dict(zip(keys, stds.tolist())), "reference_windows": len(train),
        "train_sessions": [s["identity"] for s in state["train"]],
        "calibration_sessions": [s["identity"] for s in state["calibration"]],
        "threshold": threshold, "rule_threshold": rule_threshold,
        "threshold_status": "calibrated" if calibration else "provisional",
        "threshold_policy": "95th percentile, higher; strictly greater than",
        "additional_rule": "maximum absolute standardized feature deviation",
        "scale_floor": "max(feature_std, max(abs(feature_mean), 1) * 1e-6)",
    }
    return model, baseline


def advance(previous, rows, meta, sensor, role="measurement"):
    """Evaluate against history BEFORE admitting this session to the next model."""
    if role not in ("measurement", "calibration"):
        raise ValueError("Invalid session role")
    state = copy.deepcopy(previous)
    extracted, quality = features(rows, meta, sensor)
    identity = {k: meta[k] for k in ("device_id", "user_id", "session_id", "boot_id")}
    for session in state["train"] + state["calibration"]:
        if any(session["identity"][k] != identity[k] for k in ("device_id", "user_id")):
            raise ValueError("Baseline owner mismatch")
        if session["identity"] == identity:
            raise ValueError("Session already admitted")
    model, baseline = build_baseline(state, sensor)
    result = {"pipeline_version": VERSION, "mode": "real_online_analysis", "is_synthetic": False,
              "sensor": sensor, "metadata": {k: v for k, v in meta.items() if k != "wire_payload"},
              "role": role, "quality": quality,
              "features": extracted, "scores": [], "compared_baseline": baseline,
              "feedback": None, "limitations": [
                  "Anomaly scores are relative differences, not health probabilities",
                  "Unlabelled measurements are included after scoring as requested; long-term drift may enter the baseline",
                  "Thresholds are provisional until a separate calibration capture is supplied",
                  "Only complete 2-second 50 Hz windows with <=5 ms slot jitter are used; no imputation"]}
    if not extracted:
        result["status"] = "insufficient_data"
    elif role == "calibration" and model is None:
        result["status"] = "baseline_required"
    else:
        if model is not None and role == "measurement":
            keys = feature_keys(sensor)
            values = matrix(extracted, keys)
            means = np.array([baseline["feature_mean"][k] for k in keys])
            stds = np.array([baseline["feature_std"][k] for k in keys])
            for row, score, rule in zip(extracted, -model.decision_function(values), deviation(values, means, stds)):
                result["scores"].append({"window_id": row["window_id"], "anomaly_score": float(score),
                    "threshold": baseline["threshold"], "rule_score": float(rule),
                    "rule_threshold": baseline["rule_threshold"],
                    "model_flag": bool(score > baseline["threshold"]),
                    "rule_flag": bool(rule > baseline["rule_threshold"]),
                    "is_anomaly": bool(score > baseline["threshold"] or rule > baseline["rule_threshold"])})
            result["status"] = "completed"
        else:
            result["status"] = "calibration_completed" if role == "calibration" else "baseline_created"
        # Calibration captures NEVER enter Isolation Forest fitting/statistics.
        destination = "calibration" if role == "calibration" else "train"
        state[destination].append({"identity": identity, "features": extracted})
        state["version"] += 1
        _, state["baseline"] = build_baseline(state, sensor)
        if state["baseline"] is None:
            result["status"] = "baseline_collecting"
    result["next_baseline_version"] = state["version"]
    result["next_threshold_status"] = (state["baseline"] or {}).get("threshold_status", "not_ready")
    flagged = sum(row["is_anomaly"] for row in result["scores"])
    result["summary"] = {**identity, "sensors": {sensor: {
        "status": result["status"], "session_id": meta["session_id"], "boot_id": meta["boot_id"],
        "usable_windows": len(extracted), "excluded_windows": quality["excluded_windows"],
        "compared_baseline_version": baseline["version"] if baseline else None,
        "reference_windows": baseline["reference_windows"] if baseline else 0,
        "threshold_status": baseline["threshold_status"] if baseline else "not_ready",
        "threshold": baseline["threshold"] if baseline else None,
        "rule_threshold": baseline["rule_threshold"] if baseline else None,
        "flagged_windows": flagged, "evaluated_windows": len(result["scores"]),
        "flagged_seconds": flagged * 2,
        "mean_anomaly_score": float(np.mean([r["anomaly_score"] for r in result["scores"]])) if result["scores"] else None,
        "feature_comparison": {key: {
            "current_mean": float(np.mean([r[key] for r in extracted])),
            "baseline_mean": baseline["feature_mean"][key] if baseline else None,
            "difference": float(np.mean([r[key] for r in extracted])) - baseline["feature_mean"][key] if baseline else None,
        } for key in feature_keys(sensor)} if extracted else {},
    }}}
    return state, result
