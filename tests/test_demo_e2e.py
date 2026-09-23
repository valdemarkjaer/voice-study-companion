from __future__ import annotations

import asyncio
import dataclasses
import unittest
from unittest import mock

from voice_study_companion.contracts import (
    EvaluationVerdict,
    PresentationProfile,
    SpeechPurpose,
    SyncBoundary,
    SyncStatus,
)
from voice_study_companion.demo.session import DemoSession


class CredentialFreeWalkthroughTests(unittest.TestCase):
    def test_principal_walkthrough_is_offline_and_answer_is_on_request(self) -> None:
        async def walkthrough() -> tuple[DemoSession, object, object]:
            session = DemoSession("demo-session-0001")
            opened = await session.open(PresentationProfile.FULL)
            self.assertIsNotNone(opened.card)
            assert opened.card is not None
            self.assertNotIn(
                "official_answer",
                {field.name for field in dataclasses.fields(opened.card)},
            )

            transcript = await session.simulate_transcription()
            evaluation = await session.evaluate(transcript)
            self.assertEqual(evaluation.verdict, EvaluationVerdict.CORRECT)
            self.assertEqual(session.synthesizer.requests, [])

            official = await session.request_official_answer()
            self.assertTrue(official.official_answer.startswith("Latency"))
            self.assertEqual(len(session.synthesizer.requests), 1)
            speech_request = session.synthesizer.requests[0]
            self.assertEqual(speech_request.purpose, SpeechPurpose.OFFICIAL_ANSWER)
            self.assertEqual(
                tuple(segment.text for segment in speech_request.segments),
                (official.official_answer,),
            )
            self.assertNotIn(opened.card.prompt, official.official_answer)

            advanced = session.advance()
            self.assertFalse(advanced.complete)
            closed = await session.close()
            return session, advanced, closed

        with mock.patch(
            "socket.create_connection",
            side_effect=AssertionError("outbound network attempted"),
        ):
            session, advanced, closed = asyncio.run(walkthrough())

        self.assertIsNotNone(advanced.card)
        self.assertEqual(closed.status, SyncStatus.CONFIRMED)
        self.assertEqual(
            tuple(request.boundary for request in session.synchronizer.requests),
            (SyncBoundary.SESSION_START, SyncBoundary.SESSION_END),
        )

    def test_transit_walkthrough_never_serves_media(self) -> None:
        async def walkthrough() -> list[str]:
            session = DemoSession("demo-session-transit")
            view = await session.open(PresentationProfile.TRANSIT)
            seen: list[str] = []
            while not view.complete:
                assert view.card is not None
                self.assertEqual(view.card.media, ())
                seen.append(view.card.card_id)
                await session.evaluate("purpose-made incomplete response")
                view = session.advance()
            await session.close()
            return seen

        self.assertEqual(
            asyncio.run(walkthrough()),
            ["bilingual-latency", "two-part-playback"],
        )


if __name__ == "__main__":
    unittest.main()
