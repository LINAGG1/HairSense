"""Local-only storage exercise; run from storage_demo with uvicorn app:app."""
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import mysql.connector
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

app = FastAPI(title="HairSense local storage exercise")
log = logging.getLogger(__name__)
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
UInt64 = Annotated[int, Field(strict=True, ge=0, le=18446744073709551615)]


class Reading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["0.1"]
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
    is_synthetic: Literal[True]  # This endpoint is for mock data only.
    readings: list[Reading] = Field(min_length=1, max_length=500)


@contextmanager
def database():
    connection = None
    try:
        config = json.loads(
            Path(__file__).with_name("db_config.json").read_text(encoding="utf-8-sig")
        )
        connection = mysql.connector.connect(**config, connection_timeout=5)
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        yield connection
    except (OSError, ValueError, mysql.connector.Error):
        log.exception("Database connection or operation failed")
        raise HTTPException(503, "DB unavailable: check configuration, service and setup.sql")
    finally:
        if connection is not None:
            connection.close()  # Rolls back any uncommitted batch on failure.


@app.get("/health")
def health():
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sensor_readings LIMIT 1")
        cursor.fetchall()
    return {"status": "ok", "database": "connected"}


@app.post("/sensor-batches")
def receive(batch: Batch):
    inserted = duplicates = 0
    with database() as connection, connection.cursor(dictionary=True) as cursor:
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
                    "INSERT INTO sensor_readings (" + ",".join(columns) + ") VALUES ("
                    + ",".join(["%s"] * len(columns)) + ")",
                    tuple(values.values()),
                )
                inserted += 1
            except mysql.connector.IntegrityError as exc:
                if exc.errno != 1062:
                    raise
                cursor.execute(
                    "SELECT " + ",".join(columns)
                    + " FROM sensor_readings WHERE device_id=%s AND session_id=%s AND seq=%s",
                    (batch.device_id, batch.session_id, reading.seq),
                )
                old = cursor.fetchone()
                if old != values:
                    raise HTTPException(409, f"Same identifier has different values: seq={reading.seq}")
                duplicates += 1
        connection.commit()
    return {"inserted": inserted, "duplicates": duplicates}


@app.get("/readings")
def readings(
    device_id: Identifier,
    session_id: Identifier,
    after_seq: int = Query(-1, ge=-1),
    limit: int = Query(500, ge=1, le=500),
):
    with database() as connection, connection.cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT device_id,session_id,user_id,is_synthetic,schema_version,seq,"
            "timestamp_ms,optical,gyro_x,gyro_y,gyro_z FROM sensor_readings "
            "WHERE device_id=%s AND session_id=%s AND seq>%s ORDER BY seq LIMIT %s",
            (device_id, session_id, after_seq, limit),
        )
        items = cursor.fetchall()
    for item in items:
        item["is_synthetic"] = bool(item["is_synthetic"])
    return {"items": items}
