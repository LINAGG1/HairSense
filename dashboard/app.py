import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import requests
import os
from dotenv import load_dotenv
from real_sensor_ui import fetch_snapshot, render_sensor

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")

if not API_BASE_URL:
    st.error("API 서버 주소가 설정되지 않았습니다.")
    st.stop()

SESSION_ID = os.getenv("SESSION_ID", "test-session-001")
DEVICE_ID = os.getenv("DEVICE_ID", "hairsense-001")
BOOT_ID = os.getenv("BOOT_ID", "boot-001")
USER_ID = os.getenv("USER_ID", "user-001")
SENSOR_USER_ID = os.getenv("HAIRSENSE_REAL_USER_ID", USER_ID)
SENSOR_DEVICE_ID = os.getenv("HAIRSENSE_REAL_DEVICE_ID", DEVICE_ID)

def get_analysis_result():
    return fetch_snapshot(API_BASE_URL, SENSOR_USER_ID, SENSOR_DEVICE_ID)
    
def get_session_image(snapshot):
    """Fetch the DB image belonging to the same session as the analysis."""
    metadata = snapshot.get("captures", {}).get("optical", {}).get("metadata")
    st.session_state.image_load_error = None
    if not metadata:
        return None
    try:
        response = requests.get(
            f"{API_BASE_URL}/sensor-sessions/{metadata['session_id']}/image",
            params={key: metadata[key] for key in ("device_id", "boot_id", "user_id")},
            timeout=10,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if response.headers.get("Content-Type", "").split(";")[0] not in (
            "image/jpeg", "image/png", "image/webp"
        ) or not response.content:
            st.session_state.image_load_error = "서버에서 올바른 이미지를 반환하지 않았습니다."
            return None
        return response.content
    except requests.exceptions.RequestException:
        st.session_state.image_load_error = "이미지를 불러오지 못했습니다. 센서 결과는 확인할 수 있습니다."
        return None

def get_image_history():
    try:
        response = requests.get(
            f"{API_BASE_URL}/ai/history/{USER_ID}",
            params={"limit": 30},
            timeout=10,
        )

        response.raise_for_status()

        return response.json().get("items", [])

    except requests.RequestException as e:
        st.error(f"두피 이미지 이력을 불러오지 못했습니다: {e}")
        return []

def calculate_image_baseline(history):
    if not history:
        return {}

    grade_fields = {
        "미세각질": "micro_scale_grade",
        "피지과다": "excess_sebum_grade",
        "모낭사이홍반": "perifollicular_erythema_grade",
        "모낭홍반/농포": "follicular_erythema_pustule_grade",
        "비듬": "dandruff_grade",
        "탈모": "hair_loss_grade",
    }

    baseline = {}

    for label, field in grade_fields.items():
        values = [
            row[field]
            for row in history
            if row.get(field) is not None
        ]

        if values:
            baseline[label] = sum(values) / len(values)

    return baseline

def show_image_history_graph(history):
    if not history:
        st.info("아직 두피 이미지 측정 기록이 없습니다.")
        return

    df = pd.DataFrame(history)

    df["measured_at"] = pd.to_datetime(
        df["measured_at"]
    )

    grade_fields = {
        "미세각질": "micro_scale_grade",
        "피지과다": "excess_sebum_grade",
        "모낭사이홍반": "perifollicular_erythema_grade",
        "모낭홍반/농포": "follicular_erythema_pustule_grade",
        "비듬": "dandruff_grade",
        "탈모": "hair_loss_grade",
    }

    selected_label = st.selectbox(
        "두피 상태",
        list(grade_fields.keys()),
    )

    selected_field = grade_fields[selected_label]

    baseline = df[selected_field].mean()

    graph_df = df[
        ["measured_at", selected_field]
    ].copy()

    graph_df = graph_df.dropna()

    graph_df = graph_df.rename(
        columns={
            selected_field: "severity"
        }
    )

    graph_df["baseline"] = baseline

    st.line_chart(
        graph_df.set_index("measured_at")[
            ["severity", "baseline"]
        ]
    )

    st.caption(
        f"{selected_label} 개인 평균: {baseline:.2f} "
        f"(0=양호, 1=경증, 2=중등도, 3=중증)"
    )

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False 


# ============================================================
# Page
# ============================================================

st.set_page_config(
    page_title="HairSense",
    page_icon="🪮",
    layout="wide",
)


# ============================================================
# 기본 화면 스타일
# ============================================================

st.markdown(
    """
    <style>

    .block-container {
        max-width: 100%;
        width: 100%;
        padding-top: 1rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
        padding-bottom: 0.5rem;
    }

    /* ========================================================
       공통 글씨 크기
       ======================================================== */

    h1 {
        font-size: 5rem !important;
        font-weight: 800 !important;
        margin-bottom: 0.3rem !important;
    }

    h2 {
        font-size: 3.4rem !important;
        font-weight: 800 !important;
        margin-top: 0.6rem !important;
        margin-bottom: 0.7rem !important;
    }

    h3 {
        font-size: 2.8rem !important;
        font-weight: 750 !important;
        margin-top: 0.5rem !important;
        margin-bottom: 0.5rem !important;
    }

    p {
        font-size: 2.2rem !important;
        line-height: 1.5 !important;
    }

    .stCaption,
    div[data-testid="stCaptionContainer"] {
        font-size: 1.5rem !important;
    }

    div[data-testid="stAlert"] {
        font-size: 2rem !important;
    }

    div[data-testid="stAlert"] p {
        font-size: 2rem !important;
    }

    div[data-testid="stMetricLabel"] {
        font-size: 1.7rem !important;
        font-weight: 700 !important;
    }

    div[data-testid="stMetricValue"] {
        font-size: 3.5rem !important;
        font-weight: 800 !important;
    }

    div[data-testid="stMetricDelta"] {
        font-size: 1.5rem !important;
    }

    /* ========================================================
       버튼 공통
       ======================================================== */

    .stButton button {
        font-size: 1.8rem !important;
        font-weight: 700 !important;
        min-height: 70px !important;
        border-radius: 12px !important;
    }

    /* ========================================================
       측정 전 화면
       ======================================================== */

    .pre-title {
        text-align: center;
        font-size: 5rem !important;
        font-weight: 800 !important;
        color: #00246D !important;
        margin-top: 4rem !important;
        margin-bottom: 1rem !important;
    }

    .pre-subtitle {
        text-align: center;
        font-size: 2.2rem !important;
        color: #475467 !important;
        margin-bottom: 3rem !important;
    }

    .pre-section-title {
        text-align: center;
        font-size: 3rem !important;
        font-weight: 800 !important;
        color: #1D2939 !important;
        margin-top: 1rem !important;
        margin-bottom: 1.5rem !important;
    }

    .pre-info-box {
        background-color: #F1F4F9;
        border-radius: 16px;
        padding: 2rem 2.5rem;
        margin: 0 auto 1.5rem auto;
        text-align: center;
    }

    .pre-info-title {
        font-size: 2rem !important;
        font-weight: 800 !important;
        color: #00246D !important;
        margin-bottom: 0.8rem;
    }

    .pre-info-text {
        font-size: 1.8rem !important;
        line-height: 1.6 !important;
        color: #475467 !important;
    }

    .pre-caption {
        text-align: center;
        font-size: 1.4rem !important;
        color: #667085 !important;
        margin-bottom: 1.5rem;
    }

    </style>
    """,
    unsafe_allow_html=True,
)



# ============================================================
# Session State
# ============================================================

if "measurement_done" not in st.session_state: 
    st.session_state.measurement_done = False 
 
if "analysis_result" not in st.session_state: 
    st.session_state.analysis_result = None

if "image_bytes" not in st.session_state:
    st.session_state.image_bytes = None


# ============================================================
# 측정 전 화면
# ============================================================

if not st.session_state.measurement_done:

    empty_left, center, empty_right = st.columns(
        [1, 2, 1]
    )

    with center:

        # ----------------------------------------------------
        # HairSense
        # ----------------------------------------------------

        st.markdown(
            '<div class="pre-title">HairSense</div>',
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 부제목
        # ----------------------------------------------------

        st.markdown(
            '<div class="pre-subtitle">'
            '빗질과 함께 두피 상태를 측정해보세요.'
            '</div>',
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 오늘의 측정
        # ----------------------------------------------------

        st.markdown(
            '<div class="pre-section-title">'
            '🪮 오늘의 측정'
            '</div>',
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 측정 안내
        # ----------------------------------------------------

        st.markdown(
            """
            <h3 style="text-align:center;">
                측정 준비가 완료되었습니다.
            </h3>

            <p style="text-align: center;">
                빗의 버튼을 누르면<br>
                두피 촬영과 센서 데이터 수집이 시작됩니다.
            </p>
            """,
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 테스트 안내
        # ----------------------------------------------------

        st.markdown(
            '<div class="pre-caption">'
            '측정이 끝나면 아래 버튼으로 저장된 결과를 확인해 주세요.'
            '</div>',
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 실제 측정 상태 확인
        # ----------------------------------------------------

        if st.button(
            "측정 결과 확인",
            type="primary",
            use_container_width=True,
        ):

            # 서버에서 센서 분석 결과 가져오기
            result = get_analysis_result()
            # Preserve the clicked sensor snapshot independently of image availability.
            st.session_state.real_sensor_snapshot = result

            if not result or not result.get("summary", {}).get("sensors"):
                st.warning("아직 센서 분석 결과가 없습니다.")
            else:
                # An optional image never blocks the completed sensor screen.
                image_bytes = get_session_image(result)
                # 분석 결과 저장
                st.session_state.analysis_result = result

                # API에서 받은 이미지 원본 저장
                st.session_state.image_bytes = image_bytes

                # 측정 완료
                st.session_state.measurement_done = True

                st.rerun()

else:
    analysis_result = st.session_state.analysis_result

    if analysis_result is None:
        st.error("분석 결과가 없습니다.")
        st.stop()

    # ====================================================
    # 측정 후 화면
    # ====================================================

    header_left, header_right = st.columns(
        [4.5, 1],
        gap="medium",
    )

    with header_left:
        st.title("HairSense")
        st.write(
            "빗질과 함께 두피 상태를 측정해보세요."
        )

    with header_right:
        st.write("")
        st.write("")

        if st.button(
            "🔄 다시 측정하기",
            use_container_width=True,
        ):
            st.session_state.measurement_done = False
            st.session_state.analysis_result = None
            st.session_state.image_bytes = None
            st.session_state.real_sensor_snapshot = None
            st.session_state.image_load_error = None
            st.rerun()

    st.markdown(
        """
        <style>
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] button {
            border: 2px solid #87CEEB !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


    # ====================================================
    # 좌우 메인 영역
    # ====================================================

    left_col, right_col = st.columns(
        [1, 1.35],
        gap="medium",
    )


    # ====================================================
    # 왼쪽 - 측정 이미지
    # ====================================================

    with left_col:

        # 측정 이미지 제목 + 측정 시각
        image_title_col, image_time_col = st.columns(
            [1.6, 1],
            gap="small",
        )

        with image_title_col:
            st.subheader("📷 측정 이미지")

        with image_time_col:
            optical_record = analysis_result.get("summary", {}).get("sensors", {}).get("optical", {})
            if optical_record.get("recorded_at"):
                st.caption(f"광학 측정 저장 · {optical_record['recorded_at']} (UTC)")


        # 이미지 박스
        image_box = st.container(
            border=True,
            height=600,
        )

        with image_box:

            st.write("")

            image_center_left, image_center, image_center_right = (
                st.columns([1, 2, 1])
            )

            with image_center:

                image_bytes = st.session_state.get("image_bytes")

                if image_bytes:

                    st.image(
                        image_bytes,
                        caption="실제 ESP32 촬영 이미지",
                        use_container_width=True,
                    )

                else:
                    st.info(st.session_state.get("image_load_error") or "이번 측정에서 수신된 이미지가 없습니다.")


        # 측정 완료 메시지
        st.success(
            "측정이 완료되었습니다."
        )


    # ====================================================
    # 오른쪽 - 센서 및 분석 결과
    # ====================================================

    with right_col:



        render_sensor(st.session_state.get("real_sensor_snapshot"), "optical")
        render_sensor(st.session_state.get("real_sensor_snapshot"), "gyro")

        st.divider()

        # ====================================================
        # 평소와 비교 - 막대그래프
        # ====================================================


        st.subheader("📊 나의 두피 상태 변화")
        image_history = get_image_history()
        show_image_history_graph(image_history)

        st.divider()


        # ====================================================
        # 오늘의 안내
        # ====================================================

        st.subheader("💡 오늘의 안내")

        feedback = analysis_result.get("feedback")

        if feedback:
            st.write(feedback.get("notice", "센서별 분석 결과를 확인해 주세요."))
        else:
            st.info("현재 안내 결과가 없습니다.")


        st.divider()


        # ====================================================
        # 분석 상태
        # ====================================================

        status_col1, status_col2, status_col3 = st.columns(3)

        with status_col1:
            if st.session_state.get("image_bytes"):
                st.success("이미지 수신")
            else:
                st.info("이미지 없음")

        with status_col2:
            st.success("센서 수신")

        with status_col3:
            sensor_statuses = [row["status"] for row in analysis_result.get("summary", {}).get("sensors", {}).values()]
            if sensor_statuses and all(status == "completed" for status in sensor_statuses):
                st.success("센서 분석 완료")
            else:
                st.info("센서별 수집·분석 상태를 확인해 주세요.")
