"""로컬 가상 요약 조회 및 명시적인 POST 요청에 의한 Gemini 안내 생성."""

import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sensor_feedback import FeedbackResult, generate_feedback


ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "analysis_results" / "multi_session" / "rules_holdout_002"
SUMMARY_PATH = RESULT_DIR / "session_summaries.json"
POLICY_PATH = RESULT_DIR / "rule_policy.json"
log = logging.getLogger(__name__)
router = APIRouter(prefix="/sensor-analysis", tags=["Sensor analysis (synthetic preview)"])
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class SummaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)


class SensorSummary(SummaryModel):
    status: Literal["summary_only"]
    session_verdict: None
    expected_windows: int = Field(gt=0)
    valid_windows: int = Field(gt=0)
    valid_duration_s: float = Field(gt=0)
    flagged_windows: int = Field(ge=0)
    flagged_duration_s: float = Field(ge=0)
    flagged_duration_fraction: float = Field(ge=0, le=1)
    flagged_duration_percent: float = Field(ge=0, le=100)
    longest_flagged_run_s: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self):
        if not self.flagged_windows <= self.valid_windows <= self.expected_windows:
            raise ValueError("Inconsistent window counts")
        if not self.longest_flagged_run_s <= self.flagged_duration_s <= self.valid_duration_s:
            raise ValueError("Inconsistent durations")
        if abs(self.flagged_duration_fraction - self.flagged_duration_s / self.valid_duration_s) > 1e-9:
            raise ValueError("Inconsistent duration fraction")
        if abs(self.flagged_duration_percent - round(self.flagged_duration_fraction * 100, 2)) > 1e-8:
            raise ValueError("Inconsistent duration percentage")
        return self


class Sensors(SummaryModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    optical: SensorSummary
    gyro: SensorSummary


class SessionSummary(SummaryModel):
    session_id: Identifier
    boot_id: Identifier
    user_id: Identifier
    device_id: Identifier
    is_synthetic: Literal[True]
    sensors: Sensors


class SummaryDocument(SummaryModel):
    summary_version: Literal["session_summary_demo_1"]
    is_synthetic: Literal[True]
    rule_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sessions: list[SessionSummary] = Field(min_length=1)
    limitations: list[str]

    @model_validator(mode="after")
    def unique_sessions(self):
        if len({s.session_id for s in self.sessions}) != len(self.sessions):
            raise ValueError("Ambiguous session IDs")
        return self


class SessionIndex(SummaryModel):
    session_id: Identifier
    boot_id: Identifier
    user_id: Identifier
    device_id: Identifier
    is_synthetic: Literal[True]


class ResponseInfo(SummaryModel):
    mode: Literal["synthetic_file_preview"] = "synthetic_file_preview"
    summary_version: str
    rule_policy_sha256: str
    limitations: list[str]


class SessionListResponse(ResponseInfo):
    count: int
    items: list[SessionIndex]


class SessionDetailResponse(ResponseInfo):
    session: SessionSummary


class SessionFeedbackResponse(ResponseInfo):
    session_id: Identifier
    feedback: FeedbackResult


def reject_nonfinite(value):
    raise ValueError(f"Nonstandard JSON number: {value}")


def load_document():
    try:
        raw = json.loads(SUMMARY_PATH.read_text(encoding="utf-8-sig"), parse_constant=reject_nonfinite)
        document = SummaryDocument.model_validate(raw)
        if hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest() != document.rule_policy_sha256:
            raise ValueError("Stale summary policy")
        return document
    except FileNotFoundError as exc:
        raise HTTPException(503, detail={
            "code": "summary_not_ready",
            "message": "센서 요약 파일이 없습니다. 분석 및 summarize_sensor_sessions.py 실행이 필요합니다.",
        }) from exc
    except (OSError, ValueError, ValidationError) as exc:
        log.warning("Sensor summary unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, detail={
            "code": "summary_invalid",
            "message": "센서 요약 파일 또는 판정 기준이 일치하지 않습니다. 결과를 다시 생성해 주세요.",
        }) from exc


def response_info(document):
    return {"summary_version": document.summary_version,
            "rule_policy_sha256": document.rule_policy_sha256,
            "limitations": document.limitations}


@router.get("/sessions", response_model=SessionListResponse,
            summary="저장된 가상 세션 목록 조회")
def list_sessions():
    document = load_document()
    items = [SessionIndex(**session.model_dump(exclude={"sensors"})) for session in document.sessions]
    return SessionListResponse(**response_info(document), count=len(items), items=items)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse,
            summary="저장된 가상 세션의 센서 요약 조회")
def get_session(session_id: Annotated[str, ApiPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]):
    document = load_document()
    for session in document.sessions:
        if session.session_id == session_id:
            return SessionDetailResponse(**response_info(document), session=session)
    raise HTTPException(404, detail={"code": "session_not_found", "message": "해당 세션의 요약이 없습니다."})


@router.post("/sessions/{session_id}/feedback", response_model=SessionFeedbackResponse,
             summary="Gemini로 가상 센서 안내 생성 (외부 API 호출)",
             description="요약의 관측 문장을 유지하고 안내를 생성합니다. 호출 실패 시 기본 문구와 사유를 반환합니다. GET 조회는 Gemini를 호출하지 않습니다.")
def create_feedback(session_id: Annotated[str, ApiPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]):
    detail = get_session(session_id)
    try:
        feedback = generate_feedback(detail.session.model_dump())
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(503, detail={"code": "feedback_summary_invalid",
                                        "message": "안내를 만들 센서 요약이 불완전합니다. 요약 파일을 다시 생성해 주세요."}) from exc
    return SessionFeedbackResponse(
        summary_version=detail.summary_version, rule_policy_sha256=detail.rule_policy_sha256,
        limitations=detail.limitations, session_id=session_id, feedback=feedback,
    )
