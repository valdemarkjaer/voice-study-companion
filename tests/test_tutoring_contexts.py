from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, asdict

from voice_study_companion.contracts import (
    EvaluationResult,
    EvaluationVerdict,
    LanguageTag,
    ReviewRating,
    StudyCard,
)
from voice_study_companion.tutoring.contexts import (
    CardContextBuilder,
    CardContextStore,
    ContextBuildError,
    DialogueRole,
    DialogueTurn,
)
from voice_study_companion.tutoring.grading import GradePolicy
from voice_study_companion.tutoring.representations import visible_answer_text

EN_US = LanguageTag("en-US")
PT_BR = LanguageTag("pt-BR")


def synthetic_card(
    *,
    card_id: str = "synthetic-card-1",
    token: str = "synthetic-selection-1",
    prompt: str = "Which two colors appear in the generated diagram?",
    answer: str = "Blue and amber.",
) -> StudyCard:
    return StudyCard(
        card_id=card_id,
        scope="synthetic-visual-demo",
        prompt=prompt,
        official_answer=answer,
        expected_concepts=("blue", "amber"),
        selection_token=token,
        languages=(EN_US, PT_BR),
        tags=("synthetic", "public-demo"),
    )


class CardContextTests(unittest.TestCase):
    def test_preanswer_context_structurally_excludes_official_answer(self) -> None:
        secret = "Blue and amber are the protected official answer."
        context = CardContextBuilder().pre_answer(
            synthetic_card(answer=secret),
            response_language=EN_US,
        )

        self.assertFalse(hasattr(context, "official_answer"))
        self.assertNotIn(secret, repr(context))
        self.assertNotIn(secret, str(asdict(context)))
        self.assertEqual(
            context.question.display_text,
            "Which two colors appear in the generated diagram?",
        )
        with self.assertRaises(FrozenInstanceError):
            context.card_id = "changed"  # type: ignore[misc]

    def test_card_markup_is_excluded_from_semantics_and_speech(self) -> None:
        question = (
            "<div>Generated panel: <b>Dx</b>?<br>Choose a label.</div>"
            "[sound:synthetic-tone.wav]"
            '<audio><source src="ignored.wav">hidden fallback</audio>'
        )
        answer = (
            "<div>SECRET: synthetic label</div>"
            '<img src="generated.svg">[sound:answer-tone.wav]'
        )
        card = synthetic_card(prompt=question, answer=answer)
        builder = CardContextBuilder()

        pre_answer = builder.pre_answer(card, response_language=EN_US)
        spoken_question = " ".join(
            segment.text for segment in pre_answer.question.speech_segments
        )
        self.assertEqual(pre_answer.question.display_text, question)
        self.assertNotIn("<", pre_answer.question.semantic_text)
        self.assertNotIn("sound:", pre_answer.question.semantic_text)
        self.assertNotIn("hidden fallback", spoken_question)
        self.assertNotIn("SECRET", repr(pre_answer))

        revealed = builder.revealed_answer(card, response_language=EN_US)
        spoken_answer = " ".join(
            segment.text for segment in revealed.official_answer.speech_segments
        )
        self.assertEqual(revealed.official_answer.display_text, answer)
        self.assertIn("SECRET: synthetic label", spoken_answer)
        self.assertNotIn("<div>", spoken_answer)
        self.assertNotIn("generated.svg", spoken_answer)

    def test_rendered_back_speaks_only_the_answer_side(self) -> None:
        question = "<div>Which swatch is first?</div>"
        rendered_back = (
            '<div>Which swatch is first?</div><hr class="answer" id=answer>'
            "<div>Blue.</div>"
        )

        self.assertEqual(visible_answer_text(rendered_back, question), "Blue.")
        self.assertEqual(
            visible_answer_text(
                "<div>Which swatch is first?</div><hr>Blue.",
                question,
            ),
            "Blue.",
        )
        self.assertEqual(
            visible_answer_text("{{ FrontSide }} — Blue.", question),
            "Blue.",
        )

        revealed = CardContextBuilder().revealed_answer(
            synthetic_card(prompt=question, answer=rendered_back),
            response_language=EN_US,
        )
        spoken = " ".join(
            segment.text for segment in revealed.official_answer.speech_segments
        )
        self.assertEqual(revealed.official_answer.semantic_text, "Blue.")
        self.assertEqual(spoken, "Blue.")
        self.assertNotIn("Which swatch", spoken)

    def test_context_store_drops_discussion_when_card_advances(self) -> None:
        store = CardContextStore()
        store.advance(synthetic_card())
        store.append_discussion(
            DialogueTurn(DialogueRole.LEARNER, "What does the label mean?")
        )
        self.assertEqual(
            len(store.pre_answer(response_language=EN_US).discussion),
            1,
        )

        store.advance(
            synthetic_card(
                card_id="synthetic-card-2",
                token="synthetic-selection-2",
                prompt="Which swatch is second?",
            )
        )
        context = store.pre_answer(response_language=PT_BR)
        self.assertEqual(context.card_id, "synthetic-card-2")
        self.assertEqual(context.discussion, ())
        self.assertNotIn("label mean", repr(context))

    def test_grading_and_postanswer_views_require_matching_card(self) -> None:
        card = synthetic_card()
        builder = CardContextBuilder()
        grading = builder.grading(
            card,
            learner_attempt="Blue, but I forgot the other color.",
            response_language=EN_US,
        )
        result = EvaluationResult(
            turn_id="synthetic-turn-1",
            verdict=EvaluationVerdict.PARTIAL,
            feedback="Blue is covered; amber is missing.",
            covered_concepts=("blue",),
            missing_concepts=("amber",),
            confidence=0.95,
            proposed_rating=ReviewRating.HARD,
        )
        decision = GradePolicy().evaluate(result, card=card)
        post = builder.post_answer(
            card,
            decision=decision,
            response_language=EN_US,
        )

        self.assertEqual(grading.official_answer.display_text, "Blue and amber.")
        self.assertIs(post.grade, result)

        other = synthetic_card(
            card_id="synthetic-card-2",
            token="synthetic-selection-2",
        )
        wrong_decision = GradePolicy().evaluate(result, card=other)
        with self.assertRaisesRegex(ContextBuildError, "grade_card_mismatch"):
            builder.post_answer(
                card,
                decision=wrong_decision,
                response_language=EN_US,
            )


if __name__ == "__main__":
    unittest.main()
