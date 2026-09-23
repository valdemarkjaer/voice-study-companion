"""Public, provider-neutral contracts for the offline voice-study demo.

The module intentionally depends only on the Python standard library.  A
deterministic adapter can implement every protocol without credentials,
network access, a private collection, or a paid provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name}_must_not_be_blank")


def _require_unique_text(values: tuple[str, ...], field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name}_must_not_contain_blank_values")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name}_must_be_unique")


class PresentationProfile(StrEnum):
    FULL = "full"
    TRANSIT = "transit"


class CardSide(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"


class MediaKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"


class CardSelectionStatus(StrEnum):
    READY = "ready"
    NO_DUE_CARD = "no_due_card"
    NO_ELIGIBLE_CARD = "no_eligible_card"


class EvaluationVerdict(StrEnum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    UNGRADABLE = "ungradable"


class ReviewRating(StrEnum):
    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class ReviewStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"


class SpeechPurpose(StrEnum):
    CARD_PROMPT = "card_prompt"
    FEEDBACK = "feedback"
    OFFICIAL_ANSWER = "official_answer"
    DISCUSSION = "discussion"


class SyncBoundary(StrEnum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class SyncStatus(StrEnum):
    CONFIRMED = "confirmed"
    KNOWN_FAILURE = "known_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, order=True)
class LanguageTag:
    """Opaque BCP-47-style language identifier."""

    value: str

    def __post_init__(self) -> None:
        _require_text(self.value, "language_tag")
        if len(self.value) > 35:
            raise ValueError("language_tag_too_long")


@dataclass(frozen=True, slots=True)
class MediaReference:
    media_id: str
    side: CardSide
    kind: MediaKind
    media_type: str
    alt_text: str = ""

    def __post_init__(self) -> None:
        _require_text(self.media_id, "media_id")
        _require_text(self.media_type, "media_type")
        expected_prefix = f"{self.kind.value}/"
        if not self.media_type.lower().startswith(expected_prefix):
            raise ValueError("media_type_must_match_media_kind")
        if self.kind is MediaKind.IMAGE and not self.alt_text.strip():
            raise ValueError("image_alt_text_must_not_be_blank")


@dataclass(frozen=True, slots=True)
class StudyCard:
    """A structural card shape, independent from any scheduler product."""

    card_id: str
    scope: str
    prompt: str
    official_answer: str = field(repr=False)
    expected_concepts: tuple[str, ...]
    selection_token: str = field(repr=False)
    languages: tuple[LanguageTag, ...]
    media: tuple[MediaReference, ...] = ()
    tags: tuple[str, ...] = ()
    allowed_ratings: tuple[ReviewRating, ...] = (
        ReviewRating.AGAIN,
        ReviewRating.HARD,
        ReviewRating.GOOD,
        ReviewRating.EASY,
    )

    def __post_init__(self) -> None:
        for field_name in (
            "card_id",
            "scope",
            "prompt",
            "official_answer",
            "selection_token",
        ):
            _require_text(str(getattr(self, field_name)), field_name)
        if not self.expected_concepts:
            raise ValueError("expected_concepts_must_not_be_empty")
        _require_unique_text(self.expected_concepts, "expected_concepts")
        if not self.languages or len(set(self.languages)) != len(self.languages):
            raise ValueError("languages_must_be_nonempty_and_unique")
        media_ids = tuple(item.media_id for item in self.media)
        if len(set(media_ids)) != len(media_ids):
            raise ValueError("media_ids_must_be_unique")
        _require_unique_text(self.tags, "tags")
        if not self.allowed_ratings or len(set(self.allowed_ratings)) != len(
            self.allowed_ratings
        ):
            raise ValueError("allowed_ratings_must_be_nonempty_and_unique")


@dataclass(frozen=True, slots=True)
class CardSelectionRequest:
    session_id: str
    scope: str
    profile: PresentationProfile = PresentationProfile.FULL
    allow_card_audio_in_transit: bool = False

    def __post_init__(self) -> None:
        _require_text(self.session_id, "session_id")
        _require_text(self.scope, "scope")
        if (
            self.profile is PresentationProfile.FULL
            and self.allow_card_audio_in_transit
        ):
            raise ValueError("transit_audio_override_requires_transit_profile")


@dataclass(frozen=True, slots=True)
class CardSelectionResult:
    status: CardSelectionStatus
    card: StudyCard | None = None

    def __post_init__(self) -> None:
        if self.status is CardSelectionStatus.READY and self.card is None:
            raise ValueError("ready_selection_requires_card")
        if self.status is not CardSelectionStatus.READY and self.card is not None:
            raise ValueError("empty_selection_must_not_include_card")

    @classmethod
    def ready(cls, card: StudyCard) -> CardSelectionResult:
        return cls(status=CardSelectionStatus.READY, card=card)

    @classmethod
    def empty(cls, status: CardSelectionStatus) -> CardSelectionResult:
        if status is CardSelectionStatus.READY:
            raise ValueError("empty_selection_cannot_be_ready")
        return cls(status=status)


@dataclass(frozen=True, slots=True)
class MediaRequest:
    card_id: str
    media_id: str

    def __post_init__(self) -> None:
        _require_text(self.card_id, "card_id")
        _require_text(self.media_id, "media_id")


@dataclass(frozen=True, slots=True)
class MediaArtifact:
    reference: MediaReference
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("media_content_must_not_be_empty")


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    operation_id: str
    card_id: str
    selection_token: str = field(repr=False)
    rating: ReviewRating
    confirmation_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "card_id",
            "selection_token",
            "confirmation_id",
        ):
            _require_text(str(getattr(self, field_name)), field_name)


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    operation_id: str
    card_id: str
    status: ReviewStatus

    def __post_init__(self) -> None:
        _require_text(self.operation_id, "operation_id")
        _require_text(self.card_id, "card_id")


@dataclass(frozen=True, slots=True)
class AudioInput:
    content: bytes = field(repr=False)
    media_type: str = "audio/wav"
    sample_rate_hz: int = 24_000
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("audio_content_must_not_be_empty")
        if not self.media_type.lower().startswith("audio/"):
            raise ValueError("audio_media_type_must_start_with_audio")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz_must_be_positive")
        if self.duration_ms < 0:
            raise ValueError("duration_ms_must_be_non_negative")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    turn_id: str
    audio: AudioInput
    language_hints: tuple[LanguageTag, ...]
    vocabulary: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        if not self.language_hints or len(set(self.language_hints)) != len(
            self.language_hints
        ):
            raise ValueError("language_hints_must_be_nonempty_and_unique")
        _require_unique_text(self.vocabulary, "vocabulary")


@dataclass(frozen=True, slots=True)
class Transcript:
    turn_id: str
    text: str
    languages: tuple[LanguageTag, ...]
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.text, "transcript_text")
        if not self.languages or len(set(self.languages)) != len(self.languages):
            raise ValueError("transcript_languages_must_be_nonempty_and_unique")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence_must_be_between_zero_and_one")


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    turn_id: str
    card: StudyCard
    learner_answer: str

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.learner_answer, "learner_answer")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    turn_id: str
    verdict: EvaluationVerdict
    feedback: str
    covered_concepts: tuple[str, ...] = ()
    missing_concepts: tuple[str, ...] = ()
    incorrect_concepts: tuple[str, ...] = ()
    confidence: float = 1.0
    proposed_rating: ReviewRating | None = None

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.feedback, "feedback")
        concept_groups = (
            self.covered_concepts,
            self.missing_concepts,
            self.incorrect_concepts,
        )
        for field_name, concepts in zip(
            ("covered_concepts", "missing_concepts", "incorrect_concepts"),
            concept_groups,
            strict=True,
        ):
            _require_unique_text(concepts, field_name)
        all_concepts = tuple(concept for group in concept_groups for concept in group)
        if len(set(all_concepts)) != len(all_concepts):
            raise ValueError("evaluation_concept_groups_must_be_disjoint")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence_must_be_between_zero_and_one")
        if (
            self.verdict is EvaluationVerdict.UNGRADABLE
            and self.proposed_rating is not None
        ):
            raise ValueError("ungradable_result_cannot_propose_rating")


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    language: LanguageTag
    text: str

    def __post_init__(self) -> None:
        _require_text(self.text, "speech_segment_text")


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    utterance_id: str
    purpose: SpeechPurpose
    segments: tuple[SpeechSegment, ...]

    def __post_init__(self) -> None:
        _require_text(self.utterance_id, "utterance_id")
        if not self.segments:
            raise ValueError("speech_request_requires_segments")


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    utterance_id: str
    content: bytes = field(repr=False)
    media_type: str = "audio/wav"
    duration_ms: int = 0

    def __post_init__(self) -> None:
        _require_text(self.utterance_id, "utterance_id")
        if not self.content:
            raise ValueError("speech_audio_content_must_not_be_empty")
        if not self.media_type.lower().startswith("audio/"):
            raise ValueError("speech_audio_media_type_must_start_with_audio")
        if self.duration_ms < 0:
            raise ValueError("duration_ms_must_be_non_negative")


@dataclass(frozen=True, slots=True)
class SyncRequest:
    operation_id: str
    session_id: str
    boundary: SyncBoundary

    def __post_init__(self) -> None:
        _require_text(self.operation_id, "operation_id")
        _require_text(self.session_id, "session_id")


@dataclass(frozen=True, slots=True)
class SyncResult:
    operation_id: str
    status: SyncStatus
    changed_items: int = 0
    message: str = ""
    retry_safe: bool = False

    def __post_init__(self) -> None:
        _require_text(self.operation_id, "operation_id")
        if self.changed_items < 0:
            raise ValueError("changed_items_must_be_non_negative")
        if self.status is not SyncStatus.CONFIRMED and not self.message.strip():
            raise ValueError("nonconfirmed_sync_requires_message")
        if self.status is SyncStatus.UNKNOWN and self.retry_safe:
            raise ValueError("unknown_sync_outcome_cannot_be_retry_safe")


@runtime_checkable
class CardAdapter(Protocol):
    async def select_next(
        self,
        request: CardSelectionRequest,
    ) -> CardSelectionResult: ...

    async def read_media(self, request: MediaRequest) -> MediaArtifact: ...

    async def record_review(self, submission: ReviewSubmission) -> ReviewReceipt: ...


@runtime_checkable
class Transcriber(Protocol):
    async def transcribe(self, request: TranscriptionRequest) -> Transcript: ...


@runtime_checkable
class AnswerEvaluator(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


@runtime_checkable
class SpeechSynthesizer(Protocol):
    async def synthesize(self, request: SpeechRequest) -> SpeechAudio: ...


@runtime_checkable
class CollectionSynchronizer(Protocol):
    async def sync(self, request: SyncRequest) -> SyncResult: ...
