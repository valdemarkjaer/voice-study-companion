from __future__ import annotations

import json
import unittest
from pathlib import Path

from voice_study_companion.core.domain import DialogueIntent
from voice_study_companion.tutoring.intents import (
    CommandKind,
    IntentSchemaError,
    is_explicit_next_card_request,
    is_explicit_reveal_request,
    is_safe_official_answer_tool_context,
    parse_deterministic_command,
    parse_fallback_intent,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tutoring" / "intent_corpus.v1.json"


class IntentTests(unittest.TestCase):
    def test_bilingual_corpus_never_authorizes_review_or_advance(self) -> None:
        corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(corpus["schema_version"], "intent-corpus-v1")
        self.assertIn("Purpose-made synthetic", corpus["provenance"])

        for case in corpus["cases"]:
            with self.subTest(text=case["text"]):
                command = parse_deterministic_command(
                    case["text"],
                    safe_context=(
                        "Synthetic example: ARB for hypertension and MVP with murmur"
                    ),
                    scope=corpus["scope"],
                )
                self.assertIsNotNone(command)
                assert command is not None
                self.assertEqual(command.kind.value, case["expected"])
                self.assertFalse(command.requires_tutor_call)
                self.assertFalse(command.can_reveal)
                self.assertFalse(command.can_grade)
                self.assertFalse(command.can_submit_review)
                self.assertFalse(command.can_advance)

    def test_known_and_ambiguous_glossary_queries_are_local_and_safe(self) -> None:
        known = parse_deterministic_command(
            "What does ARB mean?",
            response_language="en-US",
            safe_context="synthetic hypertension example with losartan",
            scope="synthetic-clinic-demo",
        )
        ambiguous = parse_deterministic_command(
            "O que significa MVP?",
            response_language="pt-BR",
            safe_context="MVP",
        )

        self.assertIsNotNone(known)
        assert known is not None
        self.assertIs(known.kind, CommandKind.GLOSSARY_QUERY)
        self.assertEqual(
            known.response_text,
            "ARB means angiotensin receptor blocker.",
        )
        self.assertIsNotNone(ambiguous)
        assert ambiguous is not None
        self.assertIn("ambígua", ambiguous.response_text or "")
        self.assertIn("produto mínimo viável", ambiguous.response_text or "")
        self.assertFalse(ambiguous.can_grade)

    def test_answer_read_requires_explicit_post_reveal_request(self) -> None:
        requests = (
            "Leia a resposta oficial",
            "Por favor, leia a resposta oficial",
            "Ok, daí a resposta oficial.",
            "Quero ouvir a resposta",
            "Read the official answer",
            "Can you read the official answer?",
            "Tell me the official answer",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(parse_deterministic_command(text))
                command = parse_deterministic_command(text, post_reveal=True)
                self.assertIsNotNone(command)
                assert command is not None
                self.assertIs(command.kind, CommandKind.READ_OFFICIAL_ANSWER)
                self.assertFalse(command.can_submit_review)

    def test_nearby_discussion_does_not_trigger_answer_reading(self) -> None:
        nearby = (
            "Não leia a resposta oficial",
            "Quando você fala a resposta oficial",
            "Se eu disser, leia a resposta oficial",
            "Fala sobre a resposta oficial",
            "Por que a resposta oficial está certa?",
            "Tell me about the back pressure",
            "Read the back pressure value",
        )
        for text in nearby:
            with self.subTest(text=text):
                self.assertIsNone(
                    parse_deterministic_command(text, post_reveal=True)
                )
                self.assertFalse(is_safe_official_answer_tool_context(text))

    def test_repeat_reveal_and_next_are_narrow_explicit_controls(self) -> None:
        for text in ("pode repetir?", "de novo", "say it again"):
            with self.subTest(text=text):
                self.assertIsNone(
                    parse_deterministic_command(text, post_reveal=True)
                )
        for text in ("repita a pergunta", "repeat the question"):
            with self.subTest(text=text):
                command = parse_deterministic_command(text, post_reveal=True)
                self.assertIsNotNone(command)
                assert command is not None
                self.assertIs(command.kind, CommandKind.REPEAT)

        self.assertTrue(is_explicit_reveal_request("Show me the answer"))
        self.assertFalse(is_explicit_reveal_request("Explain the answer"))
        self.assertTrue(is_explicit_next_card_request("Next card"))
        self.assertFalse(is_explicit_next_card_request("What is next?"))

    def test_fallback_corpus_keeps_clarification_out_of_grading(self) -> None:
        corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))

        for case in corpus["fallback_cases"]:
            with self.subTest(text=case["text"]):
                decision = parse_fallback_intent(
                    {
                        "intent": case["intent"],
                        "confidence": case["confidence"],
                        "clarification_question": None,
                    }
                )
                self.assertIs(decision.can_grade, case["can_grade"])
                if case["intent"] == "clarification":
                    self.assertIs(decision.intent, DialogueIntent.CLARIFICATION)
                    self.assertFalse(decision.can_submit_review)

    def test_ambiguous_and_invalid_fallbacks_fail_closed(self) -> None:
        answer = parse_fallback_intent(
            {"intent": "answer", "confidence": 0.94, "clarification_question": None}
        )
        ambiguous = parse_fallback_intent(
            {"intent": "answer", "confidence": 0.51, "clarification_question": None},
            response_language="pt-BR",
        )
        self.assertIs(answer.intent, DialogueIntent.ANSWER)
        self.assertTrue(answer.can_grade)
        self.assertIsNone(ambiguous.intent)
        self.assertTrue(ambiguous.needs_clarification)
        self.assertIn("resposta", ambiguous.clarification_question or "")
        self.assertFalse(ambiguous.can_grade)

        invalid = (
            {"intent": "answer", "confidence": 0.9},
            {
                "intent": "unknown",
                "confidence": 0.9,
                "clarification_question": None,
            },
            {
                "intent": "answer",
                "confidence": True,
                "clarification_question": None,
            },
            {
                "intent": "answer",
                "confidence": 0.9,
                "clarification_question": "Why is this present?",
            },
        )
        for value in invalid:
            with self.subTest(payload=value):
                with self.assertRaises(IntentSchemaError):
                    parse_fallback_intent(value)


if __name__ == "__main__":
    unittest.main()
