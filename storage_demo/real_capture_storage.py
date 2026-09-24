"""ESP32 real optical/image and gyro ingestion with atomic DB persistence.

No synthetic model, synthetic preprocessing, or Gemini call is made here.
"""
import hashlib
import io
import os
import numpy as np
from typing import Literal

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from sensor_pipeline import json_text
from sensor_pipeline_api import Identifier, WHERE, decode, register_session, session_lock


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class OpticalSample(WireModel):
    timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    value: FiniteFloat | None


class GyroSample(WireModel):
    timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    gyro_x: FiniteFloat | None
    gyro_y: FiniteFloat | None
    gyro_z: FiniteFloat | None


class OpticalCapture(WireModel):
    schema_version: Literal[1]
    sample_type: Literal["camera_optical"]
    boot_id: Identifier
    seq: int = Field(ge=0, le=18446744073709551615)
    timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    optical: list[OpticalSample] = Field(min_length=1, max_length=30000)
    gyro_x: None
    gyro_y: None
    gyro_z: None


class GyroCapture(WireModel):
    schema_version: Literal[1]
    sample_type: Literal["gyro"]
    boot_id: Identifier
    seq: int = Field(ge=0, le=18446744073709551615)
    timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    optical: None
    gyro: list[GyroSample] = Field(min_length=1, max_length=30000)


def normalize_wire(payload):
    """Single-device installation binding; packet seq is NOT a sample seq."""
    device = os.environ.get("HAIRSENSE_REAL_DEVICE_ID")
    user = os.environ.get("HAIRSENSE_REAL_USER_ID")
    if not device or not user:
        raise HTTPException(503, "real_device_and_user_binding_required")
    wire = payload.model_dump()
    samples = wire["optical" if wire["sample_type"] == "camera_optical" else "gyro"]
    timestamps = [s["timestamp_ms"] for s in samples]
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Sample timestamps must increase within one boot")
    rows = []
    for ordinal, sample in enumerate(samples, 1):
        row = dict(schema_version=1, boot_id=wire["boot_id"], seq=ordinal,
                   timestamp_ms=sample["timestamp_ms"], optical=None,
                   gyro_x=None, gyro_y=None, gyro_z=None)
        if wire["sample_type"] == "camera_optical":
            row["optical"] = sample["value"]
        else:
            row.update(sample)
        rows.append(row)
    metadata = CaptureMetadata(
        device_id=device, user_id=user,
        session_id=f"real-{'optical' if wire['sample_type'] == 'camera_optical' else 'gyro'}-{wire['seq']}",
        boot_id=wire["boot_id"], is_synthetic=False, schema_version=1,
        optical_unit="V", sample_rate_hz=50,
        start_timestamp_ms=timestamps[0], end_timestamp_ms=timestamps[-1] + 20,
        interval_source="observed_first_to_last_plus_nominal_20ms_not_button_duration",
        sample_type=wire["sample_type"], wire_payload=wire).model_dump()
    return metadata, rows


def describe_real_rows(metadata, rows):
    """Descriptive windows only. Never apply synthetic thresholds to real data."""
    channels = ["optical"] if metadata["sample_type"] == "camera_optical" else ["gyro_x", "gyro_y", "gyro_z"]
    times = [r["timestamp_ms"] for r in rows]
    intervals = [b-a for a, b in zip(times, times[1:])]
    groups = {}
    for row in rows:
        groups.setdefault((row["timestamp_ms"]-times[0])//2000, []).append(row)
    windows = []
    for number, group in groups.items():
        features = {}
        for channel in channels:
            values = [r[channel] for r in group if r[channel] is not None]
            features[channel] = {"valid_count": len(values), "null_count": len(group)-len(values),
                                 "mean": float(np.mean(values)) if values else None,
                                 "std": float(np.std(values, ddof=0)) if values else None}
        # A complete regular window has every expected 20 ms slot and no missing measured channel.
        expected = [times[0]+number*2000+i*20 for i in range(100)]
        complete = [r["timestamp_ms"] for r in group] == expected
        windows.append({"window_id": number, "start_timestamp_ms": times[0]+number*2000,
                        "sample_count": len(group), "complete_regular_window": complete,
                        "all_measured_values_present": all(r[c] is not None for r in group for c in channels),
                        "features": features})
    return {"mode": "real_sensor_descriptive", "pipeline_version": "real_capture_1",
            "is_synthetic": False, "status": "awaiting_real_baseline",
            "measured_channels": channels, "sample_count": len(rows),
            "observed_span_ms": times[-1]-times[0], "window_ms": 2000,
            "non_20ms_intervals": sum(dt != 20 for dt in intervals),
            "min_interval_ms": min(intervals) if intervals else None,
            "max_interval_ms": max(intervals) if intervals else None,
            "windows": windows, "anomaly_score": None, "feedback": None,
            "limitations": ["No real baseline or calibrated anomaly/rapid-change thresholds yet",
                            "Partial or irregular windows are descriptive only; no imputation",
                            "Sample seq is a server ordinal, not a device packet-loss indicator",
                            "Button start/stop times and missing leading/trailing samples are unknown"]}


class CaptureMetadata(BaseModel):
    """Internal normalized contract; wire_payload retains the ESP32 envelope."""
    model_config = ConfigDict(strict=True, extra="forbid")
    device_id: Identifier
    session_id: Identifier
    boot_id: Identifier
    user_id: Identifier
    is_synthetic: Literal[False]
    schema_version: Literal[1]
    optical_unit: Literal["V"]
    gyro_unit: Literal["deg/s"] = "deg/s"
    sample_rate_hz: Literal[50]
    start_timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    end_timestamp_ms: int = Field(ge=0, le=18446744073709551615)
    sample_type: Literal["camera_optical", "gyro"] = "camera_optical"
    wire_payload: dict | None = None
    interval_source: str = "provided"

    @model_validator(mode="after")
    def valid_bounds(self):
        if not 0 < self.end_timestamp_ms - self.start_timestamp_ms <= 600000:
            raise ValueError("Expected capture duration >0 and <=600 seconds")
        return self


def validate_capture(meta, readings, image_bytes, reading_type):
    """Validate the entire bundle before any DB mutation."""
    metadata = CaptureMetadata.model_validate(meta).model_dump()
    if not 1 <= len(readings) <= 30000:
        raise ValueError("Expected 1..30000 readings")
    rows = [reading_type.model_validate(row).model_dump() for row in readings]
    rows.sort(key=lambda r: r["seq"])
    for row in rows:
        if row["boot_id"] != metadata["boot_id"]:
            raise ValueError("Mixed boot IDs")
        if not metadata["start_timestamp_ms"] <= row["timestamp_ms"] < metadata["end_timestamp_ms"]:
            raise ValueError("Sample outside capture interval")
    if any(b["seq"] <= a["seq"] or b["timestamp_ms"] <= a["timestamp_ms"] for a, b in zip(rows, rows[1:])):
        raise ValueError("Duplicate sequence or non-monotonic timestamps")
    if metadata["sample_type"] == "gyro":
        if image_bytes is not None:
            raise ValueError("Gyro capture must not contain an image")
        payload_hash = hashlib.sha256(json_text({"metadata": metadata, "readings": rows}).encode("utf-8")).hexdigest()
        return metadata, rows, None, None, payload_hash
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= 10 * 1024 * 1024:
        raise ValueError("Expected one image <=10 MiB")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 20000000:
                raise ValueError("Unsupported image format or dimensions")
            mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image.format]
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid image") from exc
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload_hash = hashlib.sha256(json_text({"metadata": metadata, "readings": rows,
                                           "image_sha256": image_hash}).encode("utf-8")).hexdigest()
    return metadata, rows, mime, image_hash, payload_hash


def store_real_capture(database, meta, readings, image_bytes, reading_type):
    """Atomically persist image BLOB + real sensor rows. No inference side effects.

Caller supplies the existing Reading class. Unmeasured channels remain null.
"""
    metadata, rows, mime, image_hash, payload_hash = validate_capture(meta, readings, image_bytes, reading_type)
    key = tuple(metadata[k] for k in ("device_id", "session_id", "boot_id"))
    result = {"mode": "real_capture", "status": "stored", "is_synthetic": False,
              "session_id": metadata["session_id"], "boot_id": metadata["boot_id"],
              "reading_count": len(rows), "image_sha256": image_hash,
              "device_id": metadata["device_id"], "user_id": metadata["user_id"],
              "image_status": "pending" if image_bytes else "not_present",
              "sensor_analysis_status": "awaiting_real_baseline"}
    descriptive = describe_real_rows(metadata, rows)
    with database() as connection, session_lock(connection, key):
        try:
            with connection.cursor(dictionary=True) as cursor:
                session = register_session(cursor, key, metadata["user_id"], is_synthetic=False)
                stored_metadata = decode(session.get("metadata_json")) or {}
                old = None
                if image_bytes is not None:
                    cursor.execute("SELECT payload_sha256,image_status,image_result_json FROM sensor_capture_images WHERE " + WHERE, key)
                    old = cursor.fetchone()
                elif stored_metadata.get("payload_sha256"):
                    old = dict(payload_sha256=stored_metadata["payload_sha256"], image_status="not_present", image_result_json=None)
                if old:
                    if old["payload_sha256"] != payload_hash:
                        raise HTTPException(409, "capture_payload_conflict")
                    connection.rollback()
                    return {**result, "cached": True, "image_status": old["image_status"],
                            "image_result": decode(old["image_result_json"])}
                if session["status"] != "receiving":
                    raise HTTPException(409, "session_already_sealed")
                cursor.execute("SELECT COUNT(*) AS row_count FROM sensor_readings WHERE " + WHERE, key)
                if cursor.fetchone()["row_count"]:
                    raise HTTPException(409, "capture_requires_new_session")
                columns = ("device_id", "session_id", "user_id", "is_synthetic", "schema_version",
                           "boot_id", "seq", "timestamp_ms", "optical", "gyro_x", "gyro_y", "gyro_z")
                for row in rows:
                    values = {**metadata, **row}
                    cursor.execute("INSERT INTO sensor_readings (" + ",".join(columns) + ") VALUES (" +
                                   ",".join(["%s"] * len(columns)) + ")", tuple(values[k] for k in columns))
                if image_bytes is not None:
                    cursor.execute("INSERT INTO sensor_capture_images "
                               "(device_id,session_id,boot_id,payload_sha256,image_sha256,image_mime,image_bytes) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s)", (*key, payload_hash, image_hash, mime, image_bytes))
                cursor.execute("INSERT INTO sensor_analysis_runs "
                               "(device_id,session_id,boot_id,pipeline_version,status,result_json) "
                               "VALUES (%s,%s,%s,%s,%s,%s)",
                               (*key, "real_capture_1", "awaiting_real_baseline", json_text(descriptive)))
                cursor.execute("UPDATE sensor_sessions SET status='sealed',metadata_json=%s WHERE " + WHERE,
                               (json_text({**metadata, "payload_sha256": payload_hash}), *key))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {**result, "cached": False, "image_result": None}


def analyze_stored_image(database, device_id, session_id, boot_id, user_id, infer_image):
    """Use the existing image predictor callback, preserving committed raw input.

    A processing state left by a terminated process is not automatically replayed.
    No optical values are supplied to the image predictor.
    """
    key = (device_id, session_id, boot_id)
    with database() as connection, session_lock(connection, key):
        with connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT user_id FROM sensor_sessions WHERE " + WHERE, key)
            owner = cursor.fetchone()
            if not owner or owner["user_id"] != user_id:
                raise HTTPException(404, "capture_not_found")
            cursor.execute("SELECT image_bytes,image_status,image_result_json FROM sensor_capture_images WHERE " + WHERE, key)
            record = cursor.fetchone()
            if not record:
                raise HTTPException(404, "capture_not_found")
            if record["image_status"] in ("completed", "failed"):
                return {"image_status": record["image_status"], "image_result": decode(record["image_result_json"]), "cached": True}
            if record["image_status"] != "pending":
                raise HTTPException(409, "image_processing_recovery_required")
            cursor.execute("UPDATE sensor_capture_images SET image_status='processing' WHERE " + WHERE, key)
        connection.commit()
        try:
            with Image.open(io.BytesIO(record["image_bytes"])) as original:
                image = original.convert("RGB")
            try:
                result = infer_image(image)
            finally:
                image.close()
            serialized = json_text(result)
            status = "completed"
        except Exception:
            status = "failed"
            result = {"error_code": "image_inference_failed", "raw_capture_preserved": True}
            serialized = json_text(result)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sensor_capture_images SET image_status=%s,image_result_json=%s WHERE " + WHERE,
                           (status, serialized, *key))
        connection.commit()
        return {"image_status": status, "image_result": result, "cached": False}
