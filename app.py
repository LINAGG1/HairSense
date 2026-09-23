import streamlit as st
import torch
import torch.nn as nn
from torchvision.models import efficientnet_b0
from PIL import Image
from torchvision import transforms


# =========================
# 기본 설정
# =========================

st.set_page_config(
    page_title="HairSense",
    page_icon="🪮",
    layout="centered"
)

MODEL_PATH = "best_effnet_b0_focused.pth"

LABELS = [
    "미세각질",
    "피지과다",
    "모낭사이홍반",
    "모낭홍반/농포",
    "비듬",
    "탈모"
]


# =========================
# 모델 정의
# =========================

class HairSenseModel(nn.Module):
    def __init__(self):
        super().__init__()

        self.backbone = efficientnet_b0(weights=None)

        feature_dim = self.backbone.classifier[1].in_features

        self.backbone.classifier = nn.Identity()

        self.heads = nn.ModuleList([
            nn.Linear(feature_dim, 4)
            for _ in range(6)
        ])

    def forward(self, x):
        features = self.backbone(x)

        return [
            head(features)
            for head in self.heads
        ]


# =========================
# 모델 로드
# =========================

@st.cache_resource
def load_model():

    device = torch.device("cpu")

    model = HairSenseModel()

    checkpoint = torch.load(
        MODEL_PATH,
        map_location=device
    )

    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        model.load_state_dict(checkpoint["model_state_dict"])
    else:
        model.load_state_dict(checkpoint)

    model.to(device)
    model.eval()

    return model


# =========================
# 이미지 전처리
# =========================

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    )
])


# =========================
# AI 분석
# =========================

def predict(image, model):

    device = torch.device("cpu")

    image_tensor = transform(image).unsqueeze(0)
    image_tensor = image_tensor.to(device)

    with torch.no_grad():

        outputs = model(image_tensor)

    results = []

    for label, output in zip(LABELS, outputs):

        probabilities = torch.softmax(output, dim=1)

        confidence, prediction = torch.max(
            probabilities,
            dim=1
        )

        results.append({
            "label": label,
            "grade": prediction.item(),
            "confidence": confidence.item()
        })

    return results


# =========================
# 화면
# =========================

st.title("🪮 HairSense")
st.subheader("AI 기반 두피 상태 분석")

st.write(
    "두피 이미지를 업로드하면 AI 모델이 "
    "6가지 두피 상태를 0~3등급으로 분석합니다."
)

uploaded_file = st.file_uploader(
    "두피 이미지를 업로드하세요.",
    type=["jpg", "jpeg", "png"]
)


if uploaded_file is not None:

    image = Image.open(uploaded_file).convert("RGB")

    st.image(
        image,
        caption="업로드된 이미지",
        use_container_width=True
    )

    if st.button("AI 분석 시작"):

        with st.spinner("AI가 두피 상태를 분석하고 있습니다..."):

            model = load_model()

            results = predict(
                image,
                model
            )

        st.success("분석이 완료되었습니다.")

        st.divider()

        st.subheader("분석 결과")

        for result in results:

            label = result["label"]
            grade = result["grade"]
            confidence = result["confidence"]

            st.write(
                f"**{label}** : {grade}등급"
            )

            st.progress(
                confidence,
                text=f"신뢰도 {confidence * 100:.1f}%"
            )

        st.divider()

        st.caption(
            "※ 본 결과는 AI 모델의 예측 결과이며 "
            "의학적 진단을 대신하지 않습니다."
        )