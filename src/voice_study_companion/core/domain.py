"""Provider-neutral value objects for the streamed voice-study pipeline.

The public demo already exposes small adapter contracts in :mod:`contracts`.
This module adds the generation identity and streaming metadata needed by the
conversation core while deliberately reusing the public language, speech,
evaluation, and review-rating types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..contracts import (
    EvaluationVerdict,
    LanguageTag,
    ReviewRating,
    SpeechSegment,
)


# These aliases make the richer pipeline vocabulary explicit without creating
# a second set of values that could drift from the public adapter contracts.
GradeVerdict = EvaluationVerdict


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name}_must_not_be_blank")


def _require_non_negative(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name}_must_be_non_negative")


def _require_unique_text(values: tuple[str, ...], field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name}_must_not_contain_blank_values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name}_must_be_unique")


class Operation(StrEnum):
    TRANSCRIPTION = "transcription"
    DIALOGUE = "dialogue"
    GRADING = "grading"
    SYNTHESIS = "synthesis"
    TURN_DETECTION = "turn_detection"


class Capability(StrEnum):
    STREAMING_INPUT = "streaming_input"
    STREAMING_OUTPUT = "streaming_output"
    STRUCTURED_OUTPUT = "structured_output"
    CANCELLATION = "cancellation"
    KEYWORD_HINTS = "keyword_hints"
    MULTILINGUAL = "multilingual"


class AudioEncoding(StrEnum):
    PCM_S16LE = "pcm_s16le"
    WAV = "wav"


class DialogueIntent(StrEnum):
    ANSWER = "answer"
    CLARIFICATION = "clarification"
    REPEAT = "repeat"
    CONTROL = "control"
    DISCUSSION = "discussion"


class UtteranceKind(StrEnum):
    CARD_PROMPT = "card_prompt"
    CLARIFICATION = "clarification"
    FEEDBACK = "feedback"
    OFFICIAL_ANSWER = "official_answer"
    DISCUSSION = "discussion"
    SYSTEM = "system"


class UtterancePolicy(StrEnum):
    """Microphone policy owned by the server for one spoken utterance."""

    LOCKED = "locked"
    OPEN_BARGE_IN = "open_barge_in"


class ErrorCode(StrEnum):
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    BUDGET_EXCEEDED = "budget_exceeded"
    STALE_GENERATION = "stale_generation"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    """Identity shared by every asynchronous result from one generation."""

    session_id: str
    card_id: str
    turn_id: str
    generation_id: int
    sequence: int

    def __post_init__(self) -> None:
        _require_text(self.session_id, "session_id")
        _require_text(self.card_id, "card_id")
        _require_text(self.turn_id, "turn_id")
        if self.generation_id < 1:
            raise ValueError("generation_id_must_be_positive")
        _require_non_negative(self.sequence, "sequence")

    def same_generation(self, other: TurnIdentity) -> bool:
        return (
            self.session_id,
            self.card_id,
            self.turn_id,
            self.generation_id,
        ) == (
            other.session_id,
            other.card_id,
            other.turn_id,
            other.generation_id,
        )


@dataclass(frozen=True, slots=True)
class AudioFrame:
    identity: TurnIdentity
    data: bytes = field(repr=False)
    sample_rate_hz: int = 24_000
    channels: int = 1
    encoding: AudioEncoding = AudioEncoding.PCM_S16LE
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("audio_frame_data_must_not_be_empty")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz_must_be_positive")
        if self.channels not in {1, 2}:
            raise ValueError("audio_channels_must_be_one_or_two")
        _require_non_negative(self.duration_ms, "duration_ms")


@dataclass(frozen=True, slots=True)
class AudioChunk:
    identity: TurnIdentity
    chunk_index: int
    data: bytes = field(repr=False)
    sample_rate_hz: int = 24_000
    channels: int = 1
    encoding: AudioEncoding = AudioEncoding.PCM_S16LE
    final: bool = False

    def __post_init__(self) -> None:
        _require_non_negative(self.chunk_index, "chunk_index")
        if not self.data:
            raise ValueError("audio_chunk_data_must_not_be_empty")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz_must_be_positive")
        if self.channels not in {1, 2}:
            raise ValueError("audio_channels_must_be_one_or_two")


@dataclass(frozen=True, slots=True)
class SpeechHints:
    languages: tuple[LanguageTag, ...]
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.languages or len(self.languages) != len(set(self.languages)):
            raise ValueError("speech_hints_require_unique_languages")
        _require_unique_text(self.keywords, "speech_hint_keywords")


@dataclass(frozen=True, slots=True)
class Transcript:
    identity: TurnIdentity
    raw_text: str
    normalized_text: str
    detected_languages: tuple[LanguageTag, ...]
    confidence: float | None
    final: bool

    def __post_init__(self) -> None:
        _require_text(self.raw_text, "raw_text")
        _require_text(self.normalized_text, "normalized_text")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence_must_be_between_zero_and_one")


@dataclass(frozen=True, slots=True)
class DialoguePlan:
    identity: TurnIdentity
    intent: DialogueIntent
    display_text: str
    speech_segments: tuple[SpeechSegment, ...]
    requires_grading: bool = False
    supplemental: bool = False

    def __post_init__(self) -> None:
        _require_text(self.display_text, "dialogue_display_text")
        if not self.speech_segments:
            raise ValueError("dialogue_plan_requires_speech_segments")
        if self.requires_grading and self.intent is not DialogueIntent.ANSWER:
            raise ValueError("only_answer_intent_can_require_grading")


@dataclass(frozen=True, slots=True)
class GradeResult:
    identity: TurnIdentity
    verdict: GradeVerdict
    covered_concepts: tuple[str, ...]
    missing_concepts: tuple[str, ...]
    incorrect_concepts: tuple[str, ...]
    confidence: float
    explanation: str
    proposed_rating: ReviewRating | None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence_must_be_between_zero_and_one")
        _require_text(self.explanation, "grade_explanation")
        groups = (
            ("covered_concepts", self.covered_concepts),
            ("missing_concepts", self.missing_concepts),
            ("incorrect_concepts", self.incorrect_concepts),
        )
        for name, values in groups:
            _require_unique_text(values, name)
        flattened = tuple(value for _, values in groups for value in values)
        if len(flattened) != len(set(flattened)):
            raise ValueError("grade_concept_groups_must_be_disjoint")
        if (
            self.verdict is GradeVerdict.UNGRADABLE
            and self.proposed_rating is not None
        ):
            raise ValueError("ungradable_result_cannot_propose_rating")


@dataclass(frozen=True, slots=True)
class AuthorizedUtterance:
    utterance_id: str
    identity: TurnIdentity
    kind: UtteranceKind
    segments: tuple[SpeechSegment, ...]
    created_at: datetime
    expires_at: datetime
    policy: UtterancePolicy = UtterancePolicy.LOCKED

    def __post_init__(self) -> None:
        _require_text(self.utterance_id, "utterance_id")
        if not self.segments:
            raise ValueError("authorized_utterance_requires_segments")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("utterance_timestamps_must_be_timezone_aware")
        if self.expires_at.astimezone(UTC) <= self.created_at.astimezone(UTC):
            raise ValueError("utterance_expiry_must_follow_creation")


@dataclass(frozen=True, slots=True)
class UsageAmounts:
    input_tokens: int = 0
    output_tokens: int = 0
    audio_input_ms: int = 0
    audio_output_ms: int = 0
    characters: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "audio_input_ms",
            "audio_output_ms",
            "characters",
        ):
            _require_non_negative(int(getattr(self, name)), name)

    @property
    def is_zero(self) -> bool:
        return not any(
            (
                self.input_tokens,
                self.output_tokens,
                self.audio_input_ms,
                self.audio_output_ms,
                self.characters,
            )
        )


@dataclass(frozen=True, slots=True)
class Usage:
    identity: TurnIdentity
    operation: Operation
    route_id: str
    provider: str
    model: str
    amounts: UsageAmounts
    latency_ms: int
    price_book_version: str
    estimated_microusd: int
    reported: bool

    def __post_init__(self) -> None:
        for name in ("route_id", "provider", "model", "price_book_version"):
            _require_text(str(getattr(self, name)), name)
        _require_non_negative(self.latency_ms, "latency_ms")
        _require_non_negative(self.estimated_microusd, "estimated_microusd")


@dataclass(frozen=True, slots=True)
class PipelineError:
    identity: TurnIdentity | None
    code: ErrorCode
    operation: Operation | None
    public_message: str
    retry_safe: bool
    uncertain_outcome: bool
    provider_code: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.public_message, "public_message")
        if self.uncertain_outcome and self.retry_safe:
            raise ValueError("uncertain_outcome_cannot_be_retry_safe")


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    operations: frozenset[Operation]
    features: frozenset[Capability]
    languages: frozenset[LanguageTag]
    audio_encodings: frozenset[AudioEncoding] = frozenset()

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("provider_requires_an_operation")
        if not self.languages:
            raise ValueError("provider_requires_a_language")

    def supports(
        self,
        operation: Operation,
        *,
        features: frozenset[Capability] = frozenset(),
        languages: frozenset[LanguageTag] = frozenset(),
        audio_encoding: AudioEncoding | None = None,
    ) -> bool:
        if operation not in self.operations:
            return False
        if not features.issubset(self.features):
            return False
        if not languages.issubset(self.languages):
            return False
        return audio_encoding is None or audio_encoding in self.audio_encodings
