"""Dependency-free local HTTP server for the public demonstration."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import mimetypes
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from .config import RuntimeMode, load_runtime_config
from .contracts import EvaluationResult, PresentationProfile
from .demo.deck import build_synthetic_deck
from .demo.session import DemoSession, PublicCard, SessionView


REQUIRED_WEB_ASSETS = (
    "index.html",
    "styles.css",
    "app.js",
    "audio/audio-capture-worklet.js",
    "audio/audio-controller.js",
    "audio/pcm-capture.mjs",
    "audio/pcm-player-worklet.js",
    "audio/pcm-ring-buffer.mjs",
    "audio/playback-handshake.mjs",
)
STATIC_ROUTES = {
    "/": "index.html",
    **{f"/{relative}": relative for relative in REQUIRED_WEB_ASSETS},
}
SESSION_PATH = re.compile(
    r"^/api/sessions/(?P<session>demo-session-[0-9]{4,12})/"
    r"(?P<action>transcription|evaluation|official-answer|official-answer\.wav|next|close)$"
)
MEDIA_PATH = re.compile(r"^/api/media/(?P<media>[a-z0-9-]+)$")
MAX_JSON_BYTES = 64 * 1024
MAX_ACTIVE_SESSIONS = 64


class DemoHttpError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class _SessionRecord:
    session: DemoSession
    lock: threading.RLock


class DemoApplication:
    """Thread-safe registry around isolated, deterministic demo sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0
        self._sessions: dict[str, _SessionRecord] = {}
        self._media_deck = build_synthetic_deck()

    def create_session(self, profile: str) -> SessionView:
        try:
            selected = PresentationProfile(profile)
        except ValueError as exc:
            raise DemoHttpError(HTTPStatus.BAD_REQUEST, "unknown study profile") from exc
        with self._lock:
            closed = [
                session_id
                for session_id, record in self._sessions.items()
                if record.session.closed
            ]
            for session_id in closed:
                del self._sessions[session_id]
            if len(self._sessions) >= MAX_ACTIVE_SESSIONS:
                raise DemoHttpError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "too many active demo sessions",
                )
            self._counter += 1
            session_id = f"demo-session-{self._counter:04d}"
            record = _SessionRecord(DemoSession(session_id), threading.RLock())
            self._sessions[session_id] = record
        with record.lock:
            return asyncio.run(record.session.open(selected))

    def session(self, session_id: str) -> _SessionRecord:
        with self._lock:
            record = self._sessions.get(session_id)
        if record is None:
            raise DemoHttpError(HTTPStatus.NOT_FOUND, "demo session not found")
        return record

    def media(self, media_id: str) -> tuple[str, bytes]:
        try:
            artifact = self._media_deck.render_media(media_id)
        except KeyError as exc:
            raise DemoHttpError(HTTPStatus.NOT_FOUND, "synthetic media not found") from exc
        return artifact.reference.media_type, artifact.content


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], web_root: Path) -> None:
        self.application = DemoApplication()
        self.static_files = {
            route: web_root.joinpath(*Path(relative).parts)
            for route, relative in STATIC_ROUTES.items()
        }
        super().__init__(address, DemoRequestHandler)


class DemoRequestHandler(BaseHTTPRequestHandler):
    server: DemoServer
    protocol_version = "HTTP/1.1"
    static_types: ClassVar[dict[str, str]] = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
    }

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        try:
            self._validate_local_request()
            path = urlsplit(self.path).path
            if path in self.server.static_files:
                self._serve_static(self.server.static_files[path])
                return
            media_match = MEDIA_PATH.fullmatch(path)
            if media_match:
                media_type, content = self.server.application.media(
                    media_match.group("media")
                )
                self._send_bytes(HTTPStatus.OK, media_type, content, cache=True)
                return
            session_match = SESSION_PATH.fullmatch(path)
            if session_match and session_match.group("action") == "official-answer.wav":
                record = self.server.application.session(session_match.group("session"))
                with record.lock:
                    speech = record.session.official_speech()
                if speech is None:
                    raise DemoHttpError(
                        HTTPStatus.CONFLICT,
                        "official answer has not been requested",
                    )
                self._send_bytes(
                    HTTPStatus.OK,
                    speech.media_type,
                    speech.content,
                    cache=False,
                )
                return
            raise DemoHttpError(HTTPStatus.NOT_FOUND, "resource not found")
        except DemoHttpError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (OSError, ValueError, RuntimeError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": _public_error(exc)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        try:
            self._validate_local_request()
            path = urlsplit(self.path).path
            if path == "/api/sessions":
                payload = self._read_json()
                view = self.server.application.create_session(
                    str(payload.get("profile", PresentationProfile.FULL.value))
                )
                self._send_json(HTTPStatus.CREATED, _view_payload(view))
                return

            match = SESSION_PATH.fullmatch(path)
            if not match:
                raise DemoHttpError(HTTPStatus.NOT_FOUND, "resource not found")
            record = self.server.application.session(match.group("session"))
            action = match.group("action")
            with record.lock:
                if action == "transcription":
                    transcript = asyncio.run(record.session.simulate_transcription())
                    response = {"transcript": transcript}
                elif action == "evaluation":
                    payload = self._read_json()
                    answer = payload.get("answer")
                    if not isinstance(answer, str) or not answer.strip():
                        raise DemoHttpError(
                            HTTPStatus.BAD_REQUEST,
                            "answer must be a non-empty string",
                        )
                    result = asyncio.run(record.session.evaluate(answer))
                    response = {"evaluation": _evaluation_payload(result)}
                elif action == "official-answer":
                    official = asyncio.run(record.session.request_official_answer())
                    response = {
                        "official_answer": official.official_answer,
                        "speech_url": f"/api/sessions/{record.session.session_id}/official-answer.wav",
                        "speech_kind": "deterministic-tone-fake",
                    }
                elif action == "next":
                    response = _view_payload(record.session.advance())
                elif action == "close":
                    result = asyncio.run(record.session.close())
                    response = {
                        "status": result.status.value,
                        "message": "Sessão local encerrada e sincronização simulada confirmada.",
                    }
                else:
                    raise DemoHttpError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            self._send_json(HTTPStatus.OK, response)
        except DemoHttpError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
        except (OSError, ValueError, RuntimeError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": _public_error(exc)})

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("content-length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise DemoHttpError(HTTPStatus.BAD_REQUEST, "invalid content length") from exc
        if length < 0 or length > MAX_JSON_BYTES:
            raise DemoHttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise DemoHttpError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
        return payload

    def _validate_local_request(self) -> None:
        port = self.server.server_port
        allowed_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
        host = self.headers.get("host", "").strip().lower()
        if host not in allowed_hosts:
            raise DemoHttpError(HTTPStatus.FORBIDDEN, "non-local host rejected")
        origin = self.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.port != port
            ):
                raise DemoHttpError(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
        if self.headers.get("sec-fetch-site", "").lower() == "cross-site":
            raise DemoHttpError(HTTPStatus.FORBIDDEN, "cross-site request rejected")

    def _serve_static(self, path: Path) -> None:
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise DemoHttpError(HTTPStatus.NOT_FOUND, "static resource not found") from exc
        media_type = self.static_types.get(path.suffix)
        if media_type is None:
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, media_type, content, cache=False)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(status, "application/json; charset=utf-8", content, cache=False)

    def _send_bytes(
        self,
        status: HTTPStatus,
        media_type: str,
        content: bytes,
        *,
        cache: bool,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", media_type)
        self.send_header("content-length", str(len(content)))
        self.send_header("cache-control", "public, max-age=3600" if cache else "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header(
            "content-security-policy",
            "default-src 'self'; img-src 'self'; media-src 'self'; "
            "style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        """Keep responses and user-provided bodies out of default logs."""


def _public_error(exc: Exception) -> str:
    allowed = {
        "official_answer_requires_evaluation": "Evaluate the response before requesting the official answer.",
        "advance_requires_evaluation": "Evaluate the response before advancing.",
        "no_active_synthetic_session": "The demo session is no longer active.",
    }
    return allowed.get(str(exc), "The requested demo action is not available in this state.")


def _view_payload(view: SessionView) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": view.session_id,
        "profile": view.profile.value,
        "position": view.position,
        "total": view.total,
        "complete": view.complete,
        "card": None,
    }
    if view.card is not None:
        payload["card"] = _card_payload(view.card)
    return payload


def _card_payload(card: PublicCard) -> dict[str, Any]:
    return {
        "id": card.card_id,
        "label": card.label,
        "prompt": card.prompt,
        "languages": [language.value for language in card.languages],
        "terms": list(card.terms),
        "media": [
            {
                "id": item.media_id,
                "kind": item.kind.value,
                "media_type": item.media_type,
                "url": item.url,
                "alt_text": item.alt_text,
                "description": item.description,
                "width": item.width,
                "height": item.height,
                "duration_ms": item.duration_ms,
            }
            for item in card.media
        ],
    }


def _evaluation_payload(result: EvaluationResult) -> dict[str, Any]:
    return {
        "verdict": result.verdict.value,
        "feedback": result.feedback,
        "covered_concepts": list(result.covered_concepts),
        "missing_concepts": list(result.missing_concepts),
        "incorrect_concepts": list(result.incorrect_concepts),
    }


def _require_loopback(host: str) -> None:
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("demo_host_must_be_loopback") from exc
    if not address.is_loopback:
        raise ValueError("demo_host_must_be_loopback")


def _validate_web_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("required_web_assets_unavailable")
    for relative in REQUIRED_WEB_ASSETS:
        asset = root.joinpath(*Path(relative).parts)
        if asset.is_symlink() or not asset.is_file():
            raise RuntimeError("required_web_assets_unavailable")
    return root


def resolve_web_root(module_file: Path | None = None) -> Path:
    """Return a complete package-local tree or a proven source-layout tree."""

    resolved_module = (module_file or Path(__file__)).resolve()
    package_root = resolved_module.parent / "web"
    if package_root.exists() or package_root.is_symlink():
        return _validate_web_root(package_root)

    try:
        project_root = resolved_module.parents[2]
    except IndexError as exc:
        raise RuntimeError("required_web_assets_unavailable") from exc
    expected_package = project_root / "src" / "voice_study_companion"
    if resolved_module.parent != expected_package.resolve():
        raise RuntimeError("required_web_assets_unavailable")
    return _validate_web_root(project_root / "web")


def make_server(host: str = "127.0.0.1", port: int = 8765) -> DemoServer:
    _require_loopback(host)
    return DemoServer((host, port), resolve_web_root())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local voice-study demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    config = load_runtime_config()
    if config.mode is not RuntimeMode.DEMO:
        parser.error("live adapters are placeholders and are not enabled in this candidate")
    try:
        server = make_server(args.host, args.port)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        print(f"Voice Study Companion demo: http://{args.host}:{server.server_port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
