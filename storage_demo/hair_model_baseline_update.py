import pandas as pd
from pathlib import Path



# 경로


BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data" / "hair_model_baseline"

AUGMENTED_FILE = DATA_DIR / "baseline_augmented_results.csv"
BASELINE_FILE = DATA_DIR / "personal_baseline.csv"


# 두피 상태


LABELS = [
    "미세각질",
    "피지과다",
    "모낭사이홍반",
    "모낭홍반/농포",
    "비듬",
    "탈모"
]

GRADE_COLUMNS = [
    f"{label}_grade"
    for label in LABELS
]


# 1. 현재 측정 전 Baseline 계산


def calculate_baseline():

    df = pd.read_csv(
        AUGMENTED_FILE,
        encoding="utf-8-sig"
    )

    baseline_mean = df[GRADE_COLUMNS].mean()

    baseline_df = pd.DataFrame({
        "두피 상태": LABELS,
        "평균 등급": [
            baseline_mean[f"{label}_grade"]
            for label in LABELS
        ]
    })

    baseline_df["평균 등급"] = (
        baseline_df["평균 등급"].round(2)
    )

    return baseline_df


# 2. 새로운 측정값과 기존 Baseline 비교

def compare_with_baseline(current_result):

    baseline_df = calculate_baseline()

    comparison = pd.DataFrame({
        "두피 상태": LABELS,
        "과거 평균": baseline_df["평균 등급"],
        "현재 측정": [
            current_result[f"{label}_grade"]
            for label in LABELS
        ]
    })

    comparison["차이"] = (
        comparison["현재 측정"]
        - comparison["과거 평균"]
    ).round(2)

    return comparison


# 3. 측정값을 누적 데이터에 추가

def add_measurement(current_result):

    df = pd.read_csv(
        AUGMENTED_FILE,
        encoding="utf-8-sig"
    )

    new_row = {}

    # 등급 저장
    for column in GRADE_COLUMNS:
        new_row[column] = current_result[column]

    # 현재 측정값은 실제 데이터
    new_row["synthetic"] = False

    # 출처 표시
    new_row["source_filename"] = "current_measurement"

    new_row_df = pd.DataFrame([new_row])

    df = pd.concat(
        [df, new_row_df],
        ignore_index=True
    )

    df.to_csv(
        AUGMENTED_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print(f"측정값 누적 완료: 총 {len(df)}개")


# ============================================================
# 4. Baseline CSV 갱신
# ============================================================

def save_baseline():

    baseline_df = calculate_baseline()

    baseline_df.to_csv(
        BASELINE_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    print("Baseline CSV 갱신 완료")

    return baseline_df


# 전체 처리


def process_measurement(current_result):

    print("========================================")
    print("현재 측정값 분석")
    print("========================================")

    # ① 오늘 측정 전 Baseline 계산
    comparison = compare_with_baseline(
        current_result
    )

    print("\n[과거 평균 vs 현재 측정]")
    print(comparison.to_string(index=False))

    # ② 비교가 끝난 후 현재 측정값 누적
    add_measurement(current_result)

    # ③ 다음 측정을 위한 Baseline 갱신
    save_baseline()

    return comparison


# 테스트(더미데이터)


if __name__ == "__main__":

    current_result = {

        "미세각질_grade": 1,
        "피지과다_grade": 2,
        "모낭사이홍반_grade": 2,
        "모낭홍반/농포_grade": 1,
        "비듬_grade": 0,
        "탈모_grade": 1
    }

    process_measurement(current_result)