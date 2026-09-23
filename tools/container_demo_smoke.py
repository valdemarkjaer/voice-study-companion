#!/usr/bin/env python3
"""Exercise the complete credential-free demo lifecycle inside a container."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from voice_study_companion.config import RuntimeMode, load_runtime_config
from voice_study_companion.server import make_server


REPORT_SCHEMA = "voice-study-companion.container-smoke-report"
LIVE_CONFIGURATION = (
    "VSC_LIVE_MODEL_ENDPOINT",
    "VSC_LIVE_MODEL_TOKEN",
    "VSC_LIVE_CARD_ENDPOINT",
    "VSC_LIVE_CARD_TOKEN",
)


class SmokeError(RuntimeError):
    """A public-safe smoke failure that never embeds response content."""


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, str, bytes]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json"} if body is not None else {}
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=3) as response:
        return response.status, response.headers.get_content_type(), response.read()


def _post(
    base_url: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    status, media_type, body = _request(
        base_url,
        path,
        method="POST",
        payload=payload,
    )
    if status not in {HTTPStatus.OK, HTTPStatus.CREATED}:
        raise SmokeError("unexpected_http_status")
    if media_type != "application/json":
        raise SmokeError("unexpected_http_media_type")
    document = json.loads(body)
    if not isinstance(document, dict):
        raise SmokeError("unexpected_http_payload")
    return document


def run_smoke() -> dict[str, object]:
    if any(os.environ.get(name) for name in LIVE_CONFIGURATION):
        raise SmokeError("live_configuration_must_be_absent")
    config = load_runtime_config(os.environ)
    if config.mode is not RuntimeMode.DEMO or config.uses_external_services:
        raise SmokeError("credential_free_demo_mode_required")

    server = make_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status, media_type, homepage = _request(base_url, "/")
        if status != HTTPStatus.OK or media_type != "text/html":
            raise SmokeError("homepage_unavailable")
        if b"Voice Study Companion" not in homepage:
            raise SmokeError("public_brand_missing")

        opened = _post(base_url, "/api/sessions", {"profile": "full"})
        session_id = opened.get("session_id")
        if not isinstance(session_id, str) or not session_id.startswith("demo-session-"):
            raise SmokeError("session_not_opened")
        if "official_answer" in json.dumps(opened):
            raise SmokeError("answer_disclosed_before_request")

        transcript = _post(base_url, f"/api/sessions/{session_id}/transcription")
        answer = transcript.get("transcript")
        if not isinstance(answer, str) or not answer:
            raise SmokeError("transcription_missing")
        evaluated = _post(
            base_url,
            f"/api/sessions/{session_id}/evaluation",
            {"answer": answer},
        )
        evaluation = evaluated.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("verdict") != "correct":
            raise SmokeError("evaluation_failed")
        if "official_answer" in json.dumps(evaluated):
            raise SmokeError("answer_disclosed_during_evaluation")

        official = _post(base_url, f"/api/sessions/{session_id}/official-answer")
        speech_url = official.get("speech_url")
        if not isinstance(official.get("official_answer"), str) or not isinstance(
            speech_url, str
        ):
            raise SmokeError("official_answer_missing")
        audio_status, audio_type, audio = _request(base_url, speech_url)
        if (
            audio_status != HTTPStatus.OK
            or audio_type != "audio/wav"
            or not audio.startswith(b"RIFF")
        ):
            raise SmokeError("official_answer_audio_invalid")

        advanced = _post(base_url, f"/api/sessions/{session_id}/next")
        if advanced.get("position") != 2:
            raise SmokeError("card_did_not_advance")
        closed = _post(base_url, f"/api/sessions/{session_id}/close")
        if closed.get("status") != "confirmed":
            raise SmokeError("session_did_not_close")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    if thread.is_alive():
        raise SmokeError("server_did_not_stop")
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": 1,
        "status": "PASS",
        "mode": "demo",
        "external_services_enabled": False,
        "lifecycle_steps": 7,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline container demo smoke")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_smoke()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("container demo smoke: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, SmokeError) as exc:
        raise SystemExit(f"container demo smoke failed: {exc}") from exc
