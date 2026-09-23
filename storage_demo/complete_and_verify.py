"""Complete the uploaded test session, verify cached replay and DB result lookup."""
import json
import requests

from send_and_verify import BASE_URL, checked, load_source


def main():
    meta, _ = load_source("multi-session-102")
    body = {k: meta[k] for k in (
        "user_id", "device_id", "boot_id", "is_synthetic", "schema_version",
        "start_timestamp_ms", "duration_seconds", "sample_rate_hz", "interval_ms",
        "optical_unit", "gyro_unit",
    )}
    url = BASE_URL + "/sensor-sessions/" + meta["session_id"]
    with requests.Session() as client:
        first = checked(client.post(url + "/complete", json=body, timeout=120))
        if first["status"] != "completed":
            print(json.dumps({"status": first["status"], "error_code": first.get("error_code"),
                              "quality": first.get("quality")}, ensure_ascii=False, indent=2))
            raise RuntimeError("Analysis/feedback did not complete; inspect the saved status")
        second = checked(client.post(url + "/complete", json=body, timeout=30))
        if second.get("cached") is not True:
            raise RuntimeError("Repeated completion must return a cached result")
        saved = checked(client.get(url + "/analysis", params={k: meta[k] for k in ("user_id", "device_id", "boot_id")}, timeout=30))
    expected = {k: v for k, v in first.items() if k != "cached"}
    if expected != {k: v for k, v in second.items() if k != "cached"} or expected != saved:
        raise RuntimeError("Stored result and cached completion differ")
    print("PASS: DB analysis, cached completion and result lookup match")
    print(f"first_request_cached: {first['cached']}")
    for sensor, summary in saved["summary"]["sensors"].items():
        print(f"{sensor}: flagged={summary['flagged_windows']}/{summary['valid_windows']}")
    print(f"feedback_source: {saved['feedback']['source']}")
    print(f"fallback_reason: {saved['feedback']['fallback_reason']}")
    print(json.dumps(saved["feedback"]["messages"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
