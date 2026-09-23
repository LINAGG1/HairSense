"""가상 센서 데이터: 특징 추출 → Baseline → Isolation Forest.

DB에서 읽어 전처리한 파일을 기본 입력으로 사용합니다.
처음 20초는 실습용 Baseline, 이후 구간은 평가에 사용합니다.
DB 데이터와 이미지 분석 모델은 변경하지 않습니다.
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
STATS = ("mean", "std")

FEATURES = [
    f"{sensor}_{stat}"
    for sensor in SENSORS
    for stat in STATS
] + ["gyro_rapid_change_count"]

# 가상 데이터 테스트용 임시 기준입니다.
# 단위: °/s². 실제 사용자 판정에는 실측 기반 조정이 필요합니다.
GYRO_RAPID_CHANGE_THRESHOLD_DPS2 = 500.0


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def count_gyro_rapid_changes(group):
    """2초 구간 내부에서 연속된 임계값 초과를 한 이벤트로 계산합니다."""
    if len(group) < 2:
        return 0

    # 다른 부팅의 데이터를 연결해서 변화량을 계산하지 않습니다.
    if len({row["boot_id"] for row in group}) != 1:
        raise ValueError("Mixed boot IDs in gyro window")

    times_s = np.array(
        [row["timestamp_ms"] for row in group],
        dtype=float,
    ) / 1000.0

    gyro = np.array(
        [
            [row["gyro_x"], row["gyro_y"], row["gyro_z"]]
            for row in group
        ],
        dtype=float,
    )

    if not np.isfinite(gyro).all():
        raise ValueError("Invalid gyro values")

    dt = np.diff(times_s)

    # 현재는 누락 없는 20ms 가상 데이터 구간만 처리합니다.
    if not np.allclose(dt, 0.02, rtol=0, atol=1e-9):
        raise ValueError("Expected consecutive 20 ms gyro samples")

    # 인접한 두 측정의 각속도 벡터 차이 / 실제 시간 간격
    changes = np.diff(gyro, axis=0)
    change_rate = np.linalg.norm(changes, axis=1) / dt

    above = change_rate > GYRO_RAPID_CHANGE_THRESHOLD_DPS2

    # False → True로 전환되는 지점만 이벤트 시작으로 셉니다.
    # 구간의 첫 변화량부터 기준을 넘으면 한 이벤트로 셉니다.
    previous_above = np.concatenate(([False], above[:-1]))
    starts = above & ~previous_above

    return int(np.count_nonzero(starts))


def extract_features(rows, quality):
    groups = {}

    for row in rows:
        groups.setdefault(row["window_id"], []).append(row)

    usable_ids = {
        int(window["window_id"])
        for window in quality
        if window["usable"] == "True"
    }

    if set(groups) != usable_ids:
        raise ValueError("Preprocessing files disagree about usable windows")

    result = []

    for window in quality:
        if window["usable"] != "True":
            continue

        wid = int(window["window_id"])

        group = sorted(
            groups[wid],
            key=lambda row: row["timestamp_ms"],
        )

        expected_count = int(window["expected_count"])

        if len(group) != expected_count or expected_count != 100:
            raise ValueError(f"Expected 100 readings in window {wid}")

        expected_times = list(
            range(
                int(window["start_timestamp_ms"]),
                int(window["end_timestamp_ms_exclusive"]),
                20,
            )
        )

        if [row["timestamp_ms"] for row in group] != expected_times:
            raise ValueError(f"Nonuniform timestamps in window {wid}")

        record = {
            "window_id": wid,
            "elapsed_start_s": float(window["elapsed_start_s"]),
            "elapsed_end_s": float(window["elapsed_end_s"]),
        }

        # 광학 및 자이로 각 축의 평균·표준편차
        for sensor in SENSORS:
            values = np.array(
                [row[sensor] for row in group],
                dtype=float,
            )

            if not np.isfinite(values).all():
                raise ValueError(f"Invalid values in usable window {wid}")

            stats = (
                values.mean(),
                values.std(ddof=0),
            )

            record.update({
                f"{sensor}_{stat}": float(value)
                for stat, value in zip(STATS, stats)
            })

        # 세 축을 종합한 급변 횟수
        record["gyro_rapid_change_count"] = count_gyro_rapid_changes(group)

        result.append(record)

    return result


def fit_reference(features):
    baseline_rows = [
        row
        for row in features
        if row["elapsed_start_s"] >= 0
        and row["elapsed_end_s"] <= 20
    ]

    if {row["window_id"] for row in baseline_rows} != set(range(10)):
        raise ValueError(
            "This exercise needs all ten baseline windows from 0 to 20 s"
        )

    x_train = np.array([
        [row[key] for key in FEATURES]
        for row in baseline_rows
    ])

    means = x_train.mean(axis=0)
    stds = x_train.std(axis=0, ddof=0)

    model = IsolationForest(
        n_estimators=200,
        max_samples="auto",
        contamination="auto",
        random_state=42,
        n_jobs=1,
    )

    model.fit(x_train)

    return model, means, stds, baseline_rows


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        choices=["mysql_via_fastapi", "local_file"],
        default="mysql_via_fastapi",
    )

    args = parser.parse_args()
    input_dir = ROOT / "preprocessed" / args.source

    meta = json.loads(
        (input_dir / "source_metadata.json").read_text(encoding="utf-8")
    )

    if meta.get("is_synthetic") is not True:
        raise ValueError(
            "This baseline selection is only authorized for the mock exercise"
        )

    with (input_dir / "window_quality.csv").open(
        encoding="utf-8-sig",
        newline="",
    ) as file:
        quality = list(csv.DictReader(file))

    rows = [
        json.loads(line)
        for line in (input_dir / "usable_readings.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    if any(row["boot_id"] != meta["boot_id"] for row in rows):
        raise ValueError("Reading boot_id does not match source metadata")

    features = extract_features(rows, quality)

    model, means, stds, baseline_rows = fit_reference(features)

    lookup = {
        row["window_id"]: row
        for row in features
    }

    baseline_ids = {
        row["window_id"]
        for row in baseline_rows
    }

    evaluation = [
        row
        for row in features
        if row["window_id"] not in baseline_ids
    ]

    if not evaluation:
        raise ValueError("No usable evaluation windows after baseline")

    x_eval = np.array([
        [row[key] for key in FEATURES]
        for row in evaluation
    ])

    decisions = model.decision_function(x_eval)
    predictions = model.predict(x_eval)

    scores = {
        row["window_id"]: (float(score), int(prediction))
        for row, score, prediction in zip(
            evaluation,
            decisions,
            predictions,
        )
    }

    results = []
    changes = []

    for window in quality:
        wid = int(window["window_id"])

        result = {
            "window_id": wid,
            "elapsed_start_s": window["elapsed_start_s"],
            "elapsed_end_s": window["elapsed_end_s"],
            "status": "excluded",
            "reason": window["reason"],
            "decision_score": "",
            "anomaly_score": "",
            "is_anomaly": "",
            "largest_standardized_change_feature": "",
            "largest_abs_standardized_change": "",
        }

        if wid in baseline_ids:
            result["status"] = "baseline_reference"

        elif wid in scores:
            decision, prediction = scores[wid]

            result.update(
                status="evaluated",
                decision_score=decision,
                anomaly_score=-decision,
                is_anomaly=prediction == -1,
            )

            standardized = []

            for index, feature in enumerate(FEATURES):
                value = lookup[wid][feature]
                delta = value - float(means[index])

                # Baselineの変動がほぼゼロの場合は標準化しません。
                z = (
                    delta / float(stds[index])
                    if stds[index] > 1e-12
                    else None
                )

                changes.append({
                    "window_id": wid,
                    "feature": feature,
                    "current_value": value,
                    "baseline_mean": float(means[index]),
                    "baseline_std": float(stds[index]),
                    "delta": delta,
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

    write_csv(
        output / "features.csv",
        features,
        ["window_id", "elapsed_start_s", "elapsed_end_s", *FEATURES],
    )

    write_csv(
        output / "analysis_results.csv",
        results,
        list(results[0]),
    )

    write_csv(
        output / "feature_changes.csv",
        changes,
        [
            "window_id",
            "feature",
            "current_value",
            "baseline_mean",
            "baseline_std",
            "delta",
            "standardized_change",
            "standardized_change_available",
        ],
    )

    baseline = {
        "is_synthetic": True,
        "user_id": meta["user_id"],
        "device_id": meta["device_id"],
        "session_id": meta["session_id"],
        "boot_id": meta["boot_id"],
        "reference_elapsed_seconds": [0, 20],
        "reference_window_ids": sorted(baseline_ids),
        "feature_order": FEATURES,
        "std_ddof": 0,
        "feature_definition_version": "mean_std_gyro_events_v1",
        "gyro_rapid_change_threshold_dps2": (
            GYRO_RAPID_CHANGE_THRESHOLD_DPS2
        ),
        "gyro_rapid_change_count_scope": "within_each_2s_window",
        "feature_mean": dict(zip(FEATURES, means.tolist())),
        "feature_std": dict(zip(FEATURES, stds.tolist())),
        "note": "Synthetic demonstration only; not a validated personal baseline",
    }

    (output / "baseline.json").write_text(
        json.dumps(
            baseline,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    joblib.dump(
        {
            "model": model,
            "baseline": baseline,
            "sklearn_version": sklearn.__version__,
        },
        output / "sensor_model.joblib",
    )

    summary = {
        "source": args.source,
        "is_synthetic": True,
        "feature_windows": len(features),
        "features_per_window": len(FEATURES),
        "baseline_windows": len(baseline_rows),
        "evaluated_windows": len(evaluation),
        "excluded_windows": sum(
            row["status"] == "excluded"
            for row in results
        ),
        "flagged_windows": sum(
            prediction == -1
            for _, prediction in scores.values()
        ),
        "numpy_version": np.__version__,
        "sklearn_version": sklearn.__version__,
        "model_settings": {
            "n_estimators": 200,
            "contamination": "auto",
            "random_state": 42,
        },
        "anomaly_score_definition": (
            "-decision_function; greater than zero is flagged"
        ),
        "limitation": (
            "10 reference windows only; execution test, "
            "not performance validation"
        ),
    }

    (output / "run_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    for key in (
        "feature_windows",
        "features_per_window",
        "baseline_windows",
        "evaluated_windows",
        "excluded_windows",
        "flagged_windows",
    ):
        print(f"{key}: {summary[key]}")

    print("\nwindow  elapsed_s   decision_score  anomaly_score  flagged")

    for row in results:
        if row["status"] == "evaluated":
            print(
                f"{row['window_id']:>6}  "
                f"{row['elapsed_start_s']:>9}  "
                f"{row['decision_score']:>14.5f}  "
                f"{row['anomaly_score']:>13.5f}  "
                f"{row['is_anomaly']}"
            )

    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()