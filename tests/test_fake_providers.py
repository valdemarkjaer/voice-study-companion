from __future__ import annotations

import asyncio
import io
import unittest
import wave

from voice_study_companion.contracts import (
    AudioInput,
    EvaluationRequest,
    EvaluationVerdict,
    LanguageTag,
    SpeechPurpose,
    SpeechRequest,
    SpeechSegment,
    StudyCard,
    SyncBoundary,
    SyncRequest,
    SyncStatus,
    TranscriptionRequest,
)
from voice_study_companion.demo.providers import (
    DeterministicEvaluator,
    DeterministicSpeechSynthesizer,
    DeterministicSynchronizer,
    DeterministicTranscriber,
    assert_provider_contracts,
)


PT_BR = LanguageTag("pt-BR")
EN_US = LanguageTag("en-US")


def latency_card() -> StudyCard:
    return StudyCard(
        card_id="synthetic-latency",
        scope="public-demo",
        prompt="What does latency mean?",
        official_answer="Latency is the delay between input and response.",
        expected_concepts=("delay", "input-to-response"),
        selection_token="synthetic-token",
        languages=(PT_BR, EN_US),
    )


class FakeProviderTests(unittest.TestCase):
    def test_providers_satisfy_public_runtime_contracts(self) -> None:
        assert_provider_contracts()

    def test_scripted_transcription_is_stable_and_offline(self) -> None:
        provider = DeterministicTranscriber("latência means delay")
        request = TranscriptionRequest(
            turn_id="turn-1",
            audio=AudioInput(content=b"purpose-made-audio"),
            language_hints=(PT_BR, EN_US),
        )

        first = asyncio.run(provider.transcribe(request))
        second = asyncio.run(provider.transcribe(request))

        self.assertEqual(first, second)
        self.assertEqual(first.text, "latência means delay")
        self.assertEqual(len(provider.requests), 2)

    def test_evaluator_awards_transparent_partial_credit(self) -> None:
        result = asyncio.run(
            DeterministicEvaluator().evaluate(
                EvaluationRequest(
                    turn_id="turn-2",
                    card=latency_card(),
                    learner_answer="É um delay.",
                )
            )
        )

        self.assertEqual(result.verdict, EvaluationVerdict.PARTIAL)
        self.assertEqual(result.covered_concepts, ("delay",))
        self.assertEqual(result.missing_concepts, ("input-to-response",))

    def test_evaluator_accepts_mixed_language_complete_answer(self) -> None:
        result = asyncio.run(
            DeterministicEvaluator().evaluate(
                EvaluationRequest(
                    turn_id="turn-3",
                    card=latency_card(),
                    learner_answer="Latency é o atraso entre a entrada e resposta.",
                )
            )
        )

        self.assertEqual(result.verdict, EvaluationVerdict.CORRECT)
        self.assertEqual(
            result.covered_concepts,
            ("delay", "input-to-response"),
        )

    def test_fake_speech_is_valid_reproducible_wave_audio(self) -> None:
        request = SpeechRequest(
            utterance_id="utterance-1",
            purpose=SpeechPurpose.FEEDBACK,
            segments=(SpeechSegment(PT_BR, "Resposta avaliada."),),
        )
        provider = DeterministicSpeechSynthesizer()

        first = asyncio.run(provider.synthesize(request))
        second = asyncio.run(provider.synthesize(request))

        self.assertEqual(first, second)
        with wave.open(io.BytesIO(first.content), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 1)
            self.assertEqual(rendered.getframerate(), 8_000)
            self.assertGreater(rendered.getnframes(), 0)

    def test_fake_sync_confirms_without_external_state(self) -> None:
        provider = DeterministicSynchronizer()
        request = SyncRequest(
            operation_id="sync-end-1",
            session_id="session-1",
            boundary=SyncBoundary.SESSION_END,
        )

        result = asyncio.run(provider.sync(request))

        self.assertEqual(result.status, SyncStatus.CONFIRMED)
        self.assertEqual(result.changed_items, 0)
        self.assertEqual(provider.requests, [request])


if __name__ == "__main__":
    unittest.main()
