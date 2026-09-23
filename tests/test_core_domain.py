from __future__ import annotations

import ast
import asyncio
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voice_study_companion.contracts import (
    LanguageTag,
    ReviewRating,
    SpeechSegment,
    StudyCard,
)
from voice_study_companion.core.orchestrator import (
    OrchestratorResultKind,
    VoiceStudyOrchestrator,
)
from voice_study_companion.core.domain import (
    AudioEncoding,
    AudioFrame,
    AuthorizedUtterance,
    Capability,
    DialogueIntent,
    DialoguePlan,
    GradeResult,
    GradeVerdict,
    Operation,
    ProviderCapabilities,
    Transcript,
    TurnIdentity,
    UsageAmounts,
    UtteranceKind,
)
from voice_study_companion.core.ports import (
    AnswerGrader,
    DialogueModel,
    Synthesizer,
    Transcriber,
)
from voice_study_companion.core.state import VoicePhase
from voice_study_companion.providers import (
    FakeAnswerGrader,
    FakeDialogueModel,
    FakeSynthesizer,
    FakeTranscriber,
)
from voice_study_companion.tutoring.review import ConfirmationSource


def _identity() -> TurnIdentity:
    return TurnIdentity("session", "synthetic-card", "turn", 1, 1)


def _capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        operations=frozenset(Operation),
        features=frozenset(Capability),
        languages=frozenset({LanguageTag("pt-BR"), LanguageTag("en-US")}),
        audio_encodings=frozenset({AudioEncoding.PCM_S16LE}),
    )


class _AllPortsFake:
    capabilities = _capabilities()

    async def open(
        self,
        identity: object,
        hints: object,
        cancellation: object,
    ) -> object:
        return object()

    async def respond(
        self,
        context: object,
        transcript: object,
        cancellation: object,
    ) -> object:
        return object()

    async def grade(
        self,
        context: object,
        transcript: object,
        cancellation: object,
    ) -> object:
        return object()

    async def _empty(self) -> None:
        return None

    def stream(self, utterance: object, cancellation: object) -> object:
        return self._empty()


class CoreDomainTests(unittest.TestCase):
    def test_value_objects_are_immutable_and_validate_semantics(self) -> None:
        identity = _identity()
        transcript = Transcript(
            identity=identity,
            raw_text="o producer terminou, but the local buffer did not drain",
            normalized_text="o producer terminou, but the local buffer did not drain",
            detected_languages=(LanguageTag("pt-BR"), LanguageTag("en-US")),
            confidence=0.92,
            final=True,
        )
        plan = DialoguePlan(
            identity=identity,
            intent=DialogueIntent.CLARIFICATION,
            display_text="Local buffer significa buffer local.",
            speech_segments=(
                SpeechSegment(
                    LanguageTag("pt-BR"),
                    "Local buffer significa buffer local.",
                ),
            ),
        )
        grade = GradeResult(
            identity=identity,
            verdict=GradeVerdict.PARTIAL,
            covered_concepts=("producer-ended",),
            missing_concepts=("local-buffer-drained",),
            incorrect_concepts=(),
            confidence=0.86,
            explanation="The local drain signal is still missing.",
            proposed_rating=ReviewRating.HARD,
        )
        now = datetime.now(UTC)
        utterance = AuthorizedUtterance(
            utterance_id="utterance-1",
            identity=identity,
            kind=UtteranceKind.FEEDBACK,
            segments=plan.speech_segments,
            created_at=now,
            expires_at=now + timedelta(seconds=30),
        )

        self.assertEqual(
            transcript.detected_languages,
            (LanguageTag("pt-BR"), LanguageTag("en-US")),
        )
        self.assertIs(grade.proposed_rating, ReviewRating.HARD)
        self.assertEqual(utterance.identity, identity)
        with self.assertRaises(FrozenInstanceError):
            transcript.raw_text = "mutated"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "confidence"):
            Transcript(identity, "raw", "normalized", (), 1.1, True)
        with self.assertRaisesRegex(ValueError, "ungradable"):
            GradeResult(
                identity,
                GradeVerdict.UNGRADABLE,
                (),
                (),
                (),
                0.2,
                "Not enough evidence.",
                ReviewRating.AGAIN,
            )
        with self.assertRaisesRegex(ValueError, "disjoint"):
            GradeResult(
                identity,
                GradeVerdict.PARTIAL,
                ("same",),
                ("same",),
                (),
                0.8,
                "Contradictory classification.",
                ReviewRating.HARD,
            )
        with self.assertRaisesRegex(ValueError, "audio_frame"):
            AudioFrame(identity, b"")
        with self.assertRaisesRegex(ValueError, "non_negative"):
            UsageAmounts(input_tokens=-1)

    def test_provider_capabilities_are_explicit(self) -> None:
        capabilities = _capabilities()
        self.assertTrue(
            capabilities.supports(
                Operation.TRANSCRIPTION,
                features=frozenset(
                    {Capability.STREAMING_INPUT, Capability.KEYWORD_HINTS}
                ),
                languages=frozenset(
                    {LanguageTag("pt-BR"), LanguageTag("en-US")}
                ),
                audio_encoding=AudioEncoding.PCM_S16LE,
            )
        )
        self.assertFalse(
            capabilities.supports(
                Operation.TRANSCRIPTION,
                languages=frozenset({LanguageTag("es")}),
            )
        )

    def test_runtime_ports_are_structural_and_provider_neutral(self) -> None:
        fake = _AllPortsFake()
        self.assertIsInstance(fake, Transcriber)
        self.assertIsInstance(fake, DialogueModel)
        self.assertIsInstance(fake, AnswerGrader)
        self.assertIsInstance(fake, Synthesizer)

    def test_core_boundary_imports_no_paid_provider_sdk(self) -> None:
        source_root = (
            Path(__file__).parents[1] / "src" / "voice_study_companion"
        )
        packages = (source_root / "core", source_root / "providers")
        forbidden_roots = {"openai", "deepgram", "livekit", "pipecat"}
        for package in packages:
            for path in package.glob("*.py"):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                imported: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(
                            alias.name.split(".")[0] for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split(".")[0])
                self.assertTrue(
                    imported.isdisjoint(forbidden_roots),
                    (path, imported),
                )


class CoreOrchestratorTests(unittest.TestCase):
    @staticmethod
    def _card() -> StudyCard:
        return StudyCard(
            card_id="protected-playback",
            scope="synthetic-public-demo",
            prompt="Name the two signals that complete protected playback.",
            official_answer=(
                "The producer ended and the local playback buffer drained."
            ),
            expected_concepts=("producer-ended", "local-buffer-drained"),
            selection_token="synthetic-selection-protected-playback",
            languages=(LanguageTag("en-US"), LanguageTag("pt-BR")),
            tags=("synthetic", "public-demo"),
        )

    def test_answer_is_read_only_after_explicit_request_and_review_is_inert(
        self,
    ) -> None:
        async def walkthrough() -> None:
            orchestrator = VoiceStudyOrchestrator(
                session_id="offline-session",
                transcriber=FakeTranscriber(),
                dialogue=FakeDialogueModel(),
                grader=FakeAnswerGrader(),
                synthesizer=FakeSynthesizer(),
            )
            card = self._card()
            prompt = await orchestrator.prepare_card(card)
            self.assertIs(prompt.kind, OrchestratorResultKind.PROMPT)
            self.assertIs(orchestrator.state.phase, VoicePhase.SPEAKING_PROMPT)
            assert prompt.utterance is not None
            orchestrator.acknowledge_playback(
                utterance_id=prompt.utterance.utterance_id
            )

            graded = await orchestrator.submit_text(
                "The producer ended and the local buffer drained.",
                detected_languages=(LanguageTag("en-US"),),
            )
            self.assertIs(graded.kind, OrchestratorResultKind.GRADED)
            assert graded.grade_decision is not None
            self.assertTrue(graded.grade_decision.review_ready)
            self.assertIsNotNone(graded.utterance)
            assert graded.utterance is not None
            self.assertNotEqual(
                tuple(segment.text for segment in graded.utterance.segments),
                (card.official_answer,),
            )
            orchestrator.acknowledge_playback(
                utterance_id=graded.utterance.utterance_id
            )
            self.assertIs(orchestrator.state.phase, VoicePhase.AWAITING_RATING)

            official = await orchestrator.speak_official_answer()
            self.assertIs(official.kind, OrchestratorResultKind.OFFICIAL_ANSWER)
            assert official.utterance is not None
            spoken = " ".join(
                segment.text for segment in official.utterance.segments
            )
            self.assertEqual(spoken, card.official_answer)
            self.assertNotIn(card.prompt, spoken)
            orchestrator.acknowledge_playback(
                utterance_id=official.utterance.utterance_id
            )

            confirmation = orchestrator.confirm_rating(
                source=ConfirmationSource.TOUCH
            )
            self.assertIs(
                confirmation.kind,
                OrchestratorResultKind.RATING_CONFIRMED,
            )
            assert confirmation.pending_review is not None
            self.assertIs(confirmation.pending_review.rating, ReviewRating.GOOD)
            self.assertEqual(len(confirmation.pending_review.idempotency_key), 64)
            self.assertFalse(hasattr(orchestrator, "review_adapter"))

        asyncio.run(walkthrough())

    def test_transcript_correction_regrades_in_a_fresh_generation(self) -> None:
        async def walkthrough() -> None:
            orchestrator = VoiceStudyOrchestrator(
                session_id="offline-correction",
                transcriber=FakeTranscriber(),
                dialogue=FakeDialogueModel(),
                grader=FakeAnswerGrader(),
                synthesizer=FakeSynthesizer(),
            )
            prompt = await orchestrator.prepare_card(self._card())
            assert prompt.utterance is not None
            orchestrator.acknowledge_playback(
                utterance_id=prompt.utterance.utterance_id
            )
            first = await orchestrator.submit_text("The producer ended.")
            assert first.utterance is not None
            orchestrator.acknowledge_playback(
                utterance_id=first.utterance.utterance_id
            )
            first_generation = first.identity.generation_id

            corrected = await orchestrator.correct_transcript(
                "The producer ended and the local playback buffer drained."
            )
            self.assertIs(
                corrected.kind,
                OrchestratorResultKind.CORRECTED_GRADE,
            )
            self.assertGreater(
                corrected.identity.generation_id,
                first_generation,
            )
            assert corrected.grade_decision is not None
            self.assertTrue(corrected.grade_decision.review_ready)
            self.assertEqual(
                corrected.grade_decision.result.turn_id,
                corrected.identity.turn_id,
            )

        asyncio.run(walkthrough())


if __name__ == "__main__":
    unittest.main()
