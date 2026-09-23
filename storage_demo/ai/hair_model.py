from pathlib import Path

import torch
import torch.nn as nn
from torchvision.models import efficientnet_b0
from torchvision import transforms


LABELS = [
    "미세각질",
    "피지과다",
    "모낭사이홍반",
    "모낭홍반/농포",
    "비듬",
    "탈모",
]


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
        return [head(features) for head in self.heads]


# hair_model.py
#   ↓ parent
# ai
#   ↓ parent
# storage_demo
#   ↓ parent
# HairSense
MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "best_effnet_b0_focused.pth"
)


transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225],
    ),
])


def load_model():
    device = torch.device(
        "xpu" if torch.xpu.is_available() else "cpu"
    )

    model = HairSenseModel()

    checkpoint = torch.load(
        MODEL_PATH,
        map_location="cpu",
    )

    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        model.load_state_dict(checkpoint["model_state_dict"])
    else:
        model.load_state_dict(checkpoint)

    model.to(device)
    model.eval()

    return model, device


def predict(image, model, device):
    image_tensor = transform(image).unsqueeze(0)
    image_tensor = image_tensor.to(device)

    with torch.no_grad():
        outputs = model(image_tensor)

    results = []

    for label, output in zip(LABELS, outputs):
        probabilities = torch.softmax(output, dim=1)

        confidence, prediction = torch.max(
            probabilities,
            dim=1,
        )

        results.append({
            "label": label,
            "grade": int(prediction.item()),
            "confidence": float(confidence.item()),
        })

    return results