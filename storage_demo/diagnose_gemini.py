"""Run one sensor feedback request locally and show redacted error diagnostics."""

import argparse
import json
import os
import re

import requests

from sensor_analysis_api import get_session
from sensor_feedback import DEFAULT_MODEL, build_evidence, request_payload, provider_error_details


def redact_message(message, key):
    if not isinstance(message, str):
        return None
    if key:
        message = message.replace(key, "[REDACTED]")
    message = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", message)
    message = re.sub(r"https?://\S+", "[URL REDACTED]", message)
    message = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL REDACTED]", message)
    message = re.sub(r"projects/[^\s/\"']+", "projects/[REDACTED]", message)
    return " ".join(message.split())[:1500]


def response_diagnostics(response, key):
    result = {"http_status": response.status_code}
    try:
        data = response.json()
    except ValueError:
        # HTML/proxy responses can contain identifying details. Do not print them.
        result["body_format"] = "non_json"
        result["looks_like_html"] = response.text.lstrip().lower().startswith(("<!doctype html", "<html"))
        return result
    result["body_format"] = "json"
    if response.status_code == 200:
        result["note"] = "HTTP success only; verify feedback.source in Swagger next."
        return result
    result.update(provider_error_details(response))
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        result["error_message_redacted"] = redact_message(error.get("message"), key)
    elif isinstance(error, str):
        result["error_message_redacted"] = redact_message(error, key)
    else:
        result["note"] = "No standard error object; raw body omitted."
    return result


def main(check_model=False):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip()
    if not key:
        print("missing_api_key: Set GEMINI_API_KEY in this terminal first.")
        return
    if not re.fullmatch(r"gemini-[A-Za-z0-9.-]{1,80}", model):
        print("invalid_model_configuration")
        return
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        options = {"headers": {"x-goog-api-key": key, "Content-Type": "application/json"},
                   "timeout": (5, 25), "allow_redirects": False}
        if check_model:
            response = requests.get(url, **options)
        else:
            session = get_session("multi-session-102").session.model_dump()
            payload = request_payload(build_evidence(session))
            response = requests.post(url + ":generateContent", json=payload, **options)
        with response:
            result = response_diagnostics(response, key)
            result["operation"] = "get_model" if check_model else "generate_content"
            if check_model and response.status_code == 200 and result["body_format"] == "json":
                data = response.json()
                result["model_matches"] = isinstance(data, dict) and data.get("name") == f"models/{model}"
                methods = data.get("supportedGenerationMethods") if isinstance(data, dict) else None
                result["supports_generate_content"] = isinstance(methods, list) and "generateContent" in methods
                result["note"] = "Model lookup only; content generation has not been tested."
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except requests.RequestException:
        print("provider_unavailable: Connection or timeout error; exception text omitted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-model", action="store_true", help="Only GET model metadata; no sensor data or generation request")
    main(check_model=parser.parse_args().check_model)
