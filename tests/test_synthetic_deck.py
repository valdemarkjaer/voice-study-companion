from __future__ import annotations

import dataclasses
import io
import unittest
import wave

from voice_study_companion.contracts import (
    EvaluationResult,
    EvaluationVerdict,
    MediaArtifact,
    MediaKind,
    PresentationProfile,
    StudyCard,
    SyncStatus,
)
from voice_study_companion.demo.deck import (
    DeckStateError,
    StudyPhase,
    build_synthetic_deck,
)


class SyntheticDeckTests(unittest.TestCase):
    def test_catalog_is_deterministic_original_bilingual_and_typed(self) -> None:
        first = build_synthetic_deck()
        second = build_synthetic_deck()

        self.assertEqual(first.all_prompts(), second.all_prompts())
        self.assertTrue(all(isinstance(card, StudyCard) for card in first.all_study_cards()))
        bilingual = first.all_prompts()[0]
        self.assertEqual(tuple(tag.value for tag in bilingual.languages), ("pt-BR", "en-US"))
        self.assertIn("português", bilingual.prompt)
        self.assertIn("English", bilingual.prompt)
        for prompt in first.all_prompts():
            self.assertEqual(
                prompt.provenance,
                "purpose-made voice-study-companion synthetic fixture",
            )
            self.assertNotIn("http://", prompt.prompt)
            self.assertNotIn("https://", prompt.prompt)

    def test_partial_credit_uses_shared_evaluation_contract(self) -> None:
        deck = build_synthetic_deck()
        deck.open_session()

        result = deck.evaluate_concepts(("delay",))

        self.assertIsInstance(result, EvaluationResult)
        self.assertEqual(result.verdict, EvaluationVerdict.PARTIAL)
        self.assertEqual(result.covered_concepts, ("delay",))
        self.assertEqual(result.missing_concepts, ("input-to-response",))

    def test_question_projection_never_contains_official_answer(self) -> None:
        deck = build_synthetic_deck()
        question = deck.open_session()

        self.assertNotIn("official_answer", {field.name for field in dataclasses.fields(question)})
        with self.assertRaisesRegex(DeckStateError, "requires_evaluation"):
            deck.request_official_answer()
        deck.evaluate_concepts(("delay", "input-to-response"))
        self.assertTrue(deck.request_official_answer().startswith("Latency"))

    def test_transit_profile_selects_only_text_cards(self) -> None:
        deck = build_synthetic_deck()
        current = deck.open_session(PresentationProfile.TRANSIT)
        selected: list[str] = []
        while current is not None:
            self.assertTrue(current.is_text_only)
            selected.append(current.card_id)
            deck.evaluate_concepts(())
            current = deck.advance()

        self.assertEqual(selected, ["bilingual-latency", "two-part-playback"])

    def test_media_is_generated_in_memory_and_uses_shared_contracts(self) -> None:
        deck = build_synthetic_deck()
        assets = deck.media_assets()
        self.assertEqual(
            tuple(asset.kind for asset in assets),
            (MediaKind.IMAGE, MediaKind.AUDIO),
        )
        image = deck.render_media("shape-sequence-v1")
        audio = deck.render_media("low-high-tone-v1")
        self.assertIsInstance(image, MediaArtifact)
        self.assertIn(b"Three-shape sequence", image.content)
        self.assertEqual(image, deck.render_media("shape-sequence-v1"))
        with wave.open(io.BytesIO(audio.content), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 1)
            self.assertEqual(rendered.getframerate(), 8_000)
            self.assertEqual(rendered.getnframes(), 3_600)

    def test_advance_and_close_have_explicit_sync_semantics(self) -> None:
        deck = build_synthetic_deck()
        deck.open_session(PresentationProfile.TRANSIT)
        self.assertEqual(deck.sync_results[0].operation_id, "opening-sync")
        deck.evaluate_concepts(("delay", "input-to-response"))
        self.assertIsNotNone(deck.advance())
        deck.evaluate_concepts(("producer-ended", "local-buffer-drained"))
        self.assertIsNone(deck.advance())

        closing = deck.close_session()

        self.assertEqual(deck.phase, StudyPhase.CLOSED)
        self.assertEqual(closing.status, SyncStatus.CONFIRMED)
        self.assertEqual(closing.changed_items, 2)
        self.assertIs(deck.close_session(), closing)
        self.assertEqual(
            deck.completed_card_ids,
            ("bilingual-latency", "two-part-playback"),
        )


if __name__ == "__main__":
    unittest.main()
