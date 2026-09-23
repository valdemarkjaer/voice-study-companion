"""Learner-owned transcript, inert rating proposal, and discussion state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from voice_study_companion.contracts import EvaluationResult, ReviewRating
from voice_study_companion.tutoring.grading import GradePolicyDecision
from voice_study_companion.tutoring.intents import (
    CommandKind,
    DeterministicCommand,
)


class ReviewStateError(RuntimeError):
    pass


class ConfirmationSource(StrEnum):
    VOICE = "voice"
    TOUCH = "touch"


class DiscussionProvenance(StrEnum):
    CARD = "card"
    LEARNER = "learner"
    SUPPLEMENTAL = "supplemental"


@dataclass(frozen=True, slots=True)
class TranscriptRevision:
    raw_text: str
    normalized_text: str
    revision: int


@dataclass(frozen=True, slots=True)
class ConfirmedReview:
    """Confirmed data for an adapter; creating it performs no write."""

    card_id: str
    selection_token: str
    rating: ReviewRating
    model_proposed_rating: ReviewRating | None
    overridden: bool
    source: ConfirmationSource
    transcript: TranscriptRevision


@dataclass(frozen=True, slots=True)
class DiscussionEntry:
    provenance: DiscussionProvenance
    text: str
    uncertain: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("discussion_text_required")
        if self.provenance is not DiscussionProvenance.SUPPLEMENTAL and self.uncertain:
            raise ValueError("only_supplemental_discussion_can_be_uncertain")


class TutoringReviewState:
    """Keep every model rating inert until explicit learner confirmation.

    Even after confirmation this object only emits an immutable value. A
    caller-owned adapter must separately choose to apply it.
    """

    def __init__(
        self,
        *,
        card_id: str,
        selection_token: str,
        allowed_ratings: tuple[ReviewRating, ...],
        max_discussion_turns: int = 12,
        max_discussion_characters: int = 8_000,
    ) -> None:
        if not card_id.strip() or not selection_token.strip():
            raise ValueError("card_identity_required")
        if not allowed_ratings or len(set(allowed_ratings)) != len(allowed_ratings):
            raise ValueError("allowed_ratings_required")
        if max_discussion_turns < 1 or max_discussion_characters < 1:
            raise ValueError("discussion_bounds_must_be_positive")
        self._card_id = card_id
        self._selection_token = selection_token
        self._allowed_ratings = allowed_ratings
        self._max_discussion_turns = max_discussion_turns
        self._max_discussion_characters = max_discussion_characters
        self._transcript: TranscriptRevision | None = None
        self._grade: GradePolicyDecision | None = None
        self._rating_override: ReviewRating | None = None
        self._confirmed: ConfirmedReview | None = None
        self._revealed = False
        self._discussion: list[DiscussionEntry] = []

    @property
    def transcript(self) -> TranscriptRevision | None:
        return self._transcript

    @property
    def grade(self) -> EvaluationResult | None:
        return self._grade.result if self._grade is not None else None

    @property
    def proposed_rating(self) -> ReviewRating | None:
        if self._rating_override is not None:
            return self._rating_override
        if self._grade is None or self._grade.result is None:
            return None
        return self._grade.result.proposed_rating

    @property
    def confirmed_review(self) -> ConfirmedReview | None:
        return self._confirmed

    @property
    def discussion(self) -> tuple[DiscussionEntry, ...]:
        return tuple(self._discussion)

    def record_transcript(self, raw_text: str, normalized_text: str) -> None:
        if self._confirmed is not None:
            raise ReviewStateError("review_already_confirmed")
        if not raw_text.strip() or not normalized_text.strip():
            raise ValueError("transcript_text_required")
        revision = 0 if self._transcript is None else self._transcript.revision + 1
        self._transcript = TranscriptRevision(raw_text, normalized_text, revision)
        self._invalidate_grade()

    def correct_transcript(self, corrected_text: str) -> None:
        if self._transcript is None:
            raise ReviewStateError("transcript_not_recorded")
        self.record_transcript(corrected_text, corrected_text)

    def apply_grade(self, decision: GradePolicyDecision) -> None:
        if self._confirmed is not None:
            raise ReviewStateError("review_already_confirmed")
        if self._transcript is None:
            raise ReviewStateError("transcript_not_recorded")
        if (
            decision.card_id != self._card_id
            or decision.selection_token != self._selection_token
        ):
            raise ReviewStateError("grade_card_mismatch")
        self._grade = decision
        self._rating_override = None

    def override_rating(self, rating: ReviewRating) -> None:
        if self._confirmed is not None:
            raise ReviewStateError("review_already_confirmed")
        if self._transcript is None:
            raise ReviewStateError("transcript_not_recorded")
        if rating not in self._allowed_ratings:
            raise ReviewStateError("rating_not_permitted")
        self._rating_override = rating

    def apply_command(
        self,
        command: DeterministicCommand,
        *,
        source: ConfirmationSource,
    ) -> ConfirmedReview | None:
        if command.kind is CommandKind.OVERRIDE_RATING:
            if command.requested_rating is None:
                raise ReviewStateError("rating_command_missing_rating")
            self.override_rating(command.requested_rating)
            return None
        if command.kind is CommandKind.CONFIRM_RATING:
            return self.confirm(source=source)
        raise ReviewStateError("command_not_a_rating_confirmation")

    def confirm(self, *, source: ConfirmationSource) -> ConfirmedReview:
        if self._confirmed is not None:
            raise ReviewStateError("review_already_confirmed")
        if self._transcript is None:
            raise ReviewStateError("transcript_not_recorded")
        model_rating = (
            self._grade.result.proposed_rating
            if self._grade is not None and self._grade.result is not None
            else None
        )
        if self._rating_override is None and (
            self._grade is None or not self._grade.review_ready
        ):
            raise ReviewStateError("grade_not_review_ready")
        rating = self.proposed_rating
        if rating is None or rating not in self._allowed_ratings:
            raise ReviewStateError("rating_not_permitted")
        self._confirmed = ConfirmedReview(
            card_id=self._card_id,
            selection_token=self._selection_token,
            rating=rating,
            model_proposed_rating=model_rating,
            overridden=model_rating is None or rating is not model_rating,
            source=source,
            transcript=self._transcript,
        )
        return self._confirmed

    def reveal(self) -> None:
        if self._grade is None:
            raise ReviewStateError("grade_not_available")
        self._revealed = True

    def reveal_without_grade(self) -> None:
        """Record an explicit reveal without inventing an attempt or grade."""

        if self._transcript is not None or self._grade is not None:
            raise ReviewStateError("ungraded_reveal_requires_no_attempt_or_grade")
        if self._confirmed is not None:
            raise ReviewStateError("review_already_confirmed")
        self._revealed = True

    def append_discussion(self, entry: DiscussionEntry) -> None:
        if not self._revealed:
            raise ReviewStateError("discussion_requires_reveal")
        if len(entry.text) > self._max_discussion_characters:
            raise ReviewStateError("discussion_entry_too_large")
        self._discussion.append(entry)
        while (
            len(self._discussion) > self._max_discussion_turns
            or sum(len(item.text) for item in self._discussion)
            > self._max_discussion_characters
        ):
            self._discussion.pop(0)

    def advance(
        self,
        *,
        card_id: str,
        selection_token: str,
        allowed_ratings: tuple[ReviewRating, ...],
    ) -> None:
        if not card_id.strip() or not selection_token.strip():
            raise ValueError("card_identity_required")
        if not allowed_ratings or len(set(allowed_ratings)) != len(allowed_ratings):
            raise ValueError("allowed_ratings_required")
        self._card_id = card_id
        self._selection_token = selection_token
        self._allowed_ratings = allowed_ratings
        self._transcript = None
        self._grade = None
        self._rating_override = None
        self._confirmed = None
        self._revealed = False
        self._discussion.clear()

    def _invalidate_grade(self) -> None:
        self._grade = None
        self._rating_override = None
