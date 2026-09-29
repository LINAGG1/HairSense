"""Real sensor dashboard components. Independent of all scalp image code."""
import requests
import streamlit as st


STATUS = {
    "baseline_created": "개인 기준이 만들어졌어요. 다음 측정부터 평소와 비교해 드려요.",
    "baseline_collecting": "평소 상태를 파악하고 있어요. 측정 기록이 조금 더 필요해요.",
    "insufficient_data": "측정 데이터가 부족해 이번 결과는 비교하기 어려워요.",
    "baseline_required": "평소와 비교하려면 먼저 측정 기록을 쌓아 주세요.",
    "calibration_completed": "개인 기준 조정이 완료됐어요. 다음 측정부터 적용돼요.",
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
    data = (snapshot or {}).get("summary", {}).get("sensors", {}).get(sensor)
    if not data:
        st.write("아직 광학 측정 결과가 없습니다." if sensor == "optical" else "아직 움직임 측정 결과가 없습니다.")
        return
    if data.get("status") in STATUS:
        st.write(STATUS[data["status"]])
        return
    message = data.get("message") or ((snapshot or {}).get("feedback") or {}).get("messages", {}).get(sensor, {}).get("message")
    if message:
        st.write(message)
    else:
        st.write("이번 측정의 결과 안내가 아직 준비되지 않았어요.")
