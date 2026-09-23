from __future__ import annotations

import json
import unittest
from pathlib import Path

from voice_study_companion.core.fixtures import (
    FIXTURE_SCHEMA_VERSION,
    load_audio_fixtures,
    load_card_fixtures,
    render_pcm_s16le,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "voice_pipeline" / "v1"


class CoreFixtureTests(unittest.TestCase):
    def test_versioned_schemas_and_documents_load(self) -> None:
        for schema_name in ("card.schema.json", "audio.schema.json"):
            schema = json.loads(
                (FIXTURE_ROOT / schema_name).read_text(encoding="utf-8")
            )
            self.assertEqual(
                schema["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
            self.assertEqual(
                schema["properties"]["schema_version"]["const"],
                1,
            )
            self.assertTrue(schema["$id"].startswith("urn:voice-study-companion:"))

        cards = load_card_fixtures(FIXTURE_ROOT / "cards.json")
        audio = load_audio_fixtures(
            FIXTURE_ROOT / "audio.json",
            card_ids=frozenset(card.fixture_id for card in cards),
        )
        self.assertTrue(cards)
        self.assertTrue(audio)
        self.assertEqual(FIXTURE_SCHEMA_VERSION, 1)

    def test_corpus_is_reauthored_and_covers_public_behaviors(self) -> None:
        cards = load_card_fixtures(FIXTURE_ROOT / "cards.json")
        audio = load_audio_fixtures(
            FIXTURE_ROOT / "audio.json",
            card_ids=frozenset(card.fixture_id for card in cards),
        )
        self.assertEqual(
            {card.fixture_id for card in cards},
            {
                "pt_vad_basic",
                "en_tts_basic",
                "mixed_stt_answer",
                "clarification_rtt",
                "repeat_request",
                "partial_playback_handshake",
                "leakage_probe",
                "mvp_product",
                "mvp_tournament",
                "stop_control",
            },
        )
        card_labels = set().union(*(card.labels for card in cards))
        audio_labels = set().union(*(item.labels for item in audio))
        self.assertTrue(
            {
                "pt_br",
                "english",
                "code_switching",
                "clarification",
                "repeat",
                "partial_answer",
                "answer_leakage_probe",
            }.issubset(card_labels | audio_labels)
        )
        self.assertTrue(
            {"transit_noise", "speaker_leakage", "headphones"}.issubset(
                audio_labels
            )
        )
        surfaces = {
            abbreviation.surface
            for card in cards
            for abbreviation in card.abbreviations
        }
        self.assertTrue({"VAD", "TTS", "STT", "RTT", "MVP"}.issubset(surfaces))
        mvp_senses = {
            abbreviation.expected_expansion
            for card in cards
            for abbreviation in card.abbreviations
            if abbreviation.surface == "MVP"
        }
        self.assertEqual(
            mvp_senses,
            {"minimum viable product", "most valuable player"},
        )

        serialized = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FIXTURE_ROOT.glob("*.json"))
        )
        self.assertNotIn("source_deck", serialized)
        self.assertNotIn("real_collection", serialized)

    def test_audio_recipes_render_deterministic_pcm(self) -> None:
        cards = load_card_fixtures(FIXTURE_ROOT / "cards.json")
        audio = load_audio_fixtures(
            FIXTURE_ROOT / "audio.json",
            card_ids=frozenset(card.fixture_id for card in cards),
        )
        for fixture in audio:
            first = render_pcm_s16le(fixture)
            second = render_pcm_s16le(fixture)
            expected_bytes = (
                fixture.sample_rate_hz
                * fixture.duration_ms
                // 1000
                * fixture.channels
                * 2
            )
            self.assertEqual(first, second)
            self.assertEqual(len(first), expected_bytes)
            self.assertTrue(any(first))


if __name__ == "__main__":
    unittest.main()
