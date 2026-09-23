"""Structural ports for fake or user-supplied voice providers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .domain import (
    AudioChunk,
    AudioFrame,
    AuthorizedUtterance,
    DialoguePlan,
    GradeResult,
    PipelineError,
    ProviderCapabilities,
    SpeechHints,
    Transcript,
    TurnIdentity,
    Usage,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...

    async def wait(self) -> None: ...

    def raise_if_cancelled(self) -> None: ...


class ProviderPayload(Protocol):
    """A context view that owns its deliberately bounded provider projection."""

    @property
    def identity(self) -> TurnIdentity: ...

    def to_provider_payload(self) -> Mapping[str, JsonValue]: ...


class TranscriptionSession(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def push(self, frame: AudioFrame) -> None: ...

    async def update_hints(self, hints: SpeechHints) -> None: ...

    async def commit(self, cancellation: CancellationSignal) -> Transcript: ...

    async def close(self) -> None: ...


@runtime_checkable
class Transcriber(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def open(
        self,
        identity: TurnIdentity,
        hints: SpeechHints,
        cancellation: CancellationSignal,
    ) -> TranscriptionSession: ...


@runtime_checkable
class DialogueModel(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def respond(
        self,
        context: ProviderPayload,
        transcript: Transcript,
        cancellation: CancellationSignal,
    ) -> tuple[DialoguePlan, Usage]: ...


@runtime_checkable
class AnswerGrader(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def grade(
        self,
        context: ProviderPayload,
        transcript: Transcript,
        cancellation: CancellationSignal,
    ) -> tuple[GradeResult, Usage]: ...


@runtime_checkable
class Synthesizer(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def stream(
        self,
        utterance: AuthorizedUtterance,
        cancellation: CancellationSignal,
    ) -> AsyncIterator[AudioChunk]: ...


class TurnSignalKind(StrEnum):
    SPEECH_STARTED = "speech_started"
    SPEECH_STOPPED = "speech_stopped"
    PLAYBACK_LEAKAGE = "playback_leakage"


class TurnDetector(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def observe(self, frame: AudioFrame) -> tuple[TurnSignalKind, ...]: ...


class VoiceTransport(Protocol):
    async def send_audio(self, chunk: AudioChunk) -> None: ...

    async def send_event(self, event: Mapping[str, JsonValue]) -> None: ...

    async def receive_audio(self) -> AudioFrame: ...

    async def close(self, error: PipelineError | None = None) -> None: ...
