from __future__ import annotations

import asyncio
import unittest
from dataclasses import FrozenInstanceError

from voice_study_companion import (
    AnswerEvaluator,
    AudioInput,
    CardAdapter,
    CardSelectionRequest,
    CardSelectionResult,
    CardSelectionStatus,
    CardSide,
    CollectionSynchronizer,
    EvaluationRequest,
    EvaluationResult,
    EvaluationVerdict,
    LanguageTag,
    MediaArtifact,
    MediaKind,
    MediaReference,
    MediaRequest,
    PresentationProfile,
    ReviewRating,
    ReviewReceipt,
    ReviewStatus,
    ReviewSubmission,
    SpeechAudio,
    SpeechPurpose,
    SpeechRequest,
    SpeechSegment,
    SpeechSynthesizer,
    StudyCard,
    SyncBoundary,
    SyncRequest,
    SyncResult,
    SyncStatus,
    Transcript,
    Transcriber,
    TranscriptionRequest,
)


PT_BR = LanguageTag("pt-BR")
EN_US = LanguageTag("en-US")


def synthetic_card() -> StudyCard:
    return StudyCard(
        card_id="synthetic-card-1",
        scope="public-demo",
        prompt="Name the two colors in the generated diagram.",
        official_answer="Blue and amber.",
        expected_concepts=("blue", "amber"),
        selection_token="offline-selection-token-1",
        languages=(EN_US, PT_BR),
        media=(
            MediaReference(
                media_id="generated-diagram",
                side=CardSide.QUESTION,
                kind=MediaKind.IMAGE,
                media_type="image/svg+xml",
                alt_text="Two synthetic circles, one blue and one amber.",
            ),
        ),
        tags=("synthetic", "bilingual"),
    )


class OfflineCardAdapter:
    def __init__(self, card: StudyCard) -> None:
        self.card = card
        self.reviews: dict[str, ReviewReceipt] = {}

    async def select_next(
        self,
        request: CardSelectionRequest,
    ) -> CardSelectionResult:
        if request.profile is PresentationProfile.TRANSIT and self.card.media:
            return CardSelectionResult.empty(CardSelectionStatus.NO_ELIGIBLE_CARD)
        return CardSelectionResult.ready(self.card)

    async def read_media(self, request: MediaRequest) -> MediaArtifact:
        reference = next(
            item for item in self.card.media if item.media_id == request.media_id
        )
        return MediaArtifact(reference=reference, content=b"<svg />")

    async def record_review(self, submission: ReviewSubmission) -> ReviewReceipt:
        previous = self.reviews.get(submission.operation_id)
        if previous is not None:
            return ReviewReceipt(
                operation_id=previous.operation_id,
                card_id=previous.card_id,
                status=ReviewStatus.ALREADY_APPLIED,
            )
        receipt = ReviewReceipt(
            operation_id=submission.operation_id,
            card_id=submission.card_id,
            status=ReviewStatus.APPLIED,
        )
        self.reviews[submission.operation_id] = receipt
        return receipt


class OfflineTranscriber:
    async def transcribe(self, request: TranscriptionRequest) -> Transcript:
        return Transcript(
            turn_id=request.turn_id,
            text="blue",
            languages=(EN_US,),
            confidence=1.0,
        )


class OfflineEvaluator:
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(
            turn_id=request.turn_id,
            verdict=EvaluationVerdict.PARTIAL,
            feedback="Blue is covered; amber is still missing.",
            covered_concepts=("blue",),
            missing_concepts=("amber",),
            confidence=1.0,
            proposed_rating=ReviewRating.HARD,
        )


class OfflineSpeechSynthesizer:
    async def synthesize(self, request: SpeechRequest) -> SpeechAudio:
        rendered = " ".join(segment.text for segment in request.segments)
        return SpeechAudio(
            utterance_id=request.utterance_id,
            content=rendered.encode("utf-8"),
            media_type="audio/wav",
            duration_ms=len(rendered) * 10,
        )


class OfflineSynchronizer:
    async def sync(self, request: SyncRequest) -> SyncResult:
        return SyncResult(
            operation_id=request.operation_id,
            status=SyncStatus.CONFIRMED,
        )


class ValueObjectTests(unittest.TestCase):
    def test_card_and_nested_values_are_immutable(self) -> None:
        card = synthetic_card()

        with self.assertRaises(FrozenInstanceError):
            card.prompt = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            card.media[0].alt_text = "changed"  # type: ignore[misc]

    def test_card_rejects_duplicate_concepts_and_media_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected_concepts_must_be_unique"):
            StudyCard(
                card_id="card",
                scope="demo",
                prompt="Prompt",
                official_answer="Answer",
                expected_concepts=("same", "same"),
                selection_token="token",
                languages=(EN_US,),
            )

        duplicate_media = MediaReference(
            media_id="same",
            side=CardSide.QUESTION,
            kind=MediaKind.AUDIO,
            media_type="audio/wav",
        )
        with self.assertRaisesRegex(ValueError, "media_ids_must_be_unique"):
            StudyCard(
                card_id="card",
                scope="demo",
                prompt="Prompt",
                official_answer="Answer",
                expected_concepts=("concept",),
                selection_token="token",
                languages=(EN_US,),
                media=(duplicate_media, duplicate_media),
            )

    def test_media_and_selection_invariants_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "match_media_kind"):
            MediaReference(
                media_id="bad",
                side=CardSide.QUESTION,
                kind=MediaKind.IMAGE,
                media_type="audio/wav",
                alt_text="Description",
            )
        with self.assertRaisesRegex(ValueError, "image_alt_text"):
            MediaReference(
                media_id="missing-alt",
                side=CardSide.QUESTION,
                kind=MediaKind.IMAGE,
                media_type="image/png",
            )
        with self.assertRaisesRegex(ValueError, "ready_selection_requires_card"):
            CardSelectionResult(CardSelectionStatus.READY)

    def test_evaluation_requires_disjoint_concepts_and_safe_ungradable_result(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "must_be_disjoint"):
            EvaluationResult(
                turn_id="turn",
                verdict=EvaluationVerdict.PARTIAL,
                feedback="Conflicting structure.",
                covered_concepts=("blue",),
                missing_concepts=("blue",),
            )
        with self.assertRaisesRegex(ValueError, "cannot_propose_rating"):
            EvaluationResult(
                turn_id="turn",
                verdict=EvaluationVerdict.UNGRADABLE,
                feedback="No usable answer.",
                proposed_rating=ReviewRating.AGAIN,
            )

    def test_unknown_sync_outcome_is_never_retry_safe(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot_be_retry_safe"):
            SyncResult(
                operation_id="sync-1",
                status=SyncStatus.UNKNOWN,
                message="Outcome cannot be established.",
                retry_safe=True,
            )


class OfflineContractTests(unittest.TestCase):
    def test_offline_fakes_satisfy_every_runtime_protocol(self) -> None:
        card_adapter = OfflineCardAdapter(synthetic_card())

        self.assertIsInstance(card_adapter, CardAdapter)
        self.assertIsInstance(OfflineTranscriber(), Transcriber)
        self.assertIsInstance(OfflineEvaluator(), AnswerEvaluator)
        self.assertIsInstance(OfflineSpeechSynthesizer(), SpeechSynthesizer)
        self.assertIsInstance(OfflineSynchronizer(), CollectionSynchronizer)

    def test_complete_contract_walkthrough_needs_no_private_service(self) -> None:
        async def walkthrough() -> tuple[
            CardSelectionResult,
            Transcript,
            EvaluationResult,
            SpeechAudio,
            ReviewReceipt,
            SyncResult,
        ]:
            cards = OfflineCardAdapter(synthetic_card())
            selection = await cards.select_next(
                CardSelectionRequest(session_id="session", scope="public-demo")
            )
            assert selection.card is not None
            transcript = await OfflineTranscriber().transcribe(
                TranscriptionRequest(
                    turn_id="turn-1",
                    audio=AudioInput(content=b"synthetic-wave"),
                    language_hints=(PT_BR, EN_US),
                    vocabulary=("blue", "amber"),
                )
            )
            evaluation = await OfflineEvaluator().evaluate(
                EvaluationRequest(
                    turn_id=transcript.turn_id,
                    card=selection.card,
                    learner_answer=transcript.text,
                )
            )
            speech = await OfflineSpeechSynthesizer().synthesize(
                SpeechRequest(
                    utterance_id="utterance-1",
                    purpose=SpeechPurpose.FEEDBACK,
                    segments=(SpeechSegment(EN_US, evaluation.feedback),),
                )
            )
            review = await cards.record_review(
                ReviewSubmission(
                    operation_id="review-1",
                    card_id=selection.card.card_id,
                    selection_token=selection.card.selection_token,
                    rating=ReviewRating.HARD,
                    confirmation_id="learner-confirmation-1",
                )
            )
            sync = await OfflineSynchronizer().sync(
                SyncRequest(
                    operation_id="sync-1",
                    session_id="session",
                    boundary=SyncBoundary.SESSION_END,
                )
            )
            return selection, transcript, evaluation, speech, review, sync

        selection, transcript, evaluation, speech, review, sync = asyncio.run(
            walkthrough()
        )

        self.assertEqual(selection.status, CardSelectionStatus.READY)
        self.assertEqual(transcript.text, "blue")
        self.assertEqual(evaluation.verdict, EvaluationVerdict.PARTIAL)
        self.assertEqual(speech.utterance_id, "utterance-1")
        self.assertEqual(review.status, ReviewStatus.APPLIED)
        self.assertEqual(sync.status, SyncStatus.CONFIRMED)

    def test_transit_selection_does_not_fall_back_to_visual_card(self) -> None:
        result = asyncio.run(
            OfflineCardAdapter(synthetic_card()).select_next(
                CardSelectionRequest(
                    session_id="session",
                    scope="public-demo",
                    profile=PresentationProfile.TRANSIT,
                )
            )
        )

        self.assertEqual(result.status, CardSelectionStatus.NO_ELIGIBLE_CARD)
        self.assertIsNone(result.card)


if __name__ == "__main__":
    unittest.main()
