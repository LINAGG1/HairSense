"""Gemini 센서 안내: 서버의 관측 문장 고정 + 짧은 안내 생성, 실패 시 기본 문구."""

import json
import math
import os
import re
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_MODEL = "gemini-3.5-flash-lite"
PROMPT_VERSION = "sensor_feedback_2"
NOTICE = "가상 데이터로 만든 시험 안내입니다. 이상 표시 비율은 건강 위험 확률이 아닙니다."
FACTS = {
    "optical_high": "이번 측정의 일부 구간에서 광학 반사 측정값이 평소 기준보다 높게 나타났어요.",
    "optical_low": "이번 측정의 일부 구간에서 광학 반사 측정값이 평소 기준보다 낮게 나타났어요.",
    "optical_mixed": "이번 측정에서 광학 반사 측정값이 평소 기준보다 높은 구간과 낮은 구간이 함께 나타났어요.",
    "optical_pattern": "이번 측정의 일부 구간에서 평소 기준과 다른 광학 측정 패턴이 표시되었어요.",
    "optical_unflagged": "이번 측정에서는 설정된 기준을 넘는 광학 구간이 표시되지 않았어요.",
    "gyro_rapid": "이번 측정의 일부 구간에서 급격한 움직임 변화 횟수가 평소 기준을 넘었어요.",
    "gyro_pattern": "이번 측정의 일부 구간에서 평소 기준과 다른 움직임 패턴이 표시되었어요.",
    "gyro_unflagged": "이번 측정에서는 설정된 기준을 넘는 움직임 구간이 표시되지 않았어요.",
}
DEFAULT_TIPS = {
    "optical": "다음에도 비슷한 조명과 거리에서 사용해 보세요.",
    "gyro": "다음에도 일정한 속도로 부드럽게 빗어보세요.",
}

# Return only fixed rule names, never a substring of the provider response.
ADVICE_RULES = [
    ("number_or_markup", r"[0-9%<>]"), ("url", r"https?://"),
] + [("term:" + word, re.escape(word)) for word in (
    "탈모", "염증", "피지", "질환", "진단", "치료", "위험", "건강", "정상", "확률",
    "압력", "세게", "강하게", "증가", "감소", "높", "낮", "평소", "오늘", "측정",
    "표준편차", "평균", "많", "적게", "크게", "작게",
)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(StrictModel):
    finding: str
    message: str = Field(min_length=10, max_length=260)


class Messages(StrictModel):
    optical: Message
    gyro: Message


class FeedbackResult(StrictModel):
    source: Literal["gemini", "fallback"]
    fallback_reason: str | None
    model: str | None
    provider_http_status: int | None = None
    provider_error_status: str | None = None
    provider_error_reason: str | None = None
    validation_error_code: str | None = None
    validation_error_sensor: Literal["optical", "gyro"] | None = None
    validation_error_rules: list[str] = Field(default_factory=list)
    prompt_version: str = PROMPT_VERSION
    generation_mode: str = "fixed_fact_with_generated_tip"
    notice: str = NOTICE
    messages: Messages


def build_evidence(session):
    """개인/기기/세션 ID, 원시 데이터, 이미지 없이 수치 요약만 선택합니다."""
    if session.get("is_synthetic") is not True:
        raise ValueError("Only synthetic feedback is supported")
    evidence = {"is_synthetic": True, "scope": "one_session_not_daily_diagnosis", "sensors": {}}
    for sensor in ("optical", "gyro"):
        summary = session["sensors"][sensor]
        if summary.get("status") != "summary_only" or summary.get("session_verdict") is not None:
            raise ValueError("Unsupported summary status")
        selected = {key: summary[key] for key in (
            "valid_duration_s", "valid_windows", "flagged_windows",
            "flagged_duration_percent", "longest_flagged_run_s",
        )}
        if not all(type(v) in (int, float) and math.isfinite(v) for v in selected.values()):
            raise ValueError("Invalid evidence numbers")
        reasons = summary.get("rule_reason_windows", {})
        allowed = ("optical_mean_increased", "optical_mean_decreased") if sensor == "optical" else ("gyro_rapid_changes_increased",)
        counts = {key: reasons.get(key, 0) for key in allowed}
        if any(type(v) is not int or v < 0 for v in counts.values()) or sum(counts.values()) > selected["flagged_windows"]:
            raise ValueError("Invalid observation counts")
        if sensor == "optical":
            high, low = counts["optical_mean_increased"], counts["optical_mean_decreased"]
            finding = "optical_mixed" if high and low else "optical_high" if high else "optical_low" if low else "optical_pattern" if selected["flagged_windows"] else "optical_unflagged"
        else:
            finding = "gyro_rapid" if counts["gyro_rapid_changes_increased"] else "gyro_pattern" if selected["flagged_windows"] else "gyro_unflagged"
        evidence["sensors"][sensor] = {**selected, "rule_reason_windows": counts,
                                         "finding": finding, "fixed_fact": FACTS[finding]}
    return evidence


def fallback_messages(evidence):
    return Messages(**{
        sensor: Message(finding=item["finding"], message=item["fixed_fact"] + " " + DEFAULT_TIPS[sensor])
        for sensor, item in evidence["sensors"].items()
    })


def request_payload(evidence):
    schema = {"type": "object", "additionalProperties": False, "required": ["optical", "gyro"], "properties": {}}
    for sensor, item in evidence["sensors"].items():
        schema["properties"][sensor] = {
            "type": "object", "additionalProperties": False,
            "properties": {"finding": {"type": "string", "enum": [item["finding"]]},
                           "message": {"type": "string"}},
            "required": ["finding", "message"],
        }
    instructions = (
        "너는 HairSense의 한국어 센서 안내 문장 편집자다. 수치 계산이나 판정을 하지 않는다. "
        "각 message는 해당 fixed_fact를 한 글자도 바꾸지 않고 그대로 시작한다. "
        "그 뒤에 선택적으로 짧고 정중한 사용 안내 한 문장을 덧붙인다. 전체 길이는 260자 이내. "
        "추가 안내는 광학이면 비슷한 조명/거리 유지, 자이로면 일정한 속도로 부드럽게 빗기만 다룬다. "
        "광학 안내 예: '측정할 때는 비슷한 조명과 거리를 유지해 주세요.' "
        "측정을 언급한다면 추가 안내 문장 맨 앞에 '측정할 때는' 또는 '측정 시에는'만 사용한다. "
        "추가 관측 사실, 원인 추정, 새로운 증가/감소 판단, 숫자, 비율, 빈도, 하루 전체 판단을 만들지 않는다. "
        "질병/탈모/피지/염증/건강/위험/진단/정상 판정이나 치료 권고는 하지 않는다. "
        "이상으로 표시되지 않았다는 사실은 건강하다는 뜻이 아니다. "
        "finding은 입력값 그대로 반환한다. JSON만 출력한다."
    )
    return {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(evidence, ensure_ascii=False, allow_nan=False)}]}],
        "generationConfig": {"maxOutputTokens": 2048,
                             "responseMimeType": "application/json", "responseJsonSchema": schema},
    }


class OutputValidationError(ValueError):
    def __init__(self, code, sensor=None, rules=()):
        super().__init__(code)
        self.code, self.sensor = code, sensor
        self.rules = list(rules)


def validate_messages(raw, evidence):
    try:
        messages = Messages.model_validate(raw)
    except ValidationError as exc:
        location = exc.errors(include_input=False)[0]["loc"]
        sensor = location[0] if location and location[0] in ("optical", "gyro") else None
        raise OutputValidationError("message_schema_mismatch", sensor) from None
    for sensor, item in evidence["sensors"].items():
        result = getattr(messages, sensor)
        if result.finding != item["finding"]:
            raise OutputValidationError("finding_changed", sensor)
        if not result.message.startswith(item["fixed_fact"]):
            raise OutputValidationError("fixed_fact_changed", sensor)
        tip = result.message[len(item["fixed_fact"]):].strip()
        # 보수적인 문자열 검사이며 자유 문장의 의미를 완전히 검증하는 장치는 아닙니다.
        # Allow only a leading usage context, not arbitrary mentions of measurements.
        # Keep the original message; normalize only the text used by the word filter.
        checked_tip = re.sub(
            r"^(?:다음(?:에도|에는|에)?\s+)?측정(?:할 때(?:는|에도)?| 시(?:에는|에도|는)?)[,\s]+",
            "", tip, count=1,
        )
        rules = [name for name, pattern in ADVICE_RULES if re.search(pattern, checked_tip)]
        if rules:
            raise OutputValidationError("unsupported_advice_content", sensor, rules)
        if tip and (not re.search(r"[가-힣]", tip) or not tip.endswith(("세요.", "봐요.", "보아요."))):
            raise OutputValidationError("advice_language_or_ending", sensor)
    return messages


def reject_constant(value):
    raise ValueError("Non-finite JSON")


def provider_error_details(response):
    """오류 원문/메타데이터 대신 알려진 상태 코드만 반환합니다."""
    statuses = {
        "INVALID_ARGUMENT", "FAILED_PRECONDITION", "NOT_FOUND", "PERMISSION_DENIED",
        "UNAUTHENTICATED", "RESOURCE_EXHAUSTED", "INTERNAL", "UNAVAILABLE",
        "DEADLINE_EXCEEDED", "UNIMPLEMENTED", "OUT_OF_RANGE", "ABORTED", "UNKNOWN",
    }
    reasons = {
        "API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED",
        "API_KEY_ANDROID_APP_BLOCKED", "API_KEY_IOS_APP_BLOCKED",
        "SERVICE_DISABLED", "BILLING_DISABLED", "CONSUMER_INVALID",
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "RATE_LIMIT_EXCEEDED",
    }
    result = {"provider_error_status": None, "provider_error_reason": None}
    try:
        data = response.json()
    except ValueError:
        return result
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return result
    status = error.get("status")
    if isinstance(status, str) and status in statuses:
        result["provider_error_status"] = status
    details = error.get("details", [])
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            reason = detail.get("reason")
            if isinstance(reason, str) and reason in reasons:
                result["provider_error_reason"] = reason
                break
    return result


def generate_feedback(session):
    evidence = build_evidence(session)
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip()
    model_valid = bool(re.fullmatch(r"gemini-[A-Za-z0-9.-]{1,80}", model))
    diagnostics = {}
    def fallback(reason):
        return FeedbackResult(source="fallback", fallback_reason=reason,
                              model=model if model_valid else None, messages=fallback_messages(evidence),
                              **diagnostics)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return fallback("missing_api_key")
    if not model_valid:
        return fallback("invalid_model_configuration")
    try:
        # 키는 URL/본문/로그에 넣지 않습니다. 리다이렉트와 자동 재시도도 사용하지 않습니다.
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=request_payload(evidence), timeout=(5, 25), allow_redirects=False,
        )
    except requests.Timeout:
        return fallback("provider_timeout")
    except requests.RequestException:
        return fallback("provider_unavailable")
    validation_stage = "provider_response_structure"
    try:
        diagnostics["provider_http_status"] = response.status_code
        if response.status_code != 200:
            diagnostics.update(provider_error_details(response))
        if diagnostics.get("provider_error_reason") in ("API_KEY_INVALID", "API_KEY_EXPIRED"):
            return fallback("provider_auth_error")
        if response.status_code in (401, 403):
            return fallback("provider_auth_error")
        if response.status_code == 429:
            return fallback("provider_rate_limited")
        if response.status_code == 404:
            return fallback("provider_model_unavailable")
        if response.status_code == 400:
            return fallback("provider_invalid_request")
        if response.status_code != 200:
            return fallback("provider_request_rejected")
        # 공급자 오류 본문은 API 응답이나 로그에 노출하지 않습니다.
        validation_stage = "provider_response_not_json"
        data = response.json()
        validation_stage = "provider_response_structure"
        candidate = data["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            return fallback("provider_response_incomplete")
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"]
                       if not part.get("thought", False))
        if len(text) > 4000:
            diagnostics["validation_error_code"] = "generated_text_too_long"
            return fallback("output_validation_failed")
        validation_stage = "generated_json_invalid"
        raw = json.loads(text, parse_constant=reject_constant)
        messages = validate_messages(raw, evidence)
    except OutputValidationError as exc:
        diagnostics["validation_error_code"] = exc.code
        diagnostics["validation_error_sensor"] = exc.sensor
        diagnostics["validation_error_rules"] = exc.rules
        return fallback("output_validation_failed")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        diagnostics["validation_error_code"] = validation_stage
        return fallback("output_validation_failed")
    finally:
        response.close()
    return FeedbackResult(source="gemini", fallback_reason=None, model=model,
                          provider_http_status=200, messages=messages)
