"""Application service for the complete credential-free demo walkthrough."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import (
    AudioInput,
    EvaluationRequest,
    EvaluationResult,
    LanguageTag,
    MediaKind,
    PresentationProfile,
    SpeechAudio,
    SpeechPurpose,
    SpeechRequest,
    SpeechSegment,
    SyncBoundary,
    SyncRequest,
    SyncResult,
    TranscriptionRequest,
)
from .deck import MediaAsset, SyntheticDeck, build_synthetic_deck
from .providers import (
    DeterministicEvaluator,
    DeterministicSpeechSynthesizer,
    DeterministicSynchronizer,
    DeterministicTranscriber,
)


@dataclass(frozen=True, slots=True)
class PublicMedia:
    media_id: str
    kind: MediaKind
    media_type: str
    url: str
    alt_text: str
    description: str
    width: int | None
    height: int | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class PublicCard:
    """Browser-safe question projection with no answer or selection token."""

    card_id: str
    label: str
    prompt: str
    languages: tuple[LanguageTag, ...]
    terms: tuple[str, ...]
    media: tuple[PublicMedia, ...]


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    profile: PresentationProfile
    position: int
    total: int
    card: PublicCard | None
    complete: bool = False


@dataclass(frozen=True, slots=True)
class OfficialAnswerView:
    official_answer: str
    speech: SpeechAudio


_LABELS = {
    "bilingual-latency": "Terminologia bilíngue",
    "two-part-playback": "Raciocínio em duas etapas",
    "original-shape-sequence": "Percepção visual sintética",
    "original-tone-order": "Percepção sonora sintética",
}
_TERMS = {
    "bilingual-latency": ("latency · latência", "input · entrada"),
    "two-part-playback": ("producer", "local buffer"),
}


class DemoSession:
    """Orchestrate one in-memory session using only deterministic adapters."""

    def __init__(
        self,
        session_id: str,
        *,
        deck: SyntheticDeck | None = None,
        transcriber: DeterministicTranscriber | None = None,
        evaluator: DeterministicEvaluator | None = None,
        synthesizer: DeterministicSpeechSynthesizer | None = None,
        synchronizer: DeterministicSynchronizer | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id_must_not_be_blank")
        self.session_id = session_id
        self.deck = deck or build_synthetic_deck()
        self.transcriber = transcriber or DeterministicTranscriber()
        self.evaluator = evaluator or DeterministicEvaluator()
        self.synthesizer = synthesizer or DeterministicSpeechSynthesizer()
        self.synchronizer = synchronizer or DeterministicSynchronizer()
        self._profile: PresentationProfile | None = None
        self._evaluation: EvaluationResult | None = None
        self._speech: SpeechAudio | None = None
        self._closed = False

    @property
    def evaluation(self) -> EvaluationResult | None:
        return self._evaluation

    @property
    def closed(self) -> bool:
        return self._closed

    async def open(self, profile: PresentationProfile) -> SessionView:
        self.deck.open_session(profile)
        self._profile = profile
        await self.synchronizer.sync(
            SyncRequest(
                operation_id=f"{self.session_id}-start",
                session_id=self.session_id,
                boundary=SyncBoundary.SESSION_START,
            )
        )
        return self.view()

    async def simulate_transcription(self) -> str:
        card = self.deck.current_study_card()
        transcript = await self.transcriber.transcribe(
            TranscriptionRequest(
                turn_id=f"{self.session_id}-{card.card_id}",
                audio=AudioInput(
                    content=b"RIFF-purpose-made-offline-demo-input",
                    duration_ms=250,
                ),
                language_hints=card.languages,
                vocabulary=card.expected_concepts,
            )
        )
        return transcript.text

    async def evaluate(self, learner_answer: str) -> EvaluationResult:
        card = self.deck.current_study_card()
        result = await self.evaluator.evaluate(
            EvaluationRequest(
                turn_id=f"{self.session_id}-{card.card_id}",
                card=card,
                learner_answer=learner_answer,
            )
        )
        self.deck.record_evaluation(result)
        self._evaluation = result
        self._speech = None
        return result

    async def request_official_answer(self) -> OfficialAnswerView:
        answer = self.deck.request_official_answer()
        card = self.deck.current_study_card()
        speech = await self.synthesizer.synthesize(
            SpeechRequest(
                utterance_id=f"{self.session_id}-{card.card_id}-official-answer",
                purpose=SpeechPurpose.OFFICIAL_ANSWER,
                segments=(SpeechSegment(card.languages[0], answer),),
            )
        )
        self._speech = speech
        return OfficialAnswerView(official_answer=answer, speech=speech)

    def official_speech(self) -> SpeechAudio | None:
        return self._speech

    def advance(self) -> SessionView:
        next_card = self.deck.advance()
        self._evaluation = None
        self._speech = None
        if next_card is None:
            return SessionView(
                session_id=self.session_id,
                profile=self._required_profile(),
                position=self.deck.total,
                total=self.deck.total,
                card=None,
                complete=True,
            )
        return self.view()

    async def close(self) -> SyncResult:
        if self._closed:
            return self.deck.close_session()
        self.deck.close_session()
        result = await self.synchronizer.sync(
            SyncRequest(
                operation_id=f"{self.session_id}-end",
                session_id=self.session_id,
                boundary=SyncBoundary.SESSION_END,
            )
        )
        self._closed = True
        return result

    def view(self) -> SessionView:
        prompt = self.deck.current_card()
        return SessionView(
            session_id=self.session_id,
            profile=self._required_profile(),
            position=self.deck.position,
            total=self.deck.total,
            card=PublicCard(
                card_id=prompt.card_id,
                label=_LABELS[prompt.card_id],
                prompt=prompt.prompt,
                languages=prompt.languages,
                terms=_TERMS.get(prompt.card_id, ()),
                media=tuple(self._public_media(asset) for asset in prompt.media),
            ),
        )

    def render_media(self, media_id: str) -> tuple[str, bytes]:
        artifact = self.deck.render_media(media_id)
        return artifact.reference.media_type, artifact.content

    def _required_profile(self) -> PresentationProfile:
        if self._profile is None:
            raise RuntimeError("session_has_not_opened")
        return self._profile

    @staticmethod
    def _public_media(asset: MediaAsset) -> PublicMedia:
        return PublicMedia(
            media_id=asset.media_id,
            kind=asset.kind,
            media_type=asset.media_type,
            url=asset.public_uri,
            alt_text=asset.reference.alt_text,
            description=asset.description,
            width=asset.width,
            height=asset.height,
            duration_ms=asset.duration_ms,
        )
