from __future__ import annotations

import json
import threading
import unittest
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from voice_study_companion.server import (
    MAX_ACTIVE_SESSIONS,
    REQUIRED_WEB_ASSETS,
    DemoApplication,
    DemoHttpError,
    make_server,
)


class DemoServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = make_server(port=0)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, bytes]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(headers or {})
        if data:
            request_headers.setdefault("content-type", "application/json")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        with urlopen(request, timeout=2) as response:
            return response.status, response.headers["content-type"], response.read()

    def post_json(
        self,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        status, media_type, body = self.request(path, method="POST", payload=payload)
        self.assertIn(status, (HTTPStatus.OK, HTTPStatus.CREATED))
        self.assertTrue(media_type.startswith("application/json"))
        return json.loads(body)

    def test_static_surface_has_neutral_offline_branding(self) -> None:
        status, _, content = self.request("/")

        rendered = content.decode("utf-8")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("Voice Study Companion", rendered)
        self.assertIn("Sem nuvem", rendered)

    def test_every_required_browser_and_audio_asset_is_served(self) -> None:
        for relative in REQUIRED_WEB_ASSETS:
            with self.subTest(asset=relative):
                status, media_type, content = self.request(f"/{relative}")
                self.assertEqual(status, HTTPStatus.OK)
                self.assertTrue(
                    media_type.startswith(("text/", "application/javascript")),
                    media_type,
                )
                self.assertTrue(content)

    def test_http_walkthrough_keeps_answer_hidden_until_request(self) -> None:
        opened = self.post_json("/api/sessions", {"profile": "full"})
        session_id = opened["session_id"]
        self.assertNotIn("official_answer", json.dumps(opened))

        transcript = self.post_json(f"/api/sessions/{session_id}/transcription")
        evaluated = self.post_json(
            f"/api/sessions/{session_id}/evaluation",
            {"answer": transcript["transcript"]},
        )
        self.assertEqual(evaluated["evaluation"]["verdict"], "correct")
        self.assertNotIn("official_answer", json.dumps(evaluated))

        official = self.post_json(f"/api/sessions/{session_id}/official-answer")
        self.assertTrue(official["official_answer"].startswith("Latency"))
        status, media_type, audio = self.request(official["speech_url"])
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(media_type, "audio/wav")
        self.assertTrue(audio.startswith(b"RIFF"))

        advanced = self.post_json(f"/api/sessions/{session_id}/next")
        self.assertEqual(advanced["position"], 2)
        closed = self.post_json(f"/api/sessions/{session_id}/close")
        self.assertEqual(closed["status"], "confirmed")

    def test_transit_session_excludes_media_and_invalid_order_is_rejected(self) -> None:
        opened = self.post_json("/api/sessions", {"profile": "transit"})
        session_id = opened["session_id"]
        self.assertEqual(opened["card"]["media"], [])

        with self.assertRaises(HTTPError) as captured:
            self.request(
                f"/api/sessions/{session_id}/official-answer",
                method="POST",
            )
        try:
            self.assertEqual(captured.exception.code, HTTPStatus.CONFLICT)
        finally:
            captured.exception.close()

    def test_server_refuses_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            make_server(host="0.0.0.0", port=0)

    def test_application_bounds_active_in_memory_sessions(self) -> None:
        application = DemoApplication()
        for _ in range(MAX_ACTIVE_SESSIONS):
            application.create_session("transit")

        with self.assertRaises(DemoHttpError) as captured:
            application.create_session("transit")
        self.assertEqual(captured.exception.status, HTTPStatus.SERVICE_UNAVAILABLE)

    def test_server_rejects_cross_origin_mutation(self) -> None:
        with self.assertRaises(HTTPError) as captured:
            self.request(
                "/api/sessions",
                method="POST",
                payload={"profile": "full"},
                headers={"origin": "https://unrelated.example.invalid"},
            )
        try:
            self.assertEqual(captured.exception.code, HTTPStatus.FORBIDDEN)
        finally:
            captured.exception.close()


if __name__ == "__main__":
    unittest.main()
