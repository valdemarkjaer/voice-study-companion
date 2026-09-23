"""Deterministic offline providers for the public demonstration."""

from __future__ import annotations

import hashlib
import io
import math
import re
import struct
import unicodedata
import wave
from collections.abc import Mapping

from ..contracts import (
    AnswerEvaluator,
    CollectionSynchronizer,
    EvaluationRequest,
    EvaluationResult,
    EvaluationVerdict,
    LanguageTag,
    ReviewRating,
    SpeechAudio,
    SpeechRequest,
    SpeechSynthesizer,
    SyncRequest,
    SyncResult,
    SyncStatus,
    Transcript,
    Transcriber,
    TranscriptionRequest,
)


PT_BR = LanguageTag("pt-BR")
EN_US = LanguageTag("en-US")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))


DEFAULT_CONCEPT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "delay": ("delay", "latency", "latencia", "atraso"),
    "input-to-response": (
        "input to response",
        "input and response",
        "input e response",
        "input e a response",
        "entrada e resposta",
        "entrada ate a resposta",
    ),
    "producer-ended": ("producer ended", "producer terminou", "fim do produtor"),
    "local-buffer-drained": (
        "local buffer drained",
        "buffer local esvaziou",
        "buffer terminou",
    ),
    "triangle": ("triangle", "triangulo"),
    "green": ("green", "verde"),
    "low-first": ("low first", "grave primeiro", "baixo primeiro"),
    "high-second": ("high second", "agudo depois", "alto depois"),
}


class DeterministicTranscriber:
    """Return a configured transcript without inspecting or uploading audio."""

    def __init__(
        self,
        text: str = "Latency é o delay entre o input e a response.",
        languages: tuple[LanguageTag, ...] = (PT_BR, EN_US),
    ) -> None:
        if not text.strip():
            raise ValueError("scripted_transcript_must_not_be_blank")
        if not languages:
            raise ValueError("scripted_transcript_requires_language")
        self._text = text
        self._languages = languages
        self.requests: list[TranscriptionRequest] = []

    async def transcribe(self, request: TranscriptionRequest) -> Transcript:
        self.requests.append(request)
        return Transcript(
            turn_id=request.turn_id,
            text=self._text,
            languages=self._languages,
            confidence=1.0,
        )


class DeterministicEvaluator:
    """Grade exact synthetic concepts with transparent bilingual aliases."""

    def __init__(
        self,
        aliases: Mapping[str, tuple[str, ...]] = DEFAULT_CONCEPT_ALIASES,
    ) -> None:
        self._aliases = {
            concept: tuple(_normalize(alias) for alias in values)
            for concept, values in aliases.items()
        }
        self.requests: list[EvaluationRequest] = []

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        self.requests.append(request)
        answer = f" {_normalize(request.learner_answer)} "
        covered: list[str] = []
        missing: list[str] = []
        for concept in request.card.expected_concepts:
            aliases = self._aliases.get(
                concept,
                (_normalize(concept.replace("-", " ")),),
            )
            if any(f" {alias} " in answer for alias in aliases if alias):
                covered.append(concept)
            else:
                missing.append(concept)

        if not missing:
            verdict = EvaluationVerdict.CORRECT
            feedback = "Correto. You covered every expected idea."
            rating = ReviewRating.GOOD
        elif covered:
            verdict = EvaluationVerdict.PARTIAL
            feedback = "Parcialmente correto. One expected idea is still missing."
            rating = ReviewRating.HARD
        else:
            verdict = EvaluationVerdict.INCORRECT
            feedback = "Ainda não. The expected ideas were not identified yet."
            rating = ReviewRating.AGAIN

        return EvaluationResult(
            turn_id=request.turn_id,
            verdict=verdict,
            feedback=feedback,
            covered_concepts=tuple(covered),
            missing_concepts=tuple(missing),
            confidence=1.0,
            proposed_rating=rating,
        )


class DeterministicSpeechSynthesizer:
    """Render stable audible tones as an inspectable fake speech response."""

    sample_rate_hz = 8_000

    def __init__(self) -> None:
        self.requests: list[SpeechRequest] = []

    async def synthesize(self, request: SpeechRequest) -> SpeechAudio:
        self.requests.append(request)
        text = " ".join(segment.text for segment in request.segments)
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        frequencies = tuple(330 + (byte % 6) * 55 for byte in digest[:4])
        tone_ms = 70
        silence_ms = 25
        frames = bytearray()
        for frequency in frequencies:
            frames.extend(self._tone(frequency, tone_ms))
            frames.extend(b"\x00\x00" * (self.sample_rate_hz * silence_ms // 1_000))
        content = io.BytesIO()
        with wave.open(content, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate_hz)
            output.writeframes(bytes(frames))
        duration_ms = len(frequencies) * (tone_ms + silence_ms)
        return SpeechAudio(
            utterance_id=request.utterance_id,
            content=content.getvalue(),
            duration_ms=duration_ms,
        )

    def _tone(self, frequency_hz: int, duration_ms: int) -> bytes:
        count = self.sample_rate_hz * duration_ms // 1_000
        amplitude = 3_200
        return b"".join(
            struct.pack(
                "<h",
                round(
                    amplitude
                    * math.sin(2 * math.pi * frequency_hz * index / self.sample_rate_hz)
                ),
            )
            for index in range(count)
        )


class DeterministicSynchronizer:
    """Confirm boundaries locally and retain only in-memory request metadata."""

    def __init__(self) -> None:
        self.requests: list[SyncRequest] = []

    async def sync(self, request: SyncRequest) -> SyncResult:
        self.requests.append(request)
        return SyncResult(
            operation_id=request.operation_id,
            status=SyncStatus.CONFIRMED,
            changed_items=0,
        )


def assert_provider_contracts() -> None:
    """Fail early if an edit breaks the public runtime protocols."""

    providers = (
        (DeterministicTranscriber(), Transcriber),
        (DeterministicEvaluator(), AnswerEvaluator),
        (DeterministicSpeechSynthesizer(), SpeechSynthesizer),
        (DeterministicSynchronizer(), CollectionSynchronizer),
    )
    for provider, contract in providers:
        if not isinstance(provider, contract):
            raise TypeError(f"{type(provider).__name__} does not satisfy {contract.__name__}")
