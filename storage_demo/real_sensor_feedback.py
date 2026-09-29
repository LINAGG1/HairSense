"""Deterministic sentences from a real capture's frozen comparison evidence."""
import copy

from sensor_feedback import FACTS, select_finding

VERSION = "real_sensor_feedback_v1"
NOTICE = "광학 결과는 유분 상태를 참고하기 위한 반사 신호 비교이며, 실제 유분량을 확정한 결과는 아닙니다. 이상치 점수는 건강 위험 확률이 아닙니다."
REAL_FACTS = {
    "optical_high": "유분 상태 참고: 광학 반사 신호가 평소 기준보다 높은 구간이 나타났어요.",
    "optical_low": "유분 상태 참고: 광학 반사 신호가 평소 기준보다 낮은 구간이 나타났어요.",
    "optical_mixed": "유분 상태 참고: 광학 반사 신호가 평소보다 높은 구간과 낮은 구간이 함께 나타났어요.",
    "optical_pattern": "유분 상태 참고: 일부 구간에서 평소와 다른 광학 반사 패턴이 나타났어요.",
    "optical_unflagged": "유분 상태 참고: 이번 광학 반사 신호에서 설정된 기준을 넘는 변화는 나타나지 않았어요.",
    "gyro_pattern": FACTS["gyro_pattern"],
    "gyro_unflagged": FACTS["gyro_unflagged"],
}
STATE_MESSAGES = {
    "baseline_created": "첫 개인 기준이 만들어졌어요. 다음 측정부터 평소와 비교해 드려요.",
    "baseline_collecting": "개인 기준을 수집 중이에요. 유효한 2초 구간이 더 필요해요.",
    "baseline_required": "아직 비교할 개인 기준이 없어요. 먼저 일반 측정을 진행해 주세요.",
    "calibration_completed": "기준 조정이 완료됐어요. 다음 측정부터 조정된 임계값으로 비교해요.",
    "insufficient_data": "유효한 2초 구간이 부족해 이번 측정은 비교할 수 없어요.",
}


def with_real_feedback(capture):
    """Enrich old snapshots without changing stored scores or learning history."""
    if capture.get("is_synthetic") is not False:
        raise ValueError("Real capture required")
    result = copy.deepcopy(capture)
    if result.get("feedback"):
        return result
    sensor = result["sensor"]
    if sensor not in ("optical", "gyro"):
        raise ValueError("Unsupported sensor")
    status = result["status"]
    counts = {}
    if status != "completed":
        finding = status
        message = STATE_MESSAGES.get(status, "이번 측정의 비교 결과를 아직 확인할 수 없어요.")
    else:
        scores = result.get("scores", [])
        baseline = result.get("compared_baseline")
        if not scores or not baseline:
            finding, message = "comparison_unavailable", "저장된 비교 정보가 부족해 결과를 설명할 수 없어요."
        else:
            flagged = [row for row in scores if row["is_anomaly"]]
            if sensor == "optical":
                counts = {"optical_mean_increased": 0, "optical_mean_decreased": 0}
                lookup = {row["window_id"]: row for row in result.get("features", [])}
                mean = baseline["feature_mean"]["optical_mean"]
                scale = max(baseline["feature_std"]["optical_mean"], max(abs(mean), 1.0) * 1e-6)
                for score in flagged:
                    feature = lookup.get(score["window_id"])
                    if feature is None:
                        continue
                    delta = (feature["optical_mean"] - mean) / scale
                    # Use the stored per-window threshold, never the newest model.
                    threshold = score.get("rule_threshold", baseline["rule_threshold"])
                    if delta > threshold:
                        counts["optical_mean_increased"] += 1
                    elif -delta > threshold:
                        counts["optical_mean_decreased"] += 1
            # Real gyro currently has axis mean/std only, not rapid-event counts.
            finding = select_finding(sensor, len(flagged), counts)
            message = REAL_FACTS[finding]
    item = {"finding": finding, "message": message, "rule_reason_windows": counts}
    result["feedback"] = {"source": "rules", "is_synthetic": False,
                          "version": VERSION, "notice": NOTICE, "messages": {sensor: item}}
    result["summary"]["sensors"][sensor].update(item)
    return result
