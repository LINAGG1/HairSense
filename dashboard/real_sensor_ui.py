"""Real sensor dashboard components. Independent of all scalp image code."""
import requests
import streamlit as st


STATUS = {
    "baseline_created": "첫 baseline 생성 완료 — 다음 측정부터 비교합니다.",
    "baseline_collecting": "baseline 수집 중 — 유효한 2초 구간이 2개 이상 필요합니다.",
    "insufficient_data": "유효한 2초 구간이 없어 비교와 학습에서 제외했습니다.",
    "baseline_required": "먼저 일반 측정으로 초기 baseline을 만들어 주세요.",
    "calibration_completed": "별도 기준 조정 완료 — 다음 측정부터 조정된 임계값을 사용합니다.",
}


def fetch_snapshot(api_url, user_id, device_id):
    try:
        response = requests.get(f"{api_url}/real-sensors/latest",
                                params={"user_id": user_id, "device_id": device_id}, timeout=30)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        st.error("실제 센서 결과를 가져오지 못했습니다. 서버의 사용자·기기 설정과 DB 설치 상태를 확인해 주세요.")
        return None


def render_sensor(snapshot, sensor):
    title = "광학 센서 분석 결과" if sensor == "optical" else "자이로 센서 분석 결과"
    st.write(title)
    data = (snapshot or {}).get("summary", {}).get("sensors", {}).get(sensor)
    if not data:
        st.info("아직 이 센서의 실제 측정 결과가 없습니다.")
        return
    message = data.get("message") or ((snapshot or {}).get("feedback") or {}).get("messages", {}).get(sensor, {}).get("message")
    if message:
        st.write(message)
    elif data["status"] == "completed":
        st.info("한 줄 설명이 아직 제공되지 않았습니다. 센서 서버를 최신 코드로 재시작해 주세요.")
    st.caption(f"세션 {data['session_id']} · 부팅 {data['boot_id']} · 저장 {data.get('recorded_at', '')}")
    if data["status"] != "completed":
        st.info(STATUS.get(data["status"], data["status"]))
    else:
        st.metric("기준을 벗어난 구간", f"{data['flagged_windows']} / {data['evaluated_windows']}",
                  help="각 구간은 2초입니다. 건강 위험 확률을 뜻하지 않습니다.")
        st.caption(f"비교 baseline v{data['compared_baseline_version']} · 이전 학습 구간 {data['reference_windows']}개")
        if data["threshold_status"] == "provisional":
            st.warning("초기 임계값입니다. 별도 기준 조정 측정을 진행해 주세요.")
        else:
            st.caption("별도 기준 조정 세션으로 결정한 임계값을 사용했습니다.")
        st.write(f"평균 이상치 점수: {data['mean_anomaly_score']:.4f}")
        st.caption(f"Isolation Forest 임계값 {data['threshold']:.4f} · 추가 규칙 임계값 {data['rule_threshold']:.4f}")
    st.caption(f"유효 구간 {data['usable_windows']}개 · 제외 구간 {data['excluded_windows']}개")
    if data.get("feature_comparison"):
        labels = {"optical_mean": "광학 평균 (V)", "optical_std": "광학 표준편차 (V)"}
        for axis in ("x", "y", "z"):
            labels[f"gyro_{axis}_mean"] = f"자이로 {axis.upper()} 평균 (deg/s)"
            labels[f"gyro_{axis}_std"] = f"자이로 {axis.upper()} 표준편차 (deg/s)"
        st.dataframe([{"특징": labels.get(key, key), "현재": row["current_mean"],
                       "이전 baseline": row["baseline_mean"], "차이": row["difference"]}
                      for key, row in data["feature_comparison"].items()], hide_index=True)
    with st.expander("센서 분석 상세"):
        st.json((snapshot or {}).get("captures", {}).get(sensor, data))


def calibration_controls(api_url, user_id, device_id):
    with st.expander("센서 기준 조정 세션"):
        st.write("첫 일반 측정 후, 평소와 같은 상태에서 별도 측정을 진행해 주세요. "
                 "선택한 센서의 다음 유효 측정은 임계값 조정에만 사용하며 baseline 학습에는 넣지 않습니다.")
        sensor = st.selectbox("기준 조정할 센서", ["optical", "gyro"],
                              format_func=lambda value: "광학 센서" if value == "optical" else "자이로 센서")
        role = st.radio("다음 측정 용도", ["calibration", "measurement"],
                        format_func=lambda value: "기준 조정" if value == "calibration" else "일반 측정 (기준 조정 취소)")
        if st.button("다음 센서 측정에 적용"):
            try:
                response = requests.post(f"{api_url}/real-sensors/{sensor}/next-capture",
                    json={"user_id": user_id, "device_id": device_id, "role": role}, timeout=30)
                if response.status_code == 409:
                    st.warning("이 센서의 초기 baseline을 먼저 수집해 주세요.")
                else:
                    response.raise_for_status()
                    st.success("다음 측정 용도를 저장했습니다. 기준 조정은 유효한 세션 1회 후 자동으로 종료됩니다.")
            except requests.RequestException:
                st.error("설정을 저장하지 못했습니다. 서버 연결을 확인해 주세요.")
