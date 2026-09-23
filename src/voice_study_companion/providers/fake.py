"""Deterministic providers for contract and credential-free integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from ..contracts import ReviewRating
from ..core.domain import (
    AudioChunk,
    AudioEncoding,
    AudioFrame,
    AuthorizedUtterance,
    Capability,
    DialogueIntent,
    DialoguePlan,
    GradeResult,
    GradeVerdict,
    LanguageTag,
    Operation,
    ProviderCapabilities,
    SpeechHints,
    SpeechSegment,
    Transcript,
    TurnIdentity,
    Usage,
    UsageAmounts,
)
from ..core.ports import CancellationSignal, ProviderPayload, TranscriptionSession

_LANGUAGES = frozenset({LanguageTag("pt-BR"), LanguageTag("en-US")})


def _capabilities(
    operation: Operation,
    *features: Capability,
    audio: bool = False,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        operations=frozenset({operation}),
        features=frozenset(features),
        languages=_LANGUAGES,
        audio_encodings=(
            frozenset({AudioEncoding.PCM_S16LE}) if audio else frozenset()
        ),
    )


def _usage(identity: TurnIdentity, operation: Operation, model: str) -> Usage:
    return Usage(
        identity=identity,
        operation=operation,
        route_id=f"fake-{operation.value}",
        provider="fake",
        model=model,
        amounts=UsageAmounts(),
        latency_ms=0,
        price_book_version="fake-v1",
        estimated_microusd=0,
        reported=True,
    )


class FakeTranscriber:
    def __init__(
        self,
        transcript: str = "producer ended and local buffer drained",
        *,
        detected_languages: tuple[LanguageTag, ...] = (
            LanguageTag("pt-BR"),
            LanguageTag("en-US"),
        ),
    ) -> None:
        if not transcript.strip():
            raise ValueError("fake_transcript_must_not_be_blank")
        self._transcript = transcript
        self._detected_languages = detected_languages

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _capabilities(
            Operation.TRANSCRIPTION,
            Capability.STREAMING_INPUT,
            Capability.CANCELLATION,
            Capability.KEYWORD_HINTS,
            Capability.MULTILINGUAL,
            audio=True,
        )

    async def open(
        self,
        identity: TurnIdentity,
        hints: SpeechHints,
        cancellation: CancellationSignal,
    ) -> TranscriptionSession:
        cancellation.raise_if_cancelled()
        return _FakeTranscriptionSession(
            identity=identity,
            hints=hints,
            transcript=self._transcript,
            detected_languages=self._detected_languages,
            cancellation=cancellation,
        )


class _FakeTranscriptionSession:
    def __init__(
        self,
        *,
        identity: TurnIdentity,
        hints: SpeechHints,
        transcript: str,
        detected_languages: tuple[LanguageTag, ...],
        cancellation: CancellationSignal,
    ) -> None:
        self._identity = identity
        self._hints = hints
        self._transcript = transcript
        self._detected_languages = detected_languages
        self._cancellation = cancellation
        self._closed = False
        self._committed = False
        self._frame_count = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _capabilities(
            Operation.TRANSCRIPTION,
            Capability.STREAMING_INPUT,
            Capability.CANCELLATION,
            Capability.KEYWORD_HINTS,
            Capability.MULTILINGUAL,
            audio=True,
        )

    async def push(self, frame: AudioFrame) -> None:
        self._ensure_active()
        self._cancellation.raise_if_cancelled()
        if not frame.identity.same_generation(self._identity):
            raise ValueError("audio_frame_generation_does_not_match_session")
        self._frame_count += 1
        await asyncio.sleep(0)

    async def update_hints(self, hints: SpeechHints) -> None:
        self._ensure_active()
        self._cancellation.raise_if_cancelled()
        self._hints = hints
        await asyncio.sleep(0)

    async def commit(self, cancellation: CancellationSignal) -> Transcript:
        self._ensure_active()
        cancellation.raise_if_cancelled()
        if self._committed:
            raise ValueError("transcription_session_already_committed")
        if self._frame_count < 1:
            raise ValueError("transcription_session_requires_audio")
        self._committed = True
        await asyncio.sleep(0)
        cancellation.raise_if_cancelled()
        return Transcript(
            identity=self._identity,
            raw_text=self._transcript,
            normalized_text=" ".join(self._transcript.split()),
            detected_languages=self._detected_languages or self._hints.languages,
            confidence=1.0,
            final=True,
        )

    async def close(self) -> None:
        self._closed = True
        await asyncio.sleep(0)

    def _ensure_active(self) -> None:
        if self._closed:
            raise ValueError("transcription_session_is_closed")


class FakeDialogueModel:
    def __init__(
        self,
        *,
        intent: DialogueIntent = DialogueIntent.ANSWER,
        display_text: str = "Resposta recebida.",
        speech_segments: tuple[SpeechSegment, ...] = (
            SpeechSegment(LanguageTag("pt-BR"), "Resposta recebida."),
        ),
        supplemental: bool = False,
    ) -> None:
        self._intent = intent
        self._display_text = display_text
        self._speech_segments = speech_segments
        self._supplemental = supplemental

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _capabilities(
            Operation.DIALOGUE,
            Capability.STRUCTURED_OUTPUT,
            Capability.CANCELLATION,
            Capability.MULTILINGUAL,
        )

    async def respond(
        self,
        context: ProviderPayload,
        transcript: Transcript,
        cancellation: CancellationSignal,
    ) -> tuple[DialoguePlan, Usage]:
        cancellation.raise_if_cancelled()
        if not context.identity.same_generation(transcript.identity):
            raise ValueError("context_and_transcript_generation_mismatch")
        context.to_provider_payload()
        await asyncio.sleep(0)
        cancellation.raise_if_cancelled()
        plan = DialoguePlan(
            identity=transcript.identity,
            intent=self._intent,
            display_text=self._display_text,
            speech_segments=self._speech_segments,
            requires_grading=self._intent is DialogueIntent.ANSWER,
            supplemental=self._supplemental,
        )
        return plan, _usage(transcript.identity, Operation.DIALOGUE, "fake-dialogue")


class FakeAnswerGrader:
    def __init__(
        self,
        *,
        verdict: GradeVerdict = GradeVerdict.CORRECT,
        covered_concepts: tuple[str, ...] = ("producer-ended", "local-buffer-drained"),
        missing_concepts: tuple[str, ...] = (),
        incorrect_concepts: tuple[str, ...] = (),
        confidence: float = 1.0,
        explanation: str = "The expected concept was covered.",
        proposed_rating: ReviewRating | None = ReviewRating.GOOD,
    ) -> None:
        self._verdict = verdict
        self._covered = covered_concepts
        self._missing = missing_concepts
        self._incorrect = incorrect_concepts
        self._confidence = confidence
        self._explanation = explanation
        self._proposed_rating = proposed_rating

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _capabilities(
            Operation.GRADING,
            Capability.STRUCTURED_OUTPUT,
            Capability.CANCELLATION,
            Capability.MULTILINGUAL,
        )

    async def grade(
        self,
        context: ProviderPayload,
        transcript: Transcript,
        cancellation: CancellationSignal,
    ) -> tuple[GradeResult, Usage]:
        cancellation.raise_if_cancelled()
        if not context.identity.same_generation(transcript.identity):
            raise ValueError("context_and_transcript_generation_mismatch")
        context.to_provider_payload()
        await asyncio.sleep(0)
        cancellation.raise_if_cancelled()
        result = GradeResult(
            identity=transcript.identity,
            verdict=self._verdict,
            covered_concepts=self._covered,
            missing_concepts=self._missing,
            incorrect_concepts=self._incorrect,
            confidence=self._confidence,
            explanation=self._explanation,
            proposed_rating=self._proposed_rating,
        )
        return result, _usage(transcript.identity, Operation.GRADING, "fake-grader")


class FakeSynthesizer:
    def __init__(self, *, chunk_bytes: int = 64) -> None:
        if chunk_bytes < 2 or chunk_bytes % 2:
            raise ValueError("fake_tts_chunk_bytes_must_be_positive_and_even")
        self._chunk_bytes = chunk_bytes

    @property
    def capabilities(self) -> ProviderCapabilities:
        return _capabilities(
            Operation.SYNTHESIS,
            Capability.STREAMING_OUTPUT,
            Capability.CANCELLATION,
            Capability.MULTILINGUAL,
            audio=True,
        )

    def stream(
        self,
        utterance: AuthorizedUtterance,
        cancellation: CancellationSignal,
    ) -> AsyncIterator[AudioChunk]:
        return self._stream(utterance, cancellation)

    async def _stream(
        self,
        utterance: AuthorizedUtterance,
        cancellation: CancellationSignal,
    ) -> AsyncIterator[AudioChunk]:
        cancellation.raise_if_cancelled()
        if utterance.expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("authorized_utterance_has_expired")
        encoded = " ".join(segment.text for segment in utterance.segments).encode()
        data = b"".join(bytes((value, 0)) for value in encoded)
        pieces = tuple(
            data[index : index + self._chunk_bytes]
            for index in range(0, len(data), self._chunk_bytes)
        )
        for index, piece in enumerate(pieces):
            cancellation.raise_if_cancelled()
            await asyncio.sleep(0)
            cancellation.raise_if_cancelled()
            yield AudioChunk(
                identity=utterance.identity,
                chunk_index=index,
                data=piece,
                sample_rate_hz=24_000,
                channels=1,
                encoding=AudioEncoding.PCM_S16LE,
                final=index == len(pieces) - 1,
            )
