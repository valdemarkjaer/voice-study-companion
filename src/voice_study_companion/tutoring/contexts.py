"""Immutable, phase-specific tutoring contexts built from public cards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from voice_study_companion.contracts import (
    EvaluationResult,
    LanguageTag,
    StudyCard,
)
from voice_study_companion.tutoring.grading import GradePolicyDecision
from voice_study_companion.tutoring.language import canonical_language
from voice_study_companion.tutoring.representations import (
    CardRepresentationBuilder,
    CardRepresentations,
    visible_answer_text,
)


class ContextBuildError(ValueError):
    """Raised when a consequential context cannot be built safely."""


class DialogueRole(StrEnum):
    LEARNER = "learner"
    TUTOR = "tutor"


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    role: DialogueRole
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("dialogue_turn_text_required")


@dataclass(frozen=True, slots=True)
class SafeCardMetadata:
    scope: str
    tags: tuple[str, ...]
    languages: tuple[LanguageTag, ...]
    media_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreAnswerContext:
    """Question-only model view; no answer field can be represented."""

    card_id: str
    selection_token: str
    question: CardRepresentations
    metadata: SafeCardMetadata
    discussion: tuple[DialogueTurn, ...]


@dataclass(frozen=True, slots=True)
class GradingContext:
    card_id: str
    selection_token: str
    question: CardRepresentations
    official_answer: CardRepresentations
    expected_concepts: tuple[str, ...]
    learner_attempt: str
    response_language: LanguageTag


@dataclass(frozen=True, slots=True)
class RevealedAnswerContext:
    """Authoritative answer view for an explicit reveal without an attempt."""

    card_id: str
    selection_token: str
    question: CardRepresentations
    official_answer: CardRepresentations
    metadata: SafeCardMetadata
    discussion: tuple[DialogueTurn, ...]


@dataclass(frozen=True, slots=True)
class PostAnswerContext:
    card_id: str
    selection_token: str
    question: CardRepresentations
    official_answer: CardRepresentations
    grade: EvaluationResult
    metadata: SafeCardMetadata
    discussion: tuple[DialogueTurn, ...]


def _metadata(card: StudyCard) -> SafeCardMetadata:
    return SafeCardMetadata(
        scope=card.scope,
        tags=card.tags,
        languages=card.languages,
        media_ids=tuple(media.media_id for media in card.media),
    )


class CardContextBuilder:
    """Build explicitly authorized views without retaining the source card."""

    def __init__(
        self,
        representations: CardRepresentationBuilder | None = None,
    ) -> None:
        self._representations = representations or CardRepresentationBuilder()

    def pre_answer(
        self,
        card: StudyCard,
        *,
        response_language: str | LanguageTag,
        discussion: tuple[DialogueTurn, ...] = (),
        conventional_english_terms: tuple[str, ...] = (),
    ) -> PreAnswerContext:
        return PreAnswerContext(
            card_id=card.card_id,
            selection_token=card.selection_token,
            question=self._representations.build(
                card.prompt,
                response_language=response_language,
                context=card.prompt,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            metadata=_metadata(card),
            discussion=tuple(discussion),
        )

    def grading(
        self,
        card: StudyCard,
        *,
        learner_attempt: str,
        response_language: str | LanguageTag,
        conventional_english_terms: tuple[str, ...] = (),
    ) -> GradingContext:
        if not learner_attempt.strip():
            raise ContextBuildError("learner_attempt_required")
        answer_semantic = visible_answer_text(card.official_answer, card.prompt)
        if not answer_semantic:
            raise ContextBuildError("official_answer_has_no_visible_text")
        context = f"{card.prompt}\n{answer_semantic}"
        language = canonical_language(response_language)
        return GradingContext(
            card_id=card.card_id,
            selection_token=card.selection_token,
            question=self._representations.build(
                card.prompt,
                response_language=language,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            official_answer=self._representations.build(
                card.official_answer,
                response_language=language,
                semantic_source=answer_semantic,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            expected_concepts=card.expected_concepts,
            learner_attempt=learner_attempt,
            response_language=language,
        )

    def post_answer(
        self,
        card: StudyCard,
        *,
        decision: GradePolicyDecision,
        response_language: str | LanguageTag,
        discussion: tuple[DialogueTurn, ...] = (),
        conventional_english_terms: tuple[str, ...] = (),
    ) -> PostAnswerContext:
        if (
            decision.card_id != card.card_id
            or decision.selection_token != card.selection_token
        ):
            raise ContextBuildError("grade_card_mismatch")
        if decision.result is None:
            raise ContextBuildError("grade_result_required")
        answer_semantic = visible_answer_text(card.official_answer, card.prompt)
        if not answer_semantic:
            raise ContextBuildError("official_answer_has_no_visible_text")
        context = f"{card.prompt}\n{answer_semantic}"
        return PostAnswerContext(
            card_id=card.card_id,
            selection_token=card.selection_token,
            question=self._representations.build(
                card.prompt,
                response_language=response_language,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            official_answer=self._representations.build(
                card.official_answer,
                response_language=response_language,
                semantic_source=answer_semantic,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            grade=decision.result,
            metadata=_metadata(card),
            discussion=tuple(discussion),
        )

    def revealed_answer(
        self,
        card: StudyCard,
        *,
        response_language: str | LanguageTag,
        discussion: tuple[DialogueTurn, ...] = (),
        conventional_english_terms: tuple[str, ...] = (),
    ) -> RevealedAnswerContext:
        answer_semantic = visible_answer_text(card.official_answer, card.prompt)
        if not answer_semantic:
            raise ContextBuildError("official_answer_has_no_visible_text")
        context = f"{card.prompt}\n{answer_semantic}"
        return RevealedAnswerContext(
            card_id=card.card_id,
            selection_token=card.selection_token,
            question=self._representations.build(
                card.prompt,
                response_language=response_language,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            official_answer=self._representations.build(
                card.official_answer,
                response_language=response_language,
                semantic_source=answer_semantic,
                context=context,
                scope=card.scope,
                conventional_english_terms=conventional_english_terms,
            ),
            metadata=_metadata(card),
            discussion=tuple(discussion),
        )


class CardContextStore:
    """Erase card-bounded discussion whenever the active card advances."""

    def __init__(self, builder: CardContextBuilder | None = None) -> None:
        self._builder = builder or CardContextBuilder()
        self._card: StudyCard | None = None
        self._card_key: tuple[str, str] | None = None
        self._discussion: list[DialogueTurn] = []

    @property
    def card_key(self) -> tuple[str, str] | None:
        return self._card_key

    def advance(self, card: StudyCard) -> None:
        self._card = card
        self._card_key = (card.card_id, card.selection_token)
        self._discussion.clear()

    def append_discussion(self, turn: DialogueTurn) -> None:
        if self._card is None:
            raise ContextBuildError("no_active_card")
        self._discussion.append(turn)

    def pre_answer(
        self,
        *,
        response_language: str | LanguageTag,
    ) -> PreAnswerContext:
        if self._card is None:
            raise ContextBuildError("no_active_card")
        return self._builder.pre_answer(
            self._card,
            response_language=response_language,
            discussion=tuple(self._discussion),
        )
