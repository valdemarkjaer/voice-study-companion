"""Purpose-made synthetic cards for the credential-free demonstration.

All text and media recipes in this module were authored for this public demo.
Nothing is loaded from a user collection, transcript, private service, or
third-party study source.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..contracts import (
    CardSide,
    EvaluationResult,
    EvaluationVerdict,
    LanguageTag,
    MediaArtifact,
    MediaKind,
    MediaReference,
    PresentationProfile,
    ReviewRating,
    StudyCard,
    SyncResult,
    SyncStatus,
)


FIXTURE_ORIGIN: Final = "purpose-made voice-study-companion synthetic fixture"
FIXTURE_LICENSE: Final = "Apache-2.0"
PT_BR: Final = LanguageTag("pt-BR")
EN_US: Final = LanguageTag("en-US")


class StudyPhase(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    CLOSED = "closed"


class DeckStateError(RuntimeError):
    """Raised when an action violates the deterministic study lifecycle."""


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """A public media reference plus its transparent generation recipe."""

    reference: MediaReference
    public_uri: str
    description: str
    recipe: str
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    origin: str = FIXTURE_ORIGIN
    license: str = FIXTURE_LICENSE

    @property
    def media_id(self) -> str:
        return self.reference.media_id

    @property
    def kind(self) -> MediaKind:
        return self.reference.kind

    @property
    def media_type(self) -> str:
        return self.reference.media_type

    def render(self) -> MediaArtifact:
        if self.recipe == "svg-three-shape-sequence-v1":
            content = _render_shape_sequence_svg()
        elif self.recipe == "pcm-square-low-high-v1":
            content = _render_low_high_wav()
        else:
            raise ValueError(f"unknown synthetic media recipe: {self.recipe}")
        return MediaArtifact(reference=self.reference, content=content)


@dataclass(frozen=True, slots=True)
class CardPrompt:
    """Question-only projection; the official answer cannot leak through it."""

    card_id: str
    prompt: str
    languages: tuple[LanguageTag, ...]
    media: tuple[MediaAsset, ...]
    provenance: str = FIXTURE_ORIGIN

    @property
    def is_text_only(self) -> bool:
        return not self.media


@dataclass(frozen=True, slots=True)
class _Fixture:
    card: StudyCard
    media: tuple[MediaAsset, ...] = ()

    def question(self) -> CardPrompt:
        return CardPrompt(
            card_id=self.card.card_id,
            prompt=self.card.prompt,
            languages=self.card.languages,
            media=self.media,
        )


_IMAGE_REFERENCE = MediaReference(
    media_id="shape-sequence-v1",
    side=CardSide.QUESTION,
    kind=MediaKind.IMAGE,
    media_type="image/svg+xml",
    alt_text=(
        "An original diagram with a blue square, green triangle, and orange "
        "circle from left to right."
    ),
)
_AUDIO_REFERENCE = MediaReference(
    media_id="low-high-tone-v1",
    side=CardSide.QUESTION,
    kind=MediaKind.AUDIO,
    media_type="audio/wav",
)
_IMAGE = MediaAsset(
    reference=_IMAGE_REFERENCE,
    public_uri="/api/media/shape-sequence-v1",
    description=_IMAGE_REFERENCE.alt_text,
    recipe="svg-three-shape-sequence-v1",
    width=480,
    height=180,
)
_AUDIO = MediaAsset(
    reference=_AUDIO_REFERENCE,
    public_uri="/api/media/low-high-tone-v1",
    description="An original cue with a low tone followed by a higher tone.",
    recipe="pcm-square-low-high-v1",
    duration_ms=450,
)


def _card(
    card_id: str,
    prompt: str,
    answer: str,
    concepts: tuple[str, ...],
    languages: tuple[LanguageTag, ...],
    media: tuple[MediaAsset, ...] = (),
) -> _Fixture:
    return _Fixture(
        card=StudyCard(
            card_id=card_id,
            scope="synthetic-public-demo",
            prompt=prompt,
            official_answer=answer,
            expected_concepts=concepts,
            selection_token=f"synthetic-selection-{card_id}",
            languages=languages,
            media=tuple(asset.reference for asset in media),
            tags=("synthetic", "public-demo"),
        ),
        media=media,
    )


_FIXTURES: Final = (
    _card(
        "bilingual-latency",
        (
            "Em uma conversa por voz, what does ‘latency’ mean? "
            "Responda em português, English, or both."
        ),
        (
            "Latency, ou latência, is the delay between an input and the "
            "corresponding response."
        ),
        ("delay", "input-to-response"),
        (PT_BR, EN_US),
    ),
    _card(
        "two-part-playback",
        (
            "Name the two independent signals this demo uses to consider "
            "protected audio complete."
        ),
        (
            "The producer must report that it ended, and the local player "
            "must report that its buffered audio drained."
        ),
        ("producer-ended", "local-buffer-drained"),
        (EN_US,),
    ),
    _card(
        "original-shape-sequence",
        "Which shape appears between the square and the circle?",
        "The green triangle appears in the middle.",
        ("triangle", "green"),
        (EN_US,),
        (_IMAGE,),
    ),
    _card(
        "original-tone-order",
        "Does the purpose-made cue move from low to high or high to low?",
        "It moves from a low tone to a higher tone.",
        ("low-first", "high-second"),
        (EN_US,),
        (_AUDIO,),
    ),
)


class SyntheticDeck:
    """A fresh, deterministic single-session deck with explicit boundaries."""

    def __init__(self, fixtures: tuple[_Fixture, ...] = _FIXTURES) -> None:
        if not fixtures:
            raise ValueError("synthetic_deck_requires_fixture")
        ids = tuple(fixture.card.card_id for fixture in fixtures)
        if len(ids) != len(set(ids)):
            raise ValueError("synthetic_fixture_ids_must_be_unique")
        self._fixtures = fixtures
        self._phase = StudyPhase.IDLE
        self._profile: PresentationProfile | None = None
        self._eligible: tuple[_Fixture, ...] = ()
        self._index = 0
        self._evaluation: EvaluationResult | None = None
        self._completed: list[str] = []
        self._sync_results: list[SyncResult] = []

    @property
    def phase(self) -> StudyPhase:
        return self._phase

    @property
    def profile(self) -> PresentationProfile | None:
        return self._profile

    @property
    def completed_card_ids(self) -> tuple[str, ...]:
        return tuple(self._completed)

    @property
    def sync_results(self) -> tuple[SyncResult, ...]:
        return tuple(self._sync_results)

    @property
    def position(self) -> int:
        self._require_active()
        return self._index + 1

    @property
    def total(self) -> int:
        return len(self._eligible) if self._eligible else len(self._fixtures)

    def all_prompts(self) -> tuple[CardPrompt, ...]:
        return tuple(fixture.question() for fixture in self._fixtures)

    def all_study_cards(self) -> tuple[StudyCard, ...]:
        return tuple(fixture.card for fixture in self._fixtures)

    def media_assets(self) -> tuple[MediaAsset, ...]:
        return tuple(asset for fixture in self._fixtures for asset in fixture.media)

    def open_session(
        self,
        profile: PresentationProfile = PresentationProfile.FULL,
    ) -> CardPrompt:
        if self._phase is not StudyPhase.IDLE:
            raise DeckStateError("session_can_only_open_from_idle")
        eligible = tuple(
            fixture
            for fixture in self._fixtures
            if profile is PresentationProfile.FULL or not fixture.media
        )
        if not eligible:
            raise DeckStateError("selected_profile_has_no_eligible_card")
        self._profile = profile
        self._eligible = eligible
        self._phase = StudyPhase.ACTIVE
        self._sync_results.append(
            SyncResult(operation_id="opening-sync", status=SyncStatus.CONFIRMED)
        )
        return self.current_card()

    def current_card(self) -> CardPrompt:
        return self._current_fixture().question()

    def current_study_card(self) -> StudyCard:
        return self._current_fixture().card

    def evaluate_concepts(
        self,
        concepts: tuple[str, ...],
        *,
        turn_id: str = "synthetic-turn",
    ) -> EvaluationResult:
        fixture = self._current_fixture()
        supplied = tuple(dict.fromkeys(value.strip() for value in concepts if value.strip()))
        supplied_set = set(supplied)
        expected = fixture.card.expected_concepts
        covered = tuple(value for value in expected if value in supplied_set)
        missing = tuple(value for value in expected if value not in supplied_set)
        incorrect = tuple(value for value in supplied if value not in set(expected))
        if not missing and not incorrect:
            verdict = EvaluationVerdict.CORRECT
            rating = ReviewRating.GOOD
            feedback = "All expected synthetic concepts were covered."
        elif covered and not incorrect:
            verdict = EvaluationVerdict.PARTIAL
            rating = ReviewRating.HARD
            feedback = "Some expected synthetic concepts are still missing."
        else:
            verdict = EvaluationVerdict.INCORRECT
            rating = ReviewRating.AGAIN
            feedback = "The expected synthetic concepts were not yet covered."
        result = EvaluationResult(
            turn_id=turn_id,
            verdict=verdict,
            feedback=feedback,
            covered_concepts=covered,
            missing_concepts=missing,
            incorrect_concepts=incorrect,
            proposed_rating=rating,
        )
        self._evaluation = result
        return result

    def record_evaluation(self, result: EvaluationResult) -> None:
        expected = set(self.current_study_card().expected_concepts)
        reported = set(result.covered_concepts) | set(result.missing_concepts)
        if reported != expected:
            raise DeckStateError("evaluation_does_not_match_current_card")
        self._evaluation = result

    def request_official_answer(self) -> str:
        if self._evaluation is None:
            raise DeckStateError("official_answer_requires_evaluation")
        return self.current_study_card().official_answer

    def advance(self) -> CardPrompt | None:
        fixture = self._current_fixture()
        if self._evaluation is None:
            raise DeckStateError("advance_requires_evaluation")
        self._completed.append(fixture.card.card_id)
        self._index += 1
        self._evaluation = None
        if self._index >= len(self._eligible):
            return None
        return self.current_card()

    def close_session(self) -> SyncResult:
        if self._phase is StudyPhase.IDLE:
            raise DeckStateError("session_has_not_opened")
        if self._phase is StudyPhase.CLOSED:
            return self._sync_results[-1]
        result = SyncResult(
            operation_id="closing-sync",
            status=SyncStatus.CONFIRMED,
            changed_items=len(self._completed),
        )
        self._sync_results.append(result)
        self._phase = StudyPhase.CLOSED
        return result

    def render_media(self, media_id: str) -> MediaArtifact:
        for asset in self.media_assets():
            if asset.media_id == media_id:
                return asset.render()
        raise KeyError(media_id)

    def _require_active(self) -> None:
        if self._phase is not StudyPhase.ACTIVE:
            raise DeckStateError("no_active_synthetic_session")

    def _current_fixture(self) -> _Fixture:
        self._require_active()
        if self._index >= len(self._eligible):
            raise DeckStateError("synthetic_deck_is_exhausted")
        return self._eligible[self._index]


def build_synthetic_deck() -> SyntheticDeck:
    return SyntheticDeck()


def _render_shape_sequence_svg() -> bytes:
    return b"""<svg xmlns="http://www.w3.org/2000/svg" width="480" height="180"
  viewBox="0 0 480 180" role="img" aria-labelledby="title description">
  <title id="title">Three-shape sequence</title>
  <desc id="description">A blue square, a green triangle, and an orange circle.</desc>
  <rect width="480" height="180" fill="#f8fafc"/>
  <rect x="50" y="55" width="70" height="70" rx="8" fill="#2563eb"/>
  <polygon points="240,45 195,125 285,125" fill="#15803d"/>
  <circle cx="390" cy="90" r="42" fill="#c2410c"/>
</svg>
"""


def _render_low_high_wav() -> bytes:
    sample_rate = 8_000
    amplitude = 4_096

    def square_tone(frequency: int, duration_ms: int) -> bytes:
        sample_count = sample_rate * duration_ms // 1_000
        half_period = sample_rate // frequency // 2
        return b"".join(
            struct.pack(
                "<h",
                amplitude if (index // half_period) % 2 == 0 else -amplitude,
            )
            for index in range(sample_count)
        )

    silence = b"\x00\x00" * (sample_rate * 50 // 1_000)
    frames = square_tone(400, 200) + silence + square_tone(800, 200)
    output = io.BytesIO()
    with wave.open(output, "wb") as rendered:
        rendered.setnchannels(1)
        rendered.setsampwidth(2)
        rendered.setframerate(sample_rate)
        rendered.writeframes(frames)
    return output.getvalue()
