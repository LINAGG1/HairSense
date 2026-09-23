"""가상 데이터의 광학/자이로 분리 분석. 기존 analyze.py의 특징 추출을 재사용합니다."""

import argparse
import csv
import json

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from analyze import (
    ROOT, FEATURES, GYRO_RAPID_CHANGE_THRESHOLD_DPS2,
    extract_features, write_csv,
)


FEATURE_GROUPS = {
    "optical": ["optical_mean", "optical_std"],
    "gyro": [
        "gyro_x_mean", "gyro_x_std", "gyro_y_mean", "gyro_y_std",
        "gyro_z_mean", "gyro_z_std", "gyro_rapid_change_count",
    ],
}


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def fit_sensor(reference, keys):
    x = np.array([[row[key] for key in keys] for row in reference], dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Non-finite baseline features")
    model = IsolationForest(
        n_estimators=200, max_samples="auto", contamination="auto",
        random_state=42, n_jobs=1,
    )
    model.fit(x)
    return model, x.mean(axis=0), x.std(axis=0, ddof=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["mysql_via_fastapi", "local_file"],
                        default="mysql_via_fastapi")
    args = parser.parse_args()
    source = ROOT / "preprocessed" / args.source
    meta = json.loads((source / "source_metadata.json").read_text(encoding="utf-8"))
    if meta.get("is_synthetic") is not True:
        raise ValueError("First-20-second baseline is only for synthetic exercises")
    with (source / "window_quality.csv").open(encoding="utf-8-sig", newline="") as file:
        quality = list(csv.DictReader(file))
    rows = [json.loads(line) for line in
            (source / "usable_readings.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if any(row["boot_id"] != meta["boot_id"] for row in rows):
        raise ValueError("Mixed boot IDs or metadata mismatch")

    features = extract_features(rows, quality)
    reference = [row for row in features
                 if row["elapsed_start_s"] >= 0 and row["elapsed_end_s"] <= 20]
    reference_ids = {row["window_id"] for row in reference}
    if reference_ids != set(range(10)):
        raise ValueError("All ten baseline windows from 0 to 20 seconds are required")
    evaluation = [row for row in features if row["window_id"] not in reference_ids]
    if not evaluation:
        raise ValueError("No evaluation windows")
    lookup = {row["window_id"]: row for row in features}

    output = ROOT / "analysis_results" / args.source / "by_sensor"
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "features.csv", features,
              ["window_id", "elapsed_start_s", "elapsed_end_s", *FEATURES])
    summary = {
        "source": args.source, "is_synthetic": True,
        "feature_windows": len(features), "baseline_windows": len(reference),
        "evaluated_windows": len(evaluation),
        "excluded_windows": sum(w["usable"] != "True" for w in quality),
        "anomaly_score_definition": "-decision_function; positive means flagged, not probability",
        "limitation": "Synthetic 10-window baseline; thresholds uncalibrated. Shared quality exclusion remains.",
        "sensors": {},
    }

    for sensor, keys in FEATURE_GROUPS.items():
        model, means, stds = fit_sensor(reference, keys)
        x_eval = np.array([[row[key] for key in keys] for row in evaluation], dtype=float)
        decisions = model.decision_function(x_eval)
        scores = {row["window_id"]: float(score)
                  for row, score in zip(evaluation, decisions)}
        results, changes = [], []
        for window in quality:
            wid = int(window["window_id"])
            result = {
                "window_id": wid, "elapsed_start_s": window["elapsed_start_s"],
                "elapsed_end_s": window["elapsed_end_s"], "sensor": sensor,
                "status": "excluded", "reason": window["reason"],
                "decision_score": "", "anomaly_score": "", "is_anomaly": "",
            }
            if wid in reference_ids:
                result["status"] = "baseline_reference"
            elif wid in scores:
                score = scores[wid]
                result.update(status="evaluated", decision_score=score,
                              anomaly_score=-score, is_anomaly=score < 0)
                for index, key in enumerate(keys):
                    value = float(lookup[wid][key])
                    delta = value - float(means[index])
                    available = bool(stds[index] > 1e-12)
                    changes.append({
                        "window_id": wid, "sensor": sensor, "feature": key,
                        "current_value": value, "baseline_mean": float(means[index]),
                        "baseline_std": float(stds[index]), "delta": delta,
                        "standardized_change": delta / float(stds[index]) if available else "",
                        "standardized_change_available": available,
                    })
            results.append(result)

        constant_features = [key for key, std in zip(keys, stds) if std <= 1e-12]
        baseline = {
            **{key: meta[key] for key in
               ("user_id", "device_id", "session_id", "boot_id", "is_synthetic")},
            "sensor": sensor, "reference_elapsed_seconds": [0, 20],
            "reference_window_ids": sorted(reference_ids), "feature_order": keys,
            "feature_mean": dict(zip(keys, means.tolist())),
            "feature_std": dict(zip(keys, stds.tolist())), "std_ddof": 0,
            "constant_reference_features": constant_features,
            "constant_feature_note": "No reliable standardized change; exactly constant features cannot split trees. Inspect absolute delta.",
            "feature_definition_version": "mean_std_gyro_events_v1",
            "gyro_rapid_change_threshold_dps2": GYRO_RAPID_CHANGE_THRESHOLD_DPS2,
            "gyro_rapid_change_count_scope": "within_each_2s_window",
            "note": "Synthetic demonstration only; not a validated personal baseline",
        }
        write_csv(output / f"{sensor}_results.csv", results, list(results[0]))
        write_csv(output / f"{sensor}_changes.csv", changes, list(changes[0]))
        write_json(output / f"{sensor}_baseline.json", baseline)
        joblib.dump({"model": model, "baseline": baseline,
                     "sklearn_version": sklearn.__version__},
                    output / f"{sensor}_model.joblib")
        summary["sensors"][sensor] = {
            "features_per_window": len(keys),
            "flagged_windows": sum(score < 0 for score in scores.values()),
            "constant_reference_features": constant_features,
        }

    summary["numpy_version"] = np.__version__
    summary["sklearn_version"] = sklearn.__version__
    summary["model_settings"] = {
        "n_estimators": 200, "max_samples": "auto", "contamination": "auto",
        "random_state": 42, "n_jobs": 1,
    }
    write_json(output / "run_summary.json", summary)
    for key in ("feature_windows", "baseline_windows", "evaluated_windows", "excluded_windows"):
        print(f"{key}: {summary[key]}")
    for sensor, info in summary["sensors"].items():
        print(f"{sensor}_features: {info['features_per_window']}")
        print(f"{sensor}_flagged_windows: {info['flagged_windows']}")
        print(f"{sensor}_constant_reference_features: {info['constant_reference_features']}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
