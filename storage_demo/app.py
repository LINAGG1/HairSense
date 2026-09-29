"""Local-only storage exercise; run from storage_demo with uvicorn app:app."""
import json
import logging
import io
from contextlib import contextmanager, ExitStack
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from ai.hair_model import load_model, predict
from sensor_analysis_api import router as sensor_analysis_router
from sensor_pipeline_api import make_router, session_lock, require_receiving
from real_sensor_service import make_real_sensor_router
from real_capture_storage import (OpticalCapture, GyroCapture, normalize_wire,
                                  store_real_capture, analyze_stored_image)


app = FastAPI(title="HairSense local storage exercise")
app.include_router(sensor_analysis_router)
log = logging.getLogger(__name__)


def validation_fields(errors):
    # Never log uploaded images, raw sensor values, or the full request body.
    return [{"loc": list(error["loc"]), "type": error["type"],
             "msg": error["msg"]} for error in errors]


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    errors = validation_fields(exc.errors())
    log.warning("Request validation failed path=%s content_type=%s fields=%s",
                request.url.path, request.headers.get("content-type", ""), errors)
    response = {"detail": errors}
    if request.url.path in ("/optical", "/ai/analyze"):
        response["hint"] = (
            "Send application/json with optical samples (no image required), or "
            "multipart/form-data with file=image and metadata=optical JSON text."
        )
    return JSONResponse(status_code=422, content=response)

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$"
    )
]

UInt64 = Annotated[
    int,
    Field(
        strict=True,
        ge=0,
        le=18446744073709551615
    )
]


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    boot_id: Identifier
    seq: UInt64
    timestamp_ms: UInt64
    optical: FiniteFloat | None
    gyro_x: FiniteFloat | None
    gyro_y: FiniteFloat | None
    gyro_z: FiniteFloat | None


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    device_id: Identifier
    session_id: Identifier
    user_id: Identifier
    is_synthetic: Literal[True]
    readings: list[Reading] = Field(
        min_length=1,
        max_length=500
    )


@contextmanager
def database():
    connection = None

    try:
        config = json.loads(
            Path(__file__).with_name("db_config.json").read_text(
                encoding="utf-8-sig"
            )
        )

        connection = mysql.connector.connect(
            **config,
            connection_timeout=5
        )

        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")

        yield connection

    except (OSError, ValueError, mysql.connector.Error):
        log.exception("Database connection or operation failed")

        raise HTTPException(
            503,
            "DB unavailable: check configuration, service and setup.sql"
        )

    finally:
        if connection is not None:
            connection.close()


app.include_router(make_router(database))
app.include_router(make_real_sensor_router(database))


# ---------------------------------------------------------
# AI model
# ---------------------------------------------------------

_ai_model = None
_ai_device = None


def get_ai_model():
    global _ai_model, _ai_device

    if _ai_model is None:
        try:
            _ai_model, _ai_device = load_model()
            log.info(
                "HairSense AI model loaded on %s",
                _ai_device
            )
        except Exception:
            log.exception("Failed to load HairSense AI model")

            raise HTTPException(
                500,
                "AI model could not be loaded"
            )

    return _ai_model, _ai_device

# ---------------------------------------------------------
# Image analysis DB
# ---------------------------------------------------------

def save_image_analysis(
    user_id: str,
    session_id: str | None,
    results: list[dict],
):
    result_map = {
        item["label"]: item
        for item in results
    }

    required_labels = [
        "미세각질",
        "피지과다",
        "모낭사이홍반",
        "모낭홍반/농포",
        "비듬",
        "탈모",
    ]

    for label in required_labels:
        if label not in result_map:
            raise ValueError(
                f"Missing AI result: {label}"
            )

    values = (
        user_id,
        session_id,

        result_map["미세각질"]["grade"],
        result_map["미세각질"]["confidence"],

        result_map["피지과다"]["grade"],
        result_map["피지과다"]["confidence"],

        result_map["모낭사이홍반"]["grade"],
        result_map["모낭사이홍반"]["confidence"],

        result_map["모낭홍반/농포"]["grade"],
        result_map["모낭홍반/농포"]["confidence"],

        result_map["비듬"]["grade"],
        result_map["비듬"]["confidence"],

        result_map["탈모"]["grade"],
        result_map["탈모"]["confidence"],
    )

    with database() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO image_analysis_results (
                user_id,
                session_id,

                micro_scale_grade,
                micro_scale_confidence,

                excess_sebum_grade,
                excess_sebum_confidence,

                perifollicular_erythema_grade,
                perifollicular_erythema_confidence,

                follicular_erythema_pustule_grade,
                follicular_erythema_pustule_confidence,

                dandruff_grade,
                dandruff_confidence,

                hair_loss_grade,
                hair_loss_confidence
            )
            VALUES (
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            )
            """,
            values,
        )

        connection.commit()

        return cursor.lastrowid

# ---------------------------------------------------------
# Health
# ---------------------------------------------------------

@app.get("/health")
def health():
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sensor_readings LIMIT 1")
        cursor.fetchall()

    return {
        "status": "ok",
        "database": "connected"
    }


# ---------------------------------------------------------
# Sensor API
# ---------------------------------------------------------

@app.post("/sensor-batches")
def receive(batch: Batch):
    inserted = duplicates = 0

    with database() as connection, connection.cursor(
        dictionary=True
    ) as cursor, ExitStack() as locks:

        for boot_id in sorted({reading.boot_id for reading in batch.readings}):
            key = (batch.device_id, batch.session_id, boot_id)
            locks.enter_context(session_lock(connection, key))
            require_receiving(connection, key, batch.user_id)

        for reading in batch.readings:
            values = {
                "device_id": batch.device_id,
                "session_id": batch.session_id,
                "user_id": batch.user_id,
                "is_synthetic": batch.is_synthetic,
                **reading.model_dump(),
            }

            columns = list(values)

            try:
                cursor.execute(
                    "INSERT INTO sensor_readings ("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join(["%s"] * len(columns))
                    + ")",
                    tuple(values.values()),
                )

                inserted += 1

            except mysql.connector.IntegrityError as exc:
                if exc.errno != 1062:
                    raise

                cursor.execute(
                    "SELECT "
                    + ",".join(columns)
                    + " FROM sensor_readings "
                    "WHERE device_id=%s "
                    "AND session_id=%s "
                    "AND boot_id=%s "
                    "AND seq=%s",
                    (
                        batch.device_id,
                        batch.session_id,
                        reading.boot_id,
                        reading.seq
                    ),
                )

                old = cursor.fetchone()

                if old != values:
                    raise HTTPException(
                        409,
                        f"Same identifier has different values: "
                        f"boot_id={reading.boot_id}, seq={reading.seq}"
                    )

                duplicates += 1

        connection.commit()

    return {
        "inserted": inserted,
        "duplicates": duplicates
    }


# ---------------------------------------------------------
# Sensor reading API
# ---------------------------------------------------------

@app.get("/readings")
def readings(
    device_id: Identifier,
    session_id: Identifier,
    boot_id: Identifier,
    after_seq: int = Query(-1, ge=-1),
    limit: int = Query(500, ge=1, le=500),
):
    with database() as connection, connection.cursor(
        dictionary=True
    ) as cursor:

        cursor.execute(
            "SELECT "
            "device_id,session_id,user_id,is_synthetic,"
            "schema_version,boot_id,seq,timestamp_ms,"
            "optical,gyro_x,gyro_y,gyro_z "
            "FROM sensor_readings "
            "WHERE device_id=%s "
            "AND session_id=%s "
            "AND boot_id=%s "
            "AND seq>%s "
            "ORDER BY seq "
            "LIMIT %s",
            (
                device_id,
                session_id,
                boot_id,
                after_seq,
                limit
            ),
        )

        items = cursor.fetchall()

    for item in items:
        item["is_synthetic"] = bool(
            item["is_synthetic"]
        )

    return {
        "items": items
    }


# ---------------------------------------------------------
# Image AI API
# ---------------------------------------------------------

def receive_real_capture(payload, image_bytes=None):
    try:
        metadata, rows = normalize_wire(payload)
        result = store_real_capture(database, metadata, rows, image_bytes, Reading)
    except ValueError as exc:
        log.warning("Real capture validation failed: %s",
                    validation_fields(exc.errors()) if isinstance(exc, ValidationError) else str(exc))
        raise HTTPException(422, "invalid_real_capture: check fields, timestamps and image") from exc
    if image_bytes is not None:
        # DB is authoritative. Local copy is a recoverable convenience cache.
        try:
            directory = Path(__file__).resolve().parent / "received_images"
            directory.mkdir(parents=True, exist_ok=True)
            with Image.open(io.BytesIO(image_bytes)) as original:
                extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[original.format]
            (directory / (result["image_sha256"] + extension)).write_bytes(image_bytes)
            result["local_image_saved"] = True
        except OSError:
            log.exception("Local image copy failed; original is retained in MySQL")
            result["local_image_saved"] = False

        def infer(image):
            model, device = get_ai_model()
            return {"device": str(device), "results": predict(image, model, device)}

        image_result = analyze_stored_image(database, metadata["device_id"],
            metadata["session_id"], metadata["boot_id"], metadata["user_id"], infer)
        result.update(image_status=image_result["image_status"], image_result=image_result["image_result"])
    return result


@app.post("/gyro", tags=["Real captures"])
def receive_gyro(payload: GyroCapture):
    return receive_real_capture(payload)


async def receive_optical_json(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8 * 1024 * 1024:
            raise HTTPException(413, "metadata_too_large")
    try:
        payload = OpticalCapture.model_validate_json(bytes(body))
    except ValidationError as exc:
        errors = validation_fields(exc.errors())
        log.warning("Optical JSON validation failed fields=%s", errors)
        raise HTTPException(422, {"code": "invalid_optical_metadata", "errors": errors}) from exc
    return await run_in_threadpool(receive_real_capture, payload)


_optical_schema = OpticalCapture.model_json_schema()
_optical_schema["properties"]["optical"]["items"] = _optical_schema.pop("$defs")["OpticalSample"]
OPTICAL_REQUEST_DOC = {"requestBody": {"content": {"application/json": {
    "schema": _optical_schema
}}}}


@app.post("/ai/analyze", openapi_extra=OPTICAL_REQUEST_DOC)
async def analyze_image(
    request: Request,
    file: UploadFile | None = File(None),
    metadata: str | None = Form(None),
):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        return await receive_optical_json(request)
    if content_type != "multipart/form-data":
        raise HTTPException(415, "Use application/json for optical samples or multipart/form-data for images")
    if file is None:
        raise HTTPException(422, [{"loc": ["body", "file"], "type": "missing", "msg": "Field required for multipart image upload"}])
    if metadata is not None:
        if len(metadata.encode("utf-8")) > 8 * 1024 * 1024:
            raise HTTPException(413, "metadata_too_large")
        try:
            payload = OpticalCapture.model_validate_json(metadata)
        except ValidationError as exc:
            errors = validation_fields(exc.errors())
            log.warning("Optical metadata validation failed fields=%s", errors)
            raise HTTPException(422, {"code": "invalid_optical_metadata", "errors": errors}) from exc
        image_bytes = await file.read(10 * 1024 * 1024 + 1)
        if len(image_bytes) > 10 * 1024 * 1024:
            raise HTTPException(413, "image_too_large")
        capture = await run_in_threadpool(receive_real_capture, payload, image_bytes)
        return {"filename": file.filename, **(capture.get("image_result") or {}), "capture": capture}

    if not file.content_type or not file.content_type.startswith(
        "image/"
    ):
        raise HTTPException(
            400,
            "이미지 파일만 업로드할 수 있습니다."
        )

    try:
        image_bytes = await file.read()

        if not image_bytes:
            raise HTTPException(
                400,
                "빈 이미지 파일입니다."
            )

        with Image.open(io.BytesIO(image_bytes)) as original:
            image_format = original.format
            image = original.convert("RGB")

    except UnidentifiedImageError:
        raise HTTPException(
            400,
            "이미지를 읽을 수 없습니다."
        )

    except HTTPException:
        raise

    except Exception:
        log.exception("Failed to read uploaded image")

        raise HTTPException(
            400,
            "이미지 처리에 실패했습니다."
        )

    try:
        save_dir = Path(__file__).resolve().parent / "received_images"
        save_dir.mkdir(parents=True, exist_ok=True)
        extension = {
            "JPEG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp",
        }.get(image_format, ".img")
        save_path = save_dir / f"{uuid4().hex}{extension}"
        save_path.write_bytes(image_bytes)
    except OSError:
        log.exception("Failed to save uploaded image")
        raise HTTPException(500, "이미지 저장에 실패했습니다.")

    log.info("Received image saved: %s", save_path)

    model, device = get_ai_model()

    try:
        results = predict(
            image,
            model,
            device
        )

    except Exception:
        log.exception("AI inference failed")

        raise HTTPException(
            500,
            "AI 이미지 분석에 실패했습니다."
        )

    # -----------------------------------------------------
    # AI 분석 결과 MySQL 저장
    # -----------------------------------------------------

    try:
        image_result_id = save_image_analysis(
            user_id="user-001",
            session_id="real-session-001",
            results=results,
        )

    except Exception:
        log.exception("Failed to save image analysis result")

        raise HTTPException(
            500,
            "AI 분석 결과 저장에 실패했습니다."
        )

    print("=========HERE=========")
    print("Image_result_id = ", image_result_id)

    return {
        "filename": file.filename,
        "device": str(device),
        "results": results,
        "image_result_id": image_result_id,
    }

@app.post("/optical", tags=["Real captures"], openapi_extra=OPTICAL_REQUEST_DOC)
async def receive_optical(
    request: Request,
    file: UploadFile | None = File(None),
    metadata: str | None = Form(None),
):
    """OPT101 JSON without a camera, or a legacy optical + image capture."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "multipart/form-data" and metadata is None:
        raise HTTPException(422, [{"loc": ["body", "metadata"], "type": "missing", "msg": "Field required"}])
    return await analyze_image(request=request, file=file, metadata=metadata)


# ---------------------------------------------------------
# Image history API
# ---------------------------------------------------------

@app.get("/ai/history")
def get_image_history(
    limit: int = Query(30, ge=1, le=100),
):
    user_id = "user-001"

    with database() as connection, connection.cursor(
        dictionary=True
    ) as cursor:

        cursor.execute(
            """
            SELECT
                id,
                user_id,
                session_id,
                measured_at,

                micro_scale_grade,
                micro_scale_confidence,

                excess_sebum_grade,
                excess_sebum_confidence,

                perifollicular_erythema_grade,
                perifollicular_erythema_confidence,

                follicular_erythema_pustule_grade,
                follicular_erythema_pustule_confidence,

                dandruff_grade,
                dandruff_confidence,

                hair_loss_grade,
                hair_loss_confidence

            FROM image_analysis_results

            WHERE user_id = %s

            ORDER BY measured_at ASC

            LIMIT %s
            """,
            (user_id, limit),
        )

        rows = cursor.fetchall()

    return {
        "user_id": user_id,
        "count": len(rows),
        "items": rows,
    }
