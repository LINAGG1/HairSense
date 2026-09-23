"""Send the mock file through HTTP, then compare all stored records via HTTP."""
import json
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
DATA_DIR = Path(__file__).resolve().parent.parent / "mock_data"


def checked(response):
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def main():
    metadata = json.loads((DATA_DIR / "metadata.json").read_text(encoding="utf-8"))
    expected = [
        json.loads(line)
        for line in (DATA_DIR / "sensor_mock.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not expected or len({row['seq'] for row in expected}) != len(expected):
        raise RuntimeError("Source must be nonempty and contain unique sequence numbers")
    if any(row["boot_id"] != metadata["boot_id"] for row in expected):
        raise RuntimeError("Source boot_id must match metadata boot_id")
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
