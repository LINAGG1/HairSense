"""Local-only storage exercise; run from storage_demo with uvicorn app:app."""
import json
import logging
import io
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from ai.hair_model import load_model, predict


app = FastAPI(title="HairSense local storage exercise")
log = logging.getLogger(__name__)

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

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
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
    ) as cursor:

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

@app.post("/ai/analyze")
async def analyze_image(
    file: UploadFile = File(...)
):
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

        image = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

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

    return {
        "filename": file.filename,
        "device": str(device),
        "results": results,
    }
