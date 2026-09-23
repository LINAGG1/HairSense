"""다중 가상 세션 분석: train 학습 → calibration 임계값 고정 → test 평가."""

import csv
import hashlib
import json

import joblib
import numpy as np
import sklearn

from analyze import ROOT, GYRO_RAPID_CHANGE_THRESHOLD_DPS2, extract_features, write_csv
from analyze_by_sensor import FEATURE_GROUPS, fit_sensor, write_json
from preprocess import process


DATASET = ROOT / "mock_sessions" / "experiment_001"
OUTPUT = ROOT / "analysis_results" / "multi_session" / "experiment_001"
# 평가 결과를 보기 전에 고정하는 개발용 설정. 실제 사용자 기준은 아닙니다.
CALIBRATION_QUANTILE = 0.95


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def validate_splits(entries):
    parts = {name: [e for e in entries if e["split"] == name]
             for name in ("train", "calibration", "test")}
    if {name: len(items) for name, items in parts.items()} != {
        "train": 12, "calibration": 6, "test": 6,
    } or sum(map(len, parts.values())) != len(entries):
        raise ValueError("Expected 12 train, 6 calibration and 6 test sessions")
    for key in ("session_id", "boot_id", "directory", "session_order"):
        if len({e[key] for e in entries}) != len(entries):
            raise ValueError(f"Repeated session identity: {key}")
    if not (max(e["session_order"] for e in parts["train"])
            < min(e["session_order"] for e in parts["calibration"])
            <= max(e["session_order"] for e in parts["calibration"])
            < min(e["session_order"] for e in parts["test"])):
        raise ValueError("Sessions must be split in chronological order")
    return parts


def session_folder(entry):
    folder = (DATASET / entry["directory"]).resolve()
    if not folder.is_relative_to(DATASET.resolve()):
        raise ValueError("Session path outside dataset")
    return folder


def load_features(entries):
    """시나리오/정답 라벨을 읽지 않고 센서 데이터만 전처리합니다."""
    features, quality_reports, identities = [], [], set()
    for entry in entries:
        folder = session_folder(entry)
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        if meta.get("is_synthetic") is not True:
            raise ValueError("This script only supports synthetic sessions")
        for key in ("session_id", "boot_id", "split", "session_order"):
            if meta[key] != entry[key]:
                raise ValueError(f"Manifest/metadata mismatch: {key}")
        identities.add((meta["user_id"], meta["device_id"], meta["schema_version"],
                        meta["optical_unit"], meta["gyro_unit"], meta["sample_rate_hz"]))
        rows = [json.loads(line) for line in
                (folder / "sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        quality, usable, report = process(rows, meta)
        # 이 실험은 완전한 60초 세션 전용입니다. 품질 문제를 조용히 제외하지 않습니다.
        if report["excluded_windows"] or report["usable_windows"] != 30:
            raise ValueError(f"Incomplete session: {entry['session_id']}")
        csv_quality = [{key: str(value) for key, value in row.items()} for row in quality]
        for row in extract_features(usable, csv_quality):
            features.append({
                "session_id": meta["session_id"], "boot_id": meta["boot_id"],
                "split": meta["split"], **row,
            })
        quality_reports.append({"session_id": meta["session_id"], **report})
    if len(identities) != 1:
        raise ValueError("Mixed users, devices or sensor configurations")
    return features, quality_reports, next(iter(identities))


def feature_matrix(rows, keys):
    return np.array([[row[key] for key in keys] for row in rows], dtype=float)


def metrics(rows):
    tp = sum(r["expected_anomaly"] and r["is_anomaly"] for r in rows)
    fp = sum(not r["expected_anomaly"] and r["is_anomaly"] for r in rows)
    fn = sum(r["expected_anomaly"] and not r["is_anomaly"] for r in rows)
    tn = sum(not r["expected_anomaly"] and not r["is_anomaly"] for r in rows)
    divide = lambda a, b: a / b if b else None
    return {"windows": len(rows), "true_positive": tp, "false_positive": fp,
            "false_negative": fn, "true_negative": tn,
            "precision": divide(tp, tp + fp), "recall": divide(tp, tp + fn),
            "false_positive_rate": divide(fp, fp + tn),
            "false_negative_rate": divide(fn, tp + fn)}


def main():
    manifest_path = DATASET / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("is_synthetic") is not True:
        raise ValueError("Synthetic manifest required")
    parts = validate_splits(manifest["sessions"])
    train, train_quality, train_identity = load_features(parts["train"])
    calibration, calibration_quality, calibration_identity = load_features(parts["calibration"])
    if train_identity != calibration_identity:
        raise ValueError("Train/calibration identity mismatch")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bundles, calibration_rows = {}, []
    thresholds = {}

    # 아직 test 센서/라벨을 읽지 않은 상태에서 학습·임계값 저장을 완료합니다.
    for sensor, keys in FEATURE_GROUPS.items():
        model, means, stds = fit_sensor(train, keys)
        scores = -model.decision_function(feature_matrix(calibration, keys))
        threshold = float(np.quantile(scores, CALIBRATION_QUANTILE, method="higher"))
        baseline = {
            "is_synthetic": True, "user_id": train_identity[0], "device_id": train_identity[1],
            "schema_version": train_identity[2], "optical_unit": train_identity[3],
            "gyro_unit": train_identity[4], "sample_rate_hz": train_identity[5],
            "sensor": sensor, "feature_order": keys, "reference_windows": len(train),
            "train_sessions": [{"session_id": e["session_id"], "boot_id": e["boot_id"]}
                               for e in parts["train"]],
            "feature_mean": dict(zip(keys, means.tolist())),
            "feature_std": dict(zip(keys, stds.tolist())), "std_ddof": 0,
            "constant_reference_features": [key for key, std in zip(keys, stds) if std <= 1e-12],
            "feature_definition_version": "mean_std_gyro_events_v1",
            "gyro_rapid_change_threshold_dps2": GYRO_RAPID_CHANGE_THRESHOLD_DPS2,
            "gyro_rapid_change_count_scope": "within_each_2s_window",
            "note": "Synthetic one-user baseline; not validated on real sensor data",
        }
        policy = {
            "anomaly_score_definition": "-decision_function",
            "threshold": threshold, "comparison": "strictly_greater_than",
            "quantile": CALIBRATION_QUANTILE, "quantile_method": "higher",
            "calibration_windows": len(calibration),
            "calibration_session_ids": [e["session_id"] for e in parts["calibration"]],
            "calibration_flagged_windows": int(np.count_nonzero(scores > threshold)),
            "note": "Empirical window threshold; not a probability or guaranteed future error rate",
        }
        bundle = {"model": model, "baseline": baseline, "threshold_policy": policy,
                  "sklearn_version": sklearn.__version__}
        bundles[sensor] = bundle
        thresholds[sensor] = policy
        joblib.dump(bundle, OUTPUT / f"{sensor}_model.joblib")
        write_json(OUTPUT / f"{sensor}_baseline.json", baseline)
        for row, score in zip(calibration, scores):
            calibration_rows.append({
                "session_id": row["session_id"], "boot_id": row["boot_id"],
                "window_id": row["window_id"], "sensor": sensor,
                "anomaly_score": float(score), "threshold": threshold,
                "is_anomaly": bool(score > threshold),
            })
    write_json(OUTPUT / "thresholds.json", thresholds)
    write_csv(OUTPUT / "calibration_scores.csv", calibration_rows, list(calibration_rows[0]))

    test, test_quality, test_identity = load_features(parts["test"])
    if test_identity != train_identity:
        raise ValueError("Test identity differs from training identity")
    # 정답은 모델 입력에 포함하지 않고 고정된 모델의 평가에만 사용합니다.
    labels = {}
    for entry in parts["test"]:
        session_labels = read_csv(session_folder(entry) / "window_labels.csv")
        if len(session_labels) != 30 or {int(r["window_id"]) for r in session_labels} != set(range(30)):
            raise ValueError("Incomplete or duplicate evaluation labels")
        for row in session_labels:
            if any(row[f"{sensor}_injected_anomaly"] not in ("0", "1") for sensor in FEATURE_GROUPS):
                raise ValueError("Invalid evaluation label")
            labels[(entry["session_id"], int(row["window_id"]))] = row

    evaluation, changes = [], []
    for sensor, bundle in bundles.items():
        baseline = bundle["baseline"]
        keys = baseline["feature_order"]
        scores = -bundle["model"].decision_function(feature_matrix(test, keys))
        threshold = bundle["threshold_policy"]["threshold"]
        for row, score in zip(test, scores):
            label = labels[(row["session_id"], row["window_id"])]
            evaluation.append({
                "session_id": row["session_id"], "boot_id": row["boot_id"],
                "window_id": row["window_id"], "elapsed_start_s": row["elapsed_start_s"],
                "elapsed_end_s": row["elapsed_end_s"], "sensor": sensor,
                "anomaly_score": float(score), "threshold": threshold,
                "score_margin": float(score - threshold), "is_anomaly": bool(score > threshold),
                "expected_anomaly": label[f"{sensor}_injected_anomaly"] == "1",
                "scenario": label["scenario"],
            })
            for key in keys:
                mean, std = baseline["feature_mean"][key], baseline["feature_std"][key]
                value = float(row[key])
                delta = value - mean
                changes.append({
                    "session_id": row["session_id"], "boot_id": row["boot_id"],
                    "window_id": row["window_id"], "sensor": sensor, "feature": key,
                    "current_value": value, "baseline_mean": mean, "baseline_std": std,
                    "delta": delta, "standardized_change": delta / std if std > 1e-12 else "",
                    "standardized_change_available": std > 1e-12,
                })
    per_session = []
    for sensor in FEATURE_GROUPS:
        for entry in parts["test"]:
            selected = [r for r in evaluation if r["sensor"] == sensor and r["session_id"] == entry["session_id"]]
            per_session.append({"sensor": sensor, "session_id": entry["session_id"], **metrics(selected)})
    summary = {
        "is_synthetic": True, "dataset_version": manifest["dataset_version"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "train_windows": len(train), "calibration_windows": len(calibration), "test_windows": len(test),
        "calibration_quantile": CALIBRATION_QUANTILE,
        "test_metrics": {sensor: metrics([r for r in evaluation if r["sensor"] == sensor])
                         for sensor in FEATURE_GROUPS},
        "split_session_ids": {name: [e["session_id"] for e in entries] for name, entries in parts.items()},
        "numpy_version": np.__version__, "sklearn_version": sklearn.__version__,
        "model_settings": {"n_estimators": 200, "max_samples": "auto", "contamination": "auto",
                           "random_state": 42, "n_jobs": 1},
        "limitations": [
            "One simulated user only; this is not evidence of real-world accuracy",
            "Adjacent windows are correlated; calibration contains only 6 independent sessions",
            "Sensor-specific quality handling and real timestamp jitter are not implemented here",
            "Constant rapid-count features cannot split trees; inspect their absolute delta separately",
            "The test set must not be used to choose thresholds; use a new holdout after tuning",
        ],
    }
    feature_rows = train + calibration + test
    write_csv(OUTPUT / "features.csv", feature_rows, list(feature_rows[0]))
    write_csv(OUTPUT / "test_results.csv", evaluation, list(evaluation[0]))
    write_csv(OUTPUT / "test_feature_changes.csv", changes, list(changes[0]))
    write_csv(OUTPUT / "test_session_metrics.csv", per_session, list(per_session[0]))
    write_json(OUTPUT / "quality_reports.json", train_quality + calibration_quality + test_quality)
    write_json(OUTPUT / "run_summary.json", summary)
    for key in ("train_windows", "calibration_windows", "test_windows"):
        print(f"{key}: {summary[key]}")
    for sensor, result in summary["test_metrics"].items():
        print(f"{sensor}_threshold: {thresholds[sensor]['threshold']:.6f}")
        print(f"{sensor}: TP={result['true_positive']} FP={result['false_positive']} "
              f"FN={result['false_negative']} TN={result['true_negative']}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
