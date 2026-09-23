"""Send the mock file through HTTP, then compare all stored records via HTTP."""
import argparse
import json
import math
import re
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
DATA_DIR = Path(__file__).resolve().parent.parent / "mock_data"
HOLDOUT_DIR = DATA_DIR.parent / "mock_sessions" / "holdout_002"


def checked(response):
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def load_source(session_id=None):
    folder = DATA_DIR
    if session_id is not None:
        manifest = json.loads((HOLDOUT_DIR / "manifest.json").read_text(encoding="utf-8"))
        entries = [entry for entry in manifest["sessions"] if entry["session_id"] == session_id]
        if manifest.get("is_synthetic") is not True or len(entries) != 1:
            raise ValueError("Expected one synthetic holdout session in manifest")
        folder = (HOLDOUT_DIR / entries[0]["directory"]).resolve()
        if not folder.is_relative_to(HOLDOUT_DIR.resolve()):
            raise ValueError("Session directory outside holdout dataset")
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("is_synthetic") is not True:
        raise ValueError("Only synthetic sessions may be uploaded by this tool")
    for key in ("device_id", "session_id", "user_id", "boot_id"):
        if not isinstance(metadata.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", metadata[key]):
            raise ValueError(f"Invalid metadata identifier: {key}")
    if session_id is not None and any(metadata[key] != entries[0][key] for key in ("session_id", "boot_id")):
        raise ValueError("Manifest and metadata identities differ")
    expected = [
        json.loads(line)
        for line in (folder / "sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fields = {"schema_version", "boot_id", "seq", "timestamp_ms", "optical", "gyro_x", "gyro_y", "gyro_z"}
    for row in expected:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Unexpected sensor message fields")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise ValueError("Expected integer schema_version=1")
        for key in ("seq", "timestamp_ms"):
            if type(row[key]) is not int or not 0 <= row[key] <= 18446744073709551615:
                raise ValueError(f"Invalid sensor integer: {key}")
        for key in ("optical", "gyro_x", "gyro_y", "gyro_z"):
            if row[key] is not None and (type(row[key]) not in (int, float) or not math.isfinite(row[key])):
                raise ValueError(f"Invalid sensor value: {key}")
    if not expected or len({row['seq'] for row in expected}) != len(expected):
        raise RuntimeError("Source must be nonempty and contain unique sequence numbers")
    if any(row["boot_id"] != metadata["boot_id"] for row in expected):
        raise RuntimeError("Source boot_id must match metadata boot_id")
    if session_id is not None:
        from preprocess import process
        _, _, report = process(expected, metadata)
        if metadata["duration_seconds"] != 60 or len(expected) != 3000 or report["usable_windows"] != 30 or report["excluded_windows"]:
            raise ValueError("Holdout upload requires a complete 60-second session")
    return metadata, expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", help="Select a session from mock_sessions/holdout_002; default: original mock_data")
    parser.add_argument("--dry-run", action="store_true", help="Validate source files without HTTP or database writes")
    args = parser.parse_args(argv)
    metadata, expected = load_source(args.session_id)
    print(f"Session={metadata['session_id']}, boot={metadata['boot_id']}, samples={len(expected)}")
    if args.dry_run:
        print("PASS: source validation only; no HTTP requests or database writes")
        return
    context = {
        key: metadata[key]
        for key in ("device_id", "session_id", "user_id", "is_synthetic")
    }
    inserted = duplicates = 0
    with requests.Session() as client:
        checked(client.get(BASE_URL + "/health", timeout=10))
        for start in range(0, len(expected), 250):
            result = checked(client.post(
                BASE_URL + "/sensor-batches",
                json={**context, "readings": expected[start:start + 250]},
                timeout=30,
            ))
            inserted += result["inserted"]
            duplicates += result["duplicates"]
            print(f"Sent {min(start + 250, len(expected))}/{len(expected)}")

        actual = []
        after_seq = -1
        while True:
            result = checked(client.get(
                BASE_URL + "/readings",
                params={"device_id": context["device_id"],
                        "session_id": context["session_id"],
                        "boot_id": metadata["boot_id"], "after_seq": after_seq},
                timeout=30,
            ))
            if not result["items"]:
                break
            if any(item["seq"] <= after_seq for item in result["items"]) or any(
                b["seq"] <= a["seq"] for a, b in zip(result["items"], result["items"][1:])
            ):
                raise RuntimeError("API pagination did not advance in sequence order")
            actual.extend(result["items"])
            after_seq = result["items"][-1]["seq"]

    expected = sorted(({**context, **row} for row in expected), key=lambda row: row["seq"])
    if len(actual) != len(expected):
        raise RuntimeError(f"Count mismatch: source={len(expected)}, DB={len(actual)}")
    for source, stored in zip(expected, actual):
        if source != stored:
            raise RuntimeError(f"Value mismatch at seq={source['seq']}")
    print(f"Inserted={inserted}, duplicates={duplicates}")
    print(f"PASS: all {len(actual)} records match the source")
    print(f"Optical null count={sum(row['optical'] is None for row in actual)}")


if __name__ == "__main__":
    main()
