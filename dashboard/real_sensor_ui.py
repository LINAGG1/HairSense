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
    elif data["status"] in STATUS:
        st.info(STATUS[data["status"]])
