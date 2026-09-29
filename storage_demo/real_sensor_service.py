"""Transaction-scoped sensor baseline state and read-only dashboard snapshots."""
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from real_sensor_baseline import VERSION, advance, empty_state
from real_sensor_feedback import NOTICE, VERSION as FEEDBACK_VERSION, with_real_feedback
from sensor_pipeline import json_text
from sensor_pipeline_api import Identifier, decode

PROFILE_WHERE = "user_id=%s AND device_id=%s AND sensor=%s"


def record_capture(cursor, metadata, rows):
    """Called only for a NEW capture, inside its raw-input transaction.

The profile row lock serializes all captures for an owner/sensor, including
different session IDs. Rollback covers raw rows, result and learned history.
"""
    sensor = "gyro" if metadata["sample_type"] == "gyro" else "optical"
    owner = (metadata["user_id"], metadata["device_id"], sensor)
    cursor.execute("INSERT INTO real_sensor_profiles (user_id,device_id,sensor,state_json) "
                   "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE user_id=user_id",
                   (*owner, json_text(empty_state())))
    cursor.execute("SELECT state_json,next_role FROM real_sensor_profiles WHERE " + PROFILE_WHERE + " FOR UPDATE", owner)
    profile = cursor.fetchone()
    state, result = advance(decode(profile["state_json"]), rows, metadata, sensor, profile["next_role"])
    # Immutable evaluation is recorded before advancing the baseline.
    cursor.execute("INSERT INTO real_sensor_events (user_id,device_id,sensor,session_id,boot_id,result_json) "
                   "VALUES (%s,%s,%s,%s,%s,%s)",
                   (*owner, metadata["session_id"], metadata["boot_id"], json_text(result)))
    next_role = "measurement" if result["status"] == "calibration_completed" else profile["next_role"]
    cursor.execute("UPDATE real_sensor_profiles SET state_json=%s,next_role=%s WHERE " + PROFILE_WHERE,
                   (json_text(state), next_role, *owner))
    return result


def require_binding(user_id, device_id):
    expected = (os.getenv("HAIRSENSE_REAL_USER_ID"), os.getenv("HAIRSENSE_REAL_DEVICE_ID"))
    if not all(expected):
        raise HTTPException(503, "real_device_and_user_binding_required")
    if (user_id, device_id) != expected:
        raise HTTPException(404, "real_user_device_not_bound")


class NextCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: Identifier
    device_id: Identifier
    role: Literal["measurement", "calibration"]


def make_real_sensor_router(database):
    router = APIRouter(prefix="/real-sensors", tags=["Real sensor baseline"])

    @router.get("/latest")
    def latest(user_id: Identifier, device_id: Identifier):
        require_binding(user_id, device_id)
        # One SELECT ensures both sensors belong to the same DB snapshot.
        with database() as connection, connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT e.sensor,e.result_json,e.created_at FROM real_sensor_events e "
                "JOIN (SELECT sensor,MAX(id) AS id FROM real_sensor_events "
                "WHERE user_id=%s AND device_id=%s GROUP BY sensor) latest ON e.id=latest.id",
                (user_id, device_id))
            records = cursor.fetchall()
        sensors, captures = {}, {}
        for record in records:
            result = with_real_feedback(decode(record["result_json"]))
            sensor = record["sensor"]
            sensors[sensor] = {**result["summary"]["sensors"][sensor],
                               "recorded_at": str(record["created_at"])}
            captures[sensor] = result
        return {"mode": "real_online_analysis", "pipeline_version": VERSION,
                "status": "available" if sensors else "awaiting_first_capture",
                "summary": {"user_id": user_id, "device_id": device_id, "sensors": sensors},
                "captures": captures,
                "feedback": {"source": "rules", "is_synthetic": False,
                    "version": FEEDBACK_VERSION, "notice": NOTICE,
                    "messages": {sensor: result["feedback"]["messages"][sensor]
                                 for sensor, result in captures.items()}} if captures else None}

    @router.get("/baseline")
    def baseline(user_id: Identifier, device_id: Identifier):
        require_binding(user_id, device_id)
        with database() as connection, connection.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT sensor,state_json,next_role FROM real_sensor_profiles "
                           "WHERE user_id=%s AND device_id=%s", (user_id, device_id))
            return {"sensors": {row["sensor"]: {
                "baseline": decode(row["state_json"])["baseline"], "next_role": row["next_role"]
            } for row in cursor.fetchall()}}

    @router.post("/{sensor}/next-capture")
    def next_capture(sensor: Literal["optical", "gyro"], request: NextCapture):
        require_binding(request.user_id, request.device_id)
        owner = (request.user_id, request.device_id, sensor)
        with database() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT state_json FROM real_sensor_profiles WHERE " + PROFILE_WHERE + " FOR UPDATE", owner)
                row = cursor.fetchone()
                if not row or not decode(row["state_json"])["baseline"]:
                    raise HTTPException(409, "collect_initial_baseline_first")
                cursor.execute("UPDATE real_sensor_profiles SET next_role=%s WHERE " + PROFILE_WHERE,
                               (request.role, *owner))
            connection.commit()
        return {"sensor": sensor, "next_role": request.role}

    return router
