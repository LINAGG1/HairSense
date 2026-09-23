"""Local synthetic DB pipeline. Persisted state prevents automatic provider retries."""
import hashlib
import json
import logging
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sensor_feedback import generate_feedback
from sensor_pipeline import VERSION, analyze_rows, load_models, json_text

log = logging.getLogger(__name__)
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
WHERE = "device_id=%s AND session_id=%s AND boot_id=%s"


class Completion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: Identifier
    device_id: Identifier
    boot_id: Identifier
    is_synthetic: Literal[True] = True
    schema_version: Literal[1] = 1
    start_timestamp_ms: int = Field(ge=0, le=18446744073709491615)
    duration_seconds: Literal[60] = 60
    sample_rate_hz: Literal[50] = 50
    interval_ms: Literal[20] = 20
    optical_unit: Literal["V"] = "V"
    gyro_unit: Literal["deg/s"] = "deg/s"


class FeedbackRetry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    user_id: Identifier
    device_id: Identifier
    boot_id: Identifier
    retry_id: Identifier


@contextmanager
def session_lock(connection, key):
    name = "hs:" + hashlib.sha256(json_text(key).encode()).hexdigest()[:60]
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 0)", (name,))
        if cursor.fetchone()[0] != 1:
            raise HTTPException(409, "session_busy: retry after the active request finishes")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
            cursor.fetchone()


def register_session(cursor, key, user_id):
    cursor.execute("SELECT DISTINCT user_id,is_synthetic FROM sensor_readings WHERE " + WHERE, key)
    if any(r["user_id"] != user_id or not r["is_synthetic"] for r in cursor.fetchall()):
        raise HTTPException(409, "session_owner_or_source_mismatch")
    cursor.execute(
        "INSERT INTO sensor_sessions (device_id,session_id,boot_id,user_id) VALUES (%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE device_id=device_id", (*key, user_id))
    cursor.execute("SELECT user_id,status,metadata_json FROM sensor_sessions WHERE " + WHERE, key)
    session = cursor.fetchone()
    if session["user_id"] != user_id:
        raise HTTPException(409, "session_owner_mismatch")
    return session


def require_receiving(connection, key, user_id):
    with connection.cursor(dictionary=True) as cursor:
        session = register_session(cursor, key, user_id)
        if session["status"] != "receiving":
            raise HTTPException(409, "session_sealed: no more batches accepted")


def decode(value):
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


def save_run(connection, key, result):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE sensor_analysis_runs SET status=%s,result_json=%s WHERE " + WHERE,
                       (result["status"], json_text(result), *key))
    connection.commit()


def finish_analysis(connection, key, rows, meta, bundles, provenance):
    try:
        result = analyze_rows(rows, meta, bundles, provenance)
    except Exception:
        log.exception("Sensor analysis failed")
        result = {"status": "analysis_failed", "pipeline_version": VERSION,
                  "error_code": "analysis_failed", "feedback": None}
        save_run(connection, key, result)
        return result
    if result["status"] == "insufficient_data":
        save_run(connection, key, result)
        return result
    # Commit before external side effect. A crash after this point is NOT auto-retried.
    result["status"] = "feedback_pending"
    save_run(connection, key, result)
    try:
        feedback = generate_feedback(result["summary"]).model_dump()
    except Exception:
        # Do not log exceptions that might contain credentials/provider payloads.
        result["status"] = "feedback_failed"
        result["error_code"] = "feedback_exception_no_automatic_retry"
    else:
        result["feedback"] = feedback
        result["status"] = "completed"
    save_run(connection, key, result)
    return result


def replay_result(cursor, key, session, meta):
    if session["metadata_json"] is not None and decode(session["metadata_json"]) != meta:
        raise HTTPException(409, "completion_metadata_conflict")
    cursor.execute("SELECT result_json FROM sensor_analysis_runs WHERE " + WHERE, key)
    old = cursor.fetchone()
    if old:
        result = decode(old["result_json"])
        if result["status"] in ("processing", "feedback_pending", "feedback_retry_pending"):
            raise HTTPException(409, "recovery_required: interrupted run; provider call will not be retried automatically")
        return {**result, "cached": True}
    if session["status"] != "receiving":
        raise HTTPException(409, "sealed_session_without_run")
    return None


def make_router(database):
    router = APIRouter(prefix="/sensor-sessions", tags=["Sensor DB pipeline (synthetic)"])

    @router.post("/{session_id}/complete")
    def complete(session_id: Identifier, request: Completion):
        meta = {**request.model_dump(), "session_id": session_id}
        key = (request.device_id, session_id, request.boot_id)
        with database() as connection, session_lock(connection, key):
            with connection.cursor(dictionary=True) as cursor:
                session = register_session(cursor, key, request.user_id)
                cached = replay_result(cursor, key, session, meta)
                if cached is not None:
                    return cached
                cursor.execute("SELECT device_id,session_id,boot_id,user_id,is_synthetic,schema_version,seq,"
                               "timestamp_ms,optical,gyro_x,gyro_y,gyro_z FROM sensor_readings WHERE " + WHERE +
                               " ORDER BY seq LIMIT 3001", key)
                rows = cursor.fetchall()
                if not rows:
                    raise HTTPException(409, "no_sensor_readings")
                if len(rows) > 3000:
                    raise HTTPException(422, "only_60_second_sessions_supported")
                try:
                    bundles, provenance = load_models(meta)
                except (OSError, ValueError, KeyError):
                    raise HTTPException(422, "model_missing_or_incompatible_with_session") from None
                initial = {"status": "processing", "pipeline_version": VERSION, "metadata": meta,
                           "provenance": provenance, "feedback": None}
                cursor.execute("UPDATE sensor_sessions SET status='sealed',metadata_json=%s WHERE " + WHERE,
                               (json_text(meta), *key))
                cursor.execute("INSERT INTO sensor_analysis_runs "
                               "(device_id,session_id,boot_id,pipeline_version,status,result_json) "
                               "VALUES (%s,%s,%s,%s,%s,%s)", (*key, VERSION, "processing", json_text(initial)))
            connection.commit()
            return {**finish_analysis(connection, key, rows, meta, bundles, provenance), "cached": False}

    @router.get("/{session_id}/analysis")
    def analysis(session_id: Identifier, device_id: Identifier, boot_id: Identifier, user_id: Identifier):
        key = (device_id, session_id, boot_id)
        with database() as connection, connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT user_id FROM sensor_sessions WHERE " + WHERE, key)
            session = cursor.fetchone()
            if not session or session["user_id"] != user_id:
                raise HTTPException(404, "session_not_found")
            cursor.execute("SELECT result_json FROM sensor_analysis_runs WHERE " + WHERE, key)
            run = cursor.fetchone()
            if not run:
                raise HTTPException(404, "analysis_not_ready")
            return decode(run["result_json"])

    @router.post("/{session_id}/feedback/retry")
    def retry_feedback(session_id: Identifier, request: FeedbackRetry):
        key = (request.device_id, session_id, request.boot_id)
        with database() as connection, session_lock(connection, key):
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT user_id FROM sensor_sessions WHERE " + WHERE, key)
                session = cursor.fetchone()
                if not session or session["user_id"] != request.user_id:
                    raise HTTPException(404, "session_not_found")
                cursor.execute("SELECT result_json FROM sensor_analysis_runs WHERE " + WHERE, key)
                run = cursor.fetchone()
                if not run:
                    raise HTTPException(404, "analysis_not_ready")
                result = decode(run["result_json"])
            history = result.setdefault("feedback_retries", {})
            if result["status"] in ("processing", "feedback_pending", "feedback_retry_pending") or any(
                attempt["status"] == "pending" for attempt in history.values()
            ):
                raise HTTPException(409, "recovery_required: uncertain provider attempt; no automatic retry")
            if request.retry_id in history:
                attempt = history[request.retry_id]
                return {"retry_id": request.retry_id, "cached": True,
                        "status": attempt["status"], "feedback": attempt.get("feedback"),
                        "error_code": attempt.get("error_code")}
            if result["status"] != "completed" or not result.get("summary") or not result.get("feedback"):
                raise HTTPException(409, "completed_summary_and_fallback_required")
            if result["feedback"]["source"] == "gemini":
                return {"retry_id": request.retry_id, "cached": True,
                        "status": "already_gemini", "feedback": result["feedback"], "error_code": None}
            if result["feedback"]["source"] != "fallback":
                raise HTTPException(409, "fallback_required")
            if len(history) >= 20:
                raise HTTPException(409, "retry_limit_reached: inspect repeated failures")
            attempt = {"status": "pending", "previous_feedback": result["feedback"]}
            history[request.retry_id] = attempt
            result["status"] = "feedback_retry_pending"
            save_run(connection, key, result)
            try:
                feedback = generate_feedback(result["summary"]).model_dump()
            except Exception:
                attempt.update(status="failed", error_code="feedback_exception_no_automatic_retry")
            else:
                attempt.update(status="completed", feedback=feedback)
                result["feedback"] = feedback
            result["status"] = "completed"
            save_run(connection, key, result)
            return {"retry_id": request.retry_id, "cached": False, "status": attempt["status"],
                    "feedback": attempt.get("feedback"), "error_code": attempt.get("error_code")}

    return router
