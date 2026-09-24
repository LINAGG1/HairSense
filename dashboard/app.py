import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt


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
# Mock Data
# ============================================================

previous_result = {
    "미세각질": 1,
    "피지과다": 1,
    "모낭사이홍반": 2,
    "모낭홍반/농포": 1,
    "비듬": 2,
    "탈모": 1,
}

today_result = {
    "미세각질": 2,
    "피지과다": 1,
    "모낭사이홍반": 2,
    "모낭홍반/농포": 2,
    "비듬": 1,
    "탈모": 1,
}

severity_names = [
    "양호",
    "경증",
    "중등도",
    "중증",
]


# ============================================================
# 센서 Mock
# ============================================================

sensor_snapshot = {
    "광학 반사": 412,
    "압력": 723,
    "빗질 움직임": "변화 큼",
}

sensor_baseline = {
    "광학 반사": 390,
    "압력": 680,
    "빗질 움직임": "일정",
}


# ============================================================
# Gemini Mock
# ============================================================

gemini_feedback = [
    (
        "유분 변화",
        "오늘 두피의 광학 반사 특성이 평소보다 높게 측정됐어요. "
        "평소보다 유분이 증가했을 가능성이 있습니다.",
    ),
    (
        "빗질 움직임",
        "오늘의 빗질은 평소보다 움직임의 변화가 컸어요. "
        "조금 더 일정한 움직임으로 빗어보세요.",
    ),
]


# ============================================================
# Session State
# ============================================================

if "measurement_done" not in st.session_state:
    st.session_state.measurement_done = False


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
            '현재는 UI 테스트를 위해 아래 버튼으로 측정을 대신합니다.'
            '</div>',
            unsafe_allow_html=True,
        )

        # ----------------------------------------------------
        # 테스트 측정 시작
        # ----------------------------------------------------

        if st.button(
            "테스트 측정 시작",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.measurement_done = True
            st.rerun()


else:

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
            st.markdown(
                """
                <div style="
                    font-size: 1.5rem;
                    font-weight: 400;
                    margin-top: 2.00rem;
                    white-space: nowrap;
                ">
                    측정 시각 · 2026.09.24 09:20
                </div>
                """,
                unsafe_allow_html=True,
            )


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

                st.write("")

                st.markdown(
                    "### 🪮"
                )

                st.write(
                    "두피 측정 이미지"
                )

                st.caption(
                    "ESP32 카메라 촬영 이미지가 "
                    "여기에 표시됩니다."
                )


        st.caption(
            "실제 서버 연결 후 촬영된 이미지가 표시됩니다."
        )

        # 측정 완료 메시지
        st.success(
            "측정이 완료되었습니다."
        )


    # ====================================================
    # 오른쪽 - 센서 및 분석 결과
    # ====================================================

    with right_col:

        st.subheader(
            "📊 촬영 당시 센서 상태"
        )

        sensor_col1, sensor_col2, sensor_col3 = st.columns(3)

        with sensor_col1:

            optical_diff = (
                sensor_snapshot["광학 반사"]
                - sensor_baseline["광학 반사"]
            )

            st.metric(
                "광학 반사",
                sensor_snapshot["광학 반사"],
                f"+{optical_diff}",
            )

        with sensor_col2:

            pressure_diff = (
                sensor_snapshot["압력"]
                - sensor_baseline["압력"]
            )

            st.metric(
                "압력",
                sensor_snapshot["압력"],
                f"+{pressure_diff}",
            )

        with sensor_col3:

            st.metric(
                "빗질 움직임",
                sensor_snapshot["빗질 움직임"],
            )


        st.divider()

        # ====================================================
        # 평소와 비교 - 막대그래프
        # ====================================================

                # ====================================================
        # 평소와 비교 - 막대그래프
        # ====================================================

        st.subheader(
            "🧠 평소와 비교"
        )

        categories = list(today_result.keys())

        previous_values = [
            previous_result[name]
            for name in categories
        ]

        today_values = [
            today_result[name]
            for name in categories
        ]

        x = list(range(len(categories)))
        width = 0.34

        fig, ax = plt.subplots(
            figsize=(10, 5.5)
        )

        # 평소
        ax.bar(
            [i - width / 2 for i in x],
            previous_values,
            width=width,
            label="평소",
            color="#F0EEB8",
            edgecolor="none",
            zorder=3,
        )

        # 오늘
        ax.bar(
            [i + width / 2 for i in x],
            today_values,
            width=width,
            label="오늘",
            color="#99F9A1",
            edgecolor="none",
            zorder=3,
        )

        # X축
        ax.set_xticks(x)

        ax.set_xticklabels(
            categories,
            fontsize=11,
        )

        # Y축
        ax.set_yticks(
            [0, 1, 2, 3]
        )

        ax.set_yticklabels(
            [
                "양호",
                "경증",
                "중등도",
                "중증",
            ],
            fontsize=11,
        )

        ax.set_ylim(
            0,
            3.4,
        )

        # 축 제목
        ax.set_xlabel(
            "두피 상태 항목",
            fontsize=12,
            labelpad=10,
        )

        ax.set_ylabel(
            "상태",
            fontsize=12,
            labelpad=10,
        )

        # 제목/테두리 정리
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # 격자
        ax.grid(
            axis="y",
            alpha=0.18,
            linewidth=0.8,
            zorder=0,
        )

        # 범례
        ax.legend(
            loc="upper right",
            frameon=False,
            fontsize=11,
        )

        # 여백
        plt.tight_layout()

        st.pyplot(
            fig,
            use_container_width=True,
        )

        plt.close(fig)

        st.caption(
            "양호 0 · 경증 1 · 중등도 2 · 중증 3"
        )


        st.divider()


        # ====================================================
        # 오늘의 안내
        # ====================================================

        st.subheader(
            "💡 오늘의 안내"
        )

        for title, message in gemini_feedback:

            with st.container(
                border=True
            ):

                st.write(
                    f"**{title}**"
                )

                st.write(
                    message
                )


        st.divider()


        # ====================================================
        # 분석 상태
        # ====================================================

        status_col1, status_col2, status_col3 = st.columns(3)

        with status_col1:
            st.success("촬영 완료")

        with status_col2:
            st.success("센서 수신")

        with status_col3:
            st.success("AI 분석 완료")