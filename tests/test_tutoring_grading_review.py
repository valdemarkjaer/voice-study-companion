from __future__ import annotations

import unittest

from voice_study_companion.contracts import (
    EvaluationVerdict,
    LanguageTag,
    ReviewRating,
    StudyCard,
)
from voice_study_companion.tutoring.grading import (
    GradePolicy,
    GradeSchemaError,
    parse_grade_payload,
)
from voice_study_companion.tutoring.intents import parse_deterministic_command
from voice_study_companion.tutoring.review import (
    ConfirmationSource,
    DiscussionEntry,
    DiscussionProvenance,
    ReviewStateError,
    TutoringReviewState,
)

EXPECTED = ("blue", "amber")
ALLOWED = (
    ReviewRating.AGAIN,
    ReviewRating.HARD,
    ReviewRating.GOOD,
    ReviewRating.EASY,
)


def synthetic_card(
    *,
    card_id: str = "synthetic-card-1",
    token: str = "synthetic-selection-1",
    allowed_ratings: tuple[ReviewRating, ...] = ALLOWED,
) -> StudyCard:
    return StudyCard(
        card_id=card_id,
        scope="synthetic-color-demo",
        prompt="Which two colors appear?",
        official_answer="Blue and amber.",
        expected_concepts=EXPECTED,
        selection_token=token,
        languages=(LanguageTag("en-US"), LanguageTag("pt-BR")),
        allowed_ratings=allowed_ratings,
    )


def payload(
    *,
    verdict: str = "partial",
    confidence: float = 0.9,
    rating: str | None = "hard",
) -> dict[str, object]:
    if verdict == "correct":
        covered, missing, incorrect = list(EXPECTED), [], []
    elif verdict == "ungradable":
        covered, missing, incorrect = [], [], []
    else:
        covered, missing, incorrect = ["blue"], ["amber"], []
    return {
        "verdict": verdict,
        "covered_concepts": covered,
        "missing_concepts": missing,
        "incorrect_concepts": incorrect,
        "confidence": confidence,
        "feedback": "Blue was covered; amber was omitted.",
        "proposed_rating": rating,
    }


def ready_decision():
    card = synthetic_card()
    return GradePolicy().parse_and_evaluate(
        payload(),
        turn_id="synthetic-turn-1",
        card=card,
    )


def review_state() -> TutoringReviewState:
    state = TutoringReviewState(
        card_id="synthetic-card-1",
        selection_token="synthetic-selection-1",
        allowed_ratings=ALLOWED,
        max_discussion_turns=2,
        max_discussion_characters=80,
    )
    state.record_transcript("blue", "blue")
    state.apply_grade(ready_decision())
    return state


class GradingAndReviewTests(unittest.TestCase):
    def test_valid_partial_grade_is_structured_and_reviewable(self) -> None:
        card = synthetic_card()
        result = parse_grade_payload(
            payload(),
            turn_id="synthetic-turn-1",
            card=card,
        )
        decision = GradePolicy().evaluate(result, card=card)

        self.assertIs(result.verdict, EvaluationVerdict.PARTIAL)
        self.assertEqual(result.covered_concepts, ("blue",))
        self.assertEqual(result.missing_concepts, ("amber",))
        self.assertTrue(decision.review_ready)
        self.assertEqual(decision.rejection_reasons, ())

    def test_malformed_or_contradictory_grades_fail_closed(self) -> None:
        mutations = (
            (
                {
                    "covered_concepts": ["blue", "amber"],
                    "missing_concepts": ["amber"],
                },
                "overlapping",
            ),
            ({"verdict": "correct", "missing_concepts": ["amber"]}, "contradictory"),
            ({"confidence": True}, "confidence"),
            ({"unexpected": "field"}, "fields"),
        )
        for mutation, reason in mutations:
            with self.subTest(reason=reason):
                raw = payload()
                raw.update(mutation)
                with self.assertRaisesRegex(GradeSchemaError, reason):
                    parse_grade_payload(
                        raw,
                        turn_id="synthetic-turn-1",
                        card=synthetic_card(),
                    )
                decision = GradePolicy().parse_and_evaluate(
                    raw,
                    turn_id="synthetic-turn-1",
                    card=synthetic_card(),
                )
                self.assertIsNone(decision.result)
                self.assertFalse(decision.review_ready)

    def test_low_confidence_and_contradictory_rating_are_not_ready(self) -> None:
        card = synthetic_card()
        low = GradePolicy().parse_and_evaluate(
            payload(confidence=0.4),
            turn_id="synthetic-turn-1",
            card=card,
        )
        wrong_rating = GradePolicy().parse_and_evaluate(
            payload(rating="easy"),
            turn_id="synthetic-turn-1",
            card=card,
        )

        self.assertFalse(low.review_ready)
        self.assertIn("low_confidence", low.rejection_reasons)
        self.assertFalse(wrong_rating.review_ready)
        self.assertIn("rating_contradicts_verdict", wrong_rating.rejection_reasons)

    def test_only_explicit_confirmation_emits_inert_review_data(self) -> None:
        state = review_state()
        self.assertIsNone(state.confirmed_review)

        override = parse_deterministic_command("rate good")
        self.assertIsNotNone(override)
        assert override is not None
        state.apply_command(override, source=ConfirmationSource.VOICE)
        self.assertIsNone(state.confirmed_review)

        confirm = parse_deterministic_command("confirmar")
        self.assertIsNotNone(confirm)
        assert confirm is not None
        confirmed = state.apply_command(confirm, source=ConfirmationSource.VOICE)

        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertIs(confirmed.rating, ReviewRating.GOOD)
        self.assertIs(confirmed.model_proposed_rating, ReviewRating.HARD)
        self.assertTrue(confirmed.overridden)
        self.assertIs(confirmed.source, ConfirmationSource.VOICE)
        self.assertEqual(confirmed.selection_token, "synthetic-selection-1")
        with self.assertRaisesRegex(ReviewStateError, "already_confirmed"):
            state.confirm(source=ConfirmationSource.TOUCH)

    def test_restricted_rating_and_mismatched_card_fail_closed(self) -> None:
        restricted = TutoringReviewState(
            card_id="synthetic-card-1",
            selection_token="synthetic-selection-1",
            allowed_ratings=(ReviewRating.AGAIN, ReviewRating.HARD),
        )
        restricted.record_transcript("blue", "blue")
        restricted.apply_grade(ready_decision())
        override = parse_deterministic_command("rate good")
        assert override is not None
        with self.assertRaisesRegex(ReviewStateError, "rating_not_permitted"):
            restricted.apply_command(override, source=ConfirmationSource.VOICE)

        state = TutoringReviewState(
            card_id="synthetic-card-1",
            selection_token="synthetic-selection-1",
            allowed_ratings=ALLOWED,
        )
        state.record_transcript("blue", "blue")
        other = synthetic_card(
            card_id="synthetic-card-2",
            token="synthetic-selection-2",
        )
        mismatched = GradePolicy().parse_and_evaluate(
            payload(),
            turn_id="synthetic-turn-2",
            card=other,
        )
        with self.assertRaisesRegex(ReviewStateError, "grade_card_mismatch"):
            state.apply_grade(mismatched)

    def test_transcript_correction_invalidates_grade(self) -> None:
        state = review_state()
        state.correct_transcript("blue and amber")

        self.assertIsNotNone(state.transcript)
        assert state.transcript is not None
        self.assertEqual(state.transcript.revision, 1)
        self.assertIsNone(state.grade)
        self.assertIsNone(state.proposed_rating)
        with self.assertRaisesRegex(ReviewStateError, "grade_not_review_ready"):
            state.confirm(source=ConfirmationSource.TOUCH)

    def test_manual_rating_can_recover_from_ungradable_output(self) -> None:
        card = synthetic_card()
        decision = GradePolicy().parse_and_evaluate(
            payload(verdict="ungradable", confidence=0.3, rating=None),
            turn_id="synthetic-turn-1",
            card=card,
        )
        state = TutoringReviewState(
            card_id=card.card_id,
            selection_token=card.selection_token,
            allowed_ratings=card.allowed_ratings,
        )
        state.record_transcript("unclear transcript", "unclear transcript")
        state.apply_grade(decision)
        state.override_rating(ReviewRating.AGAIN)
        confirmed = state.confirm(source=ConfirmationSource.TOUCH)

        self.assertIs(confirmed.rating, ReviewRating.AGAIN)
        self.assertIsNone(confirmed.model_proposed_rating)
        self.assertTrue(confirmed.overridden)

    def test_post_reveal_discussion_is_bounded_and_cleared_on_advance(self) -> None:
        state = review_state()
        with self.assertRaisesRegex(ReviewStateError, "requires_reveal"):
            state.append_discussion(
                DiscussionEntry(DiscussionProvenance.LEARNER, "Why?")
            )

        state.reveal()
        state.append_discussion(
            DiscussionEntry(DiscussionProvenance.CARD, "Official synthetic answer")
        )
        state.append_discussion(
            DiscussionEntry(
                DiscussionProvenance.SUPPLEMENTAL,
                "Additional explanation from the tutor",
                uncertain=True,
            )
        )
        state.append_discussion(
            DiscussionEntry(DiscussionProvenance.LEARNER, "One more question")
        )

        self.assertEqual(len(state.discussion), 2)
        self.assertIs(
            state.discussion[0].provenance,
            DiscussionProvenance.SUPPLEMENTAL,
        )
        self.assertTrue(state.discussion[0].uncertain)
        state.advance(
            card_id="synthetic-card-2",
            selection_token="synthetic-selection-2",
            allowed_ratings=ALLOWED,
        )
        self.assertEqual(state.discussion, ())
        self.assertIsNone(state.transcript)
        self.assertIsNone(state.grade)
        self.assertIsNone(state.confirmed_review)


if __name__ == "__main__":
    unittest.main()
