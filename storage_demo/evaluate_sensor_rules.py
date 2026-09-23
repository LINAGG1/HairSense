"""고정된 IF 모델에 설명 가능한 규칙을 추가하고 새 가상 세션에서 비교합니다.

실행 순서: calibration으로 규칙 저장 → 새 holdout 생성 → 동일 데이터에서 비교.
재실행은 동일 난수의 재현이며, 추가적인 독립 성능 검증이 아닙니다.
"""

import hashlib
import json
import sys

import joblib
import numpy as np

from analyze import ROOT, extract_features, write_csv
from analyze_by_sensor import write_json
from analyze_sessions import (
    DATASET, OUTPUT as MODEL_DIR, feature_matrix, load_features,
    metrics, read_csv, validate_splits,
)
from preprocess import process


OUTPUT = ROOT / "analysis_results" / "multi_session" / "rules_holdout_002"
HOLDOUT = ROOT / "mock_sessions" / "holdout_002"
HOLDOUT_SEED = 20261001
RULE_QUANTILE = 0.95


def model_identity(bundle):
    b = bundle["baseline"]
    return tuple(b[k] for k in (
        "user_id", "device_id", "schema_version", "optical_unit", "gyro_unit", "sample_rate_hz",
    ))


def build_rule(sensor, calibration, bundle):
    """판정 기준은 calibration 특징에서만 계산합니다."""
    key = "optical_mean" if sensor == "optical" else "gyro_rapid_change_count"
    mean = float(bundle["baseline"]["feature_mean"][key])
    raw = np.array([r[key] for r in calibration], dtype=float)
    values = np.abs(raw - mean) if sensor == "optical" else raw
    return {
        "feature": key, "baseline_mean": mean,
        "transform": "absolute_difference_from_training_mean" if sensor == "optical" else "raw_count",
        "threshold": float(np.quantile(values, RULE_QUANTILE, method="higher")),
        "unit": "V" if sensor == "optical" else "events_per_2s_window",
        "quantile": RULE_QUANTILE, "quantile_method": "higher",
        "comparison": "strictly_greater_than", "combine": "model_flag OR rule_flag",
        "calibration_windows": len(calibration),
        "calibration_session_ids": sorted({r["session_id"] for r in calibration}),
    }


def score_rows(rows, bundle, rule):
    keys = bundle["baseline"]["feature_order"]
    scores = -bundle["model"].decision_function(feature_matrix(rows, keys))
    model_threshold = bundle["threshold_policy"]["threshold"]
    result = []
    for row, score in zip(rows, scores):
        raw = float(row[rule["feature"]])
        delta = raw - rule["baseline_mean"]
        value = abs(delta) if rule["transform"] == "absolute_difference_from_training_mean" else raw
        model_flag, rule_flag = bool(score > model_threshold), bool(value > rule["threshold"])
        reason = ""
        if rule_flag:
            reason = ("optical_mean_increased" if delta > 0 else "optical_mean_decreased") \
                if rule["feature"] == "optical_mean" else "gyro_rapid_changes_increased"
        result.append({
            "session_id": row["session_id"], "boot_id": row["boot_id"],
            "window_id": row["window_id"], "elapsed_start_s": row["elapsed_start_s"],
            "elapsed_end_s": row["elapsed_end_s"],
            "anomaly_score": float(score), "model_threshold": model_threshold,
            "model_flag": model_flag, "rule_feature": rule["feature"],
            "feature_value": raw, "baseline_mean": rule["baseline_mean"], "feature_delta": delta,
            "rule_value": value, "rule_threshold": rule["threshold"], "rule_flag": rule_flag,
            "combined_flag": model_flag or rule_flag, "rule_reason": reason,
        })
    return result


def make_holdout(original_entries):
    # 기존 생성 코드를 재사용하되 출력 위치/난수/식별자가 다른 별도 데이터셋입니다.
    sys.path.insert(0, str(ROOT))
    import generate_mock_sessions as generator
    old_output, old_seed = generator.OUTPUT, generator.SEED
    try:
        generator.OUTPUT, generator.SEED = HOLDOUT, HOLDOUT_SEED
        entries = [generator.make_session("test", ordinal, scenario)
                   for ordinal, scenario in enumerate(generator.TEST_SCENARIOS, start=101)]
    finally:
        generator.OUTPUT, generator.SEED = old_output, old_seed
    for key in ("session_id", "boot_id"):
        if {e[key] for e in entries} & {e[key] for e in original_entries}:
            raise ValueError("Holdout overlaps original sessions")
    write_json(HOLDOUT / "manifest.json", {
        "dataset_version": "rules_holdout_002", "is_synthetic": True,
        "seed": HOLDOUT_SEED, "sessions": entries, "total_readings": 18000,
        "note": "New random realizations of the same synthetic scenarios; not new real-world conditions",
    })
    return entries


def load_holdout(entries, expected_identity):
    features = []
    for entry in entries:
        folder = HOLDOUT / entry["directory"]
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        if meta.get("is_synthetic") is not True:
            raise ValueError("Synthetic data required")
        identity = tuple(meta[k] for k in (
            "user_id", "device_id", "schema_version", "optical_unit", "gyro_unit", "sample_rate_hz",
        ))
        if identity != expected_identity:
            raise ValueError("Holdout identity/configuration mismatch")
        for key in ("session_id", "boot_id", "split", "session_order"):
            if meta[key] != entry[key]:
                raise ValueError("Holdout metadata mismatch")
        rows = [json.loads(line) for line in (folder / "sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()]
        quality, usable, report = process(rows, meta)
        if report["usable_windows"] != 30 or report["excluded_windows"]:
            raise ValueError("Holdout must contain 30 complete windows per session")
        quality = [{k: str(v) for k, v in r.items()} for r in quality]
        features.extend({"session_id": meta["session_id"], "boot_id": meta["boot_id"], **row}
                        for row in extract_features(usable, quality))
    return features


def main():
    original = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    parts = validate_splits(original["sessions"])
    calibration, _, identity = load_features(parts["calibration"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bundles, rules, calibration_results, hashes = {}, {}, [], {}
    for sensor in ("optical", "gyro"):
        path = MODEL_DIR / f"{sensor}_model.joblib"
        bundle = joblib.load(path)
        if model_identity(bundle) != identity or bundle["baseline"].get("is_synthetic") is not True:
            raise ValueError("Model identity mismatch")
        if {r["session_id"] for r in bundle["baseline"]["train_sessions"]} != {e["session_id"] for e in parts["train"]}:
            raise ValueError("Unexpected model training sessions")
        if set(bundle["threshold_policy"]["calibration_session_ids"]) != {e["session_id"] for e in parts["calibration"]}:
            raise ValueError("Unexpected model calibration sessions")
        rule = build_rule(sensor, calibration, bundle)
        bundles[sensor], rules[sensor] = bundle, rule
        hashes[sensor] = hashlib.sha256(path.read_bytes()).hexdigest()
        calibration_results.extend({"sensor": sensor, **r} for r in score_rows(calibration, bundle, rule))
        # 보완 규칙이 포함된 별도 번들을 저장합니다. 기존 모델 파일은 유지됩니다.
        joblib.dump({**bundle, "additional_rule": rule}, OUTPUT / f"{sensor}_model_with_rule.joblib")
    # 평가 데이터 생성/판정 전에 정책을 저장합니다.
    policy = {
        "version": "sensor_rules_1", "is_synthetic": True, "rules": rules,
        "original_model_sha256": hashes, "holdout_seed_fixed_before_evaluation": HOLDOUT_SEED,
        "note": "OR combination can increase false positives; 95th percentile per rule does not guarantee a 5% combined error rate",
    }
    write_json(OUTPUT / "rule_policy.json", policy)
    policy_hash = hashlib.sha256((OUTPUT / "rule_policy.json").read_bytes()).hexdigest()
    write_csv(OUTPUT / "calibration_results.csv", calibration_results, list(calibration_results[0]))

    entries = make_holdout(original["sessions"])
    features = load_holdout(entries, identity)
    # 정답 파일을 읽기 전에 모든 모델/규칙의 판정을 완료합니다.
    predictions = [{"sensor": sensor, **row} for sensor in bundles
                   for row in score_rows(features, bundles[sensor], rules[sensor])]
    labels = {}
    for entry in entries:
        rows = read_csv(HOLDOUT / entry["directory"] / "window_labels.csv")
        if len(rows) != 30 or {int(r["window_id"]) for r in rows} != set(range(30)):
            raise ValueError("Invalid holdout labels")
        for row in rows:
            if any(row[f"{sensor}_injected_anomaly"] not in ("0", "1") for sensor in bundles):
                raise ValueError("Invalid binary label")
            labels[(entry["session_id"], int(row["window_id"]))] = row
    for row in predictions:
        label = labels[(row["session_id"], row["window_id"])]
        row["expected_anomaly"] = label[f"{row['sensor']}_injected_anomaly"] == "1"
        row["scenario"] = label["scenario"]
    comparison, per_session = {}, []
    for sensor in bundles:
        selected = [r for r in predictions if r["sensor"] == sensor]
        comparison[sensor] = {}
        for method, flag in (("model_only", "model_flag"), ("model_or_rule", "combined_flag")):
            comparison[sensor][method] = metrics([{**r, "is_anomaly": r[flag]} for r in selected])
            for entry in entries:
                rows = [{**r, "is_anomaly": r[flag]} for r in selected if r["session_id"] == entry["session_id"]]
                per_session.append({"sensor": sensor, "method": method,
                                    "session_id": entry["session_id"], **metrics(rows)})
        current = [r for r in calibration_results if r["sensor"] == sensor]
        comparison[sensor]["calibration_combined_flagged"] = sum(r["combined_flag"] for r in current)
    write_csv(OUTPUT / "holdout_features.csv", features, list(features[0]))
    write_csv(OUTPUT / "holdout_results.csv", predictions, list(predictions[0]))
    write_csv(OUTPUT / "holdout_session_metrics.csv", per_session, list(per_session[0]))
    if hashlib.sha256((OUTPUT / "rule_policy.json").read_bytes()).hexdigest() != policy_hash:
        raise ValueError("Policy changed during evaluation")
    if any(hashlib.sha256((MODEL_DIR / f"{s}_model.joblib").read_bytes()).hexdigest() != h for s, h in hashes.items()):
        raise ValueError("Original model changed during evaluation")
    write_json(OUTPUT / "run_summary.json", {
        "is_synthetic": True, "holdout_sessions": len(entries), "holdout_windows": len(features),
        "calibration_windows": len(calibration), "rule_policy_sha256": policy_hash,
        "comparison": comparison,
        "limitations": ["One simulated user, same simulator and anomaly scenarios",
                        "No proof of real sensor accuracy or clinical meaning",
                        "New holdout is consumed once inspected; do not tune and call it independent again",
                        "OR rules may increase false positives; calibration windows are correlated",
                        "Rapid-event counts apply within each 2-second window only"],
    })
    print(f"holdout_sessions: {len(entries)}")
    print(f"holdout_windows: {len(features)}")
    for sensor in bundles:
        print(f"{sensor}_rule_threshold: {rules[sensor]['threshold']:.6f} {rules[sensor]['unit']}")
        for method in ("model_only", "model_or_rule"):
            r = comparison[sensor][method]
            print(f"{sensor} {method}: TP={r['true_positive']} FP={r['false_positive']} FN={r['false_negative']} TN={r['true_negative']}")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
