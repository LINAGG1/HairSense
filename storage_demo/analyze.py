# 특징 추출, 기본 분석 코드

"""Synthetic-session exercise: window features -> baseline -> Isolation Forest.

Default input is the DB-derived preprocessing output. No database is modified.
Train only on elapsed [0,20) seconds. Evaluate usable windows from 20 seconds.
"""
import argparse
import csv
import json
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

ROOT = Path(__file__).resolve().parent.parent
SENSORS = ("optical", "gyro_x", "gyro_y", "gyro_z")
STATS = ("mean", "std", "min", "max")
FEATURES = [f"{sensor}_{stat}" for sensor in SENSORS for stat in STATS]


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def extract_features(rows, quality):
    groups = {}
    for row in rows:
        groups.setdefault(row["window_id"], []).append(row)
    usable_ids = {int(w["window_id"]) for w in quality if w["usable"] == "True"}
    if set(groups) != usable_ids:
        raise ValueError("Preprocessing files disagree about usable windows")
    result = []
    for window in quality:
        if window["usable"] != "True":
            continue
        wid = int(window["window_id"])
        group = sorted(groups[wid], key=lambda row: row["timestamp_ms"])
        expected_count = int(window["expected_count"])
        if len(group) != expected_count or expected_count != 100:
            raise ValueError(f"Expected 100 readings in window {wid}")
        expected_times = list(range(int(window["start_timestamp_ms"]),
                                    int(window["end_timestamp_ms_exclusive"]), 20))
        if [row["timestamp_ms"] for row in group] != expected_times:
            raise ValueError(f"Nonuniform timestamps in window {wid}")
        record = {
            "window_id": wid,
            "elapsed_start_s": float(window["elapsed_start_s"]),
            "elapsed_end_s": float(window["elapsed_end_s"]),
        }
        for sensor in SENSORS:
            values = np.array([row[sensor] for row in group], dtype=float)
            if not np.isfinite(values).all():
                raise ValueError(f"Invalid values in usable window {wid}")
            stats = (values.mean(), values.std(ddof=0), values.min(), values.max())
            record.update({f"{sensor}_{stat}": float(value)
                           for stat, value in zip(STATS, stats)})
        result.append(record)
    return result


def fit_reference(features):
    baseline_rows = [row for row in features
                     if row["elapsed_start_s"] >= 0 and row["elapsed_end_s"] <= 20]
    if {row["window_id"] for row in baseline_rows} != set(range(10)):
        raise ValueError("This exercise needs all ten baseline windows from 0 to 20 s")
    x_train = np.array([[row[key] for key in FEATURES] for row in baseline_rows])
    means = x_train.mean(axis=0)
    stds = x_train.std(axis=0, ddof=0)
    model = IsolationForest(
        n_estimators=200, max_samples="auto", contamination="auto",
        random_state=42, n_jobs=1,
    )
    model.fit(x_train)
    return model, means, stds, baseline_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["mysql_via_fastapi", "local_file"],
                        default="mysql_via_fastapi")
    args = parser.parse_args()
    input_dir = ROOT / "preprocessed" / args.source
    meta = json.loads((input_dir / "source_metadata.json").read_text(encoding="utf-8"))
    if meta.get("is_synthetic") is not True:
        raise ValueError("This baseline selection is only authorized for the mock exercise")
    with (input_dir / "window_quality.csv").open(encoding="utf-8-sig", newline="") as file:
        quality = list(csv.DictReader(file))
    rows = [json.loads(line) for line in
            (input_dir / "usable_readings.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    features = extract_features(rows, quality)
    model, means, stds, baseline_rows = fit_reference(features)
    lookup = {row["window_id"]: row for row in features}
    baseline_ids = {row["window_id"] for row in baseline_rows}
    evaluation = [row for row in features if row["window_id"] not in baseline_ids]
    x_eval = np.array([[row[key] for key in FEATURES] for row in evaluation])
    decisions = model.decision_function(x_eval)
    predictions = model.predict(x_eval)
    scores = {row["window_id"]: (float(score), int(prediction))
              for row, score, prediction in zip(evaluation, decisions, predictions)}
    results, changes = [], []
    for window in quality:
        wid = int(window["window_id"])
        result = {
            "window_id": wid,
            "elapsed_start_s": window["elapsed_start_s"],
            "elapsed_end_s": window["elapsed_end_s"],
            "status": "excluded", "reason": window["reason"],
            "decision_score": "", "anomaly_score": "", "is_anomaly": "",
            "largest_standardized_change_feature": "",
            "largest_abs_standardized_change": "",
        }
        if wid in baseline_ids:
            result["status"] = "baseline_reference"
        elif wid in scores:
            decision, prediction = scores[wid]
            result.update(status="evaluated", decision_score=decision,
                          anomaly_score=-decision, is_anomaly=prediction == -1)
            standardized = []
            for index, feature in enumerate(FEATURES):
                value = lookup[wid][feature]
                delta = value - float(means[index])
                # Do not manufacture enormous ratios for a constant reference feature.
                z = delta / float(stds[index]) if stds[index] > 1e-12 else None
                changes.append({
                    "window_id": wid, "feature": feature, "current_value": value,
                    "baseline_mean": float(means[index]),
                    "baseline_std": float(stds[index]), "delta": delta,
                    "standardized_change": z if z is not None else "",
                    "standardized_change_available": z is not None,
                })
                if z is not None:
                    standardized.append((abs(z), feature))
            if standardized:
                magnitude, feature = max(standardized)
                result["largest_standardized_change_feature"] = feature
                result["largest_abs_standardized_change"] = magnitude
        results.append(result)

    output = ROOT / "analysis_results" / args.source
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "features.csv", features,
              ["window_id", "elapsed_start_s", "elapsed_end_s", *FEATURES])
    write_csv(output / "analysis_results.csv", results, list(results[0]))
    change_columns = ["window_id", "feature", "current_value", "baseline_mean",
                      "baseline_std", "delta", "standardized_change", "standardized_change_available"]
    write_csv(output / "feature_changes.csv", changes, change_columns)
    baseline = {
        "is_synthetic": True, "user_id": meta["user_id"],
        "device_id": meta["device_id"], "session_id": meta["session_id"],
        "reference_elapsed_seconds": [0, 20], "reference_window_ids": sorted(baseline_ids),
        "feature_order": FEATURES, "std_ddof": 0,
        "feature_mean": dict(zip(FEATURES, means.tolist())),
        "feature_std": dict(zip(FEATURES, stds.tolist())),
        "note": "Synthetic demonstration only; not a validated personal baseline",
    }
    (output / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    # Feature order and reference statistics travel with the trained model.
    joblib.dump({"model": model, "baseline": baseline,
                 "sklearn_version": sklearn.__version__}, output / "sensor_model.joblib")
    summary = {
        "source": args.source, "is_synthetic": True,
        "feature_windows": len(features), "features_per_window": len(FEATURES),
        "baseline_windows": len(baseline_rows), "evaluated_windows": len(evaluation),
        "excluded_windows": sum(row["status"] == "excluded" for row in results),
        "flagged_windows": sum(prediction == -1 for _, prediction in scores.values()),
        "numpy_version": np.__version__, "sklearn_version": sklearn.__version__,
        "model_settings": {"n_estimators": 200, "contamination": "auto", "random_state": 42},
        "anomaly_score_definition": "-decision_function; greater than zero is flagged",
        "limitation": "10 reference windows only; execution test, not performance validation",
    }
    (output / "run_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    for key in ("feature_windows", "features_per_window", "baseline_windows",
                "evaluated_windows", "excluded_windows", "flagged_windows"):
        print(f"{key}: {summary[key]}")
    print("\nwindow  elapsed_s   decision_score  anomaly_score  flagged")
    for row in results:
        if row["status"] == "evaluated":
            print(f"{row['window_id']:>6}  {row['elapsed_start_s']:>9}  "
                  f"{row['decision_score']:>14.5f}  {row['anomaly_score']:>13.5f}  "
                  f"{row['is_anomaly']}")
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()
