"""Strict structured evaluation parsing with a fail-closed review policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from voice_study_companion.contracts import (
    EvaluationResult,
    EvaluationVerdict,
    ReviewRating,
    StudyCard,
)

_GRADE_FIELDS = frozenset(
    {
        "verdict",
        "covered_concepts",
        "missing_concepts",
        "incorrect_concepts",
        "confidence",
        "feedback",
        "proposed_rating",
    }
)


class GradeSchemaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GradePolicyDecision:
    """A provider result bound to the exact public card selection."""

    card_id: str
    selection_token: str = field(repr=False)
    result: EvaluationResult | None
    review_ready: bool
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.card_id.strip() or not self.selection_token.strip():
            raise ValueError("grade_decision_card_identity_required")
        expected_ready = self.result is not None and not self.rejection_reasons
        if self.review_ready is not expected_ready:
            raise ValueError("grade_decision_readiness_mismatch")


def _concepts(raw: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise GradeSchemaError(f"grade_invalid_{field_name}")
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise GradeSchemaError(f"grade_invalid_{field_name}")
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise GradeSchemaError(f"grade_duplicate_{field_name}")
    return tuple(result)


def _validate_classification(
    *,
    verdict: EvaluationVerdict,
    covered: tuple[str, ...],
    missing: tuple[str, ...],
    incorrect: tuple[str, ...],
    expected_concepts: tuple[str, ...],
) -> None:
    groups = (set(covered), set(missing), set(incorrect))
    if any(
        groups[left] & groups[right]
        for left in range(len(groups))
        for right in range(left)
    ):
        raise GradeSchemaError("grade_overlapping_concepts")

    expected = set(expected_concepts)
    if not expected or len(expected) != len(expected_concepts):
        raise GradeSchemaError("grade_invalid_expected_concepts")
    if verdict is EvaluationVerdict.UNGRADABLE:
        if covered or missing or incorrect:
            raise GradeSchemaError("grade_invalid_ungradable")
        return
    if not set(covered) <= expected or not set(missing) <= expected:
        raise GradeSchemaError("grade_unknown_expected_concept")
    if set(covered) | set(missing) != expected:
        raise GradeSchemaError("grade_incomplete_expected_concepts")
    if verdict is EvaluationVerdict.CORRECT and (missing or incorrect):
        raise GradeSchemaError("grade_contradictory_correct")
    if verdict is EvaluationVerdict.PARTIAL and (
        not covered or (not missing and not incorrect)
    ):
        raise GradeSchemaError("grade_contradictory_partial")
    if verdict is EvaluationVerdict.INCORRECT and not missing and not incorrect:
        raise GradeSchemaError("grade_contradictory_incorrect")


def parse_grade_payload(
    raw: Mapping[str, object],
    *,
    turn_id: str,
    card: StudyCard,
) -> EvaluationResult:
    """Parse an exact provider payload; reject omissions and contradictions."""

    if frozenset(raw) != _GRADE_FIELDS:
        raise GradeSchemaError("grade_invalid_fields")
    raw_verdict = raw["verdict"]
    if not isinstance(raw_verdict, str):
        raise GradeSchemaError("grade_invalid_verdict")
    try:
        verdict = EvaluationVerdict(raw_verdict)
    except ValueError as exc:
        raise GradeSchemaError("grade_invalid_verdict") from exc

    covered = _concepts(raw["covered_concepts"], "covered_concepts")
    missing = _concepts(raw["missing_concepts"], "missing_concepts")
    incorrect = _concepts(raw["incorrect_concepts"], "incorrect_concepts")
    _validate_classification(
        verdict=verdict,
        covered=covered,
        missing=missing,
        incorrect=incorrect,
        expected_concepts=card.expected_concepts,
    )

    raw_confidence = raw["confidence"]
    if isinstance(raw_confidence, bool) or not isinstance(
        raw_confidence,
        int | float,
    ):
        raise GradeSchemaError("grade_invalid_confidence")
    confidence = float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise GradeSchemaError("grade_invalid_confidence")
    feedback = raw["feedback"]
    if not isinstance(feedback, str) or not feedback.strip():
        raise GradeSchemaError("grade_invalid_feedback")
    raw_rating = raw["proposed_rating"]
    if raw_rating is None:
        rating = None
    elif isinstance(raw_rating, str):
        try:
            rating = ReviewRating(raw_rating)
        except ValueError as exc:
            raise GradeSchemaError("grade_invalid_rating") from exc
    else:
        raise GradeSchemaError("grade_invalid_rating")
    if verdict is EvaluationVerdict.UNGRADABLE and rating is not None:
        raise GradeSchemaError("grade_invalid_ungradable_rating")

    try:
        return EvaluationResult(
            turn_id=turn_id,
            verdict=verdict,
            feedback=feedback.strip(),
            covered_concepts=covered,
            missing_concepts=missing,
            incorrect_concepts=incorrect,
            confidence=confidence,
            proposed_rating=rating,
        )
    except ValueError as exc:
        raise GradeSchemaError(f"grade_invalid_result:{exc}") from exc


class GradePolicy:
    def __init__(self, *, minimum_confidence: float = 0.72) -> None:
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence_out_of_range")
        self._minimum_confidence = minimum_confidence

    def evaluate(
        self,
        result: EvaluationResult,
        *,
        card: StudyCard,
    ) -> GradePolicyDecision:
        reasons: list[str] = []
        try:
            _validate_classification(
                verdict=result.verdict,
                covered=result.covered_concepts,
                missing=result.missing_concepts,
                incorrect=result.incorrect_concepts,
                expected_concepts=card.expected_concepts,
            )
        except GradeSchemaError:
            reasons.append("result_contradicts_card")
        if result.verdict is EvaluationVerdict.UNGRADABLE:
            reasons.append("ungradable")
        if result.confidence < self._minimum_confidence:
            reasons.append("low_confidence")
        if result.proposed_rating is None:
            reasons.append("rating_missing")
        elif result.proposed_rating not in card.allowed_ratings:
            reasons.append("rating_not_permitted")
        elif not self._rating_matches_verdict(result):
            reasons.append("rating_contradicts_verdict")
        return GradePolicyDecision(
            card_id=card.card_id,
            selection_token=card.selection_token,
            result=result,
            review_ready=not reasons,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    def parse_and_evaluate(
        self,
        raw: Mapping[str, object],
        *,
        turn_id: str,
        card: StudyCard,
    ) -> GradePolicyDecision:
        try:
            result = parse_grade_payload(raw, turn_id=turn_id, card=card)
        except GradeSchemaError as exc:
            return GradePolicyDecision(
                card_id=card.card_id,
                selection_token=card.selection_token,
                result=None,
                review_ready=False,
                rejection_reasons=(str(exc),),
            )
        return self.evaluate(result, card=card)

    @staticmethod
    def _rating_matches_verdict(result: EvaluationResult) -> bool:
        if result.proposed_rating is None:
            return False
        allowed_by_verdict = {
            EvaluationVerdict.CORRECT: frozenset(
                {ReviewRating.GOOD, ReviewRating.EASY}
            ),
            EvaluationVerdict.PARTIAL: frozenset(
                {ReviewRating.AGAIN, ReviewRating.HARD}
            ),
            EvaluationVerdict.INCORRECT: frozenset({ReviewRating.AGAIN}),
            EvaluationVerdict.UNGRADABLE: frozenset(),
        }
        return result.proposed_rating in allowed_by_verdict[result.verdict]
