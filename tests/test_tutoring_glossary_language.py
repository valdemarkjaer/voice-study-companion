from __future__ import annotations

import unittest

from voice_study_companion.knowledge.medical_glossary_v1 import SpeechPolicy
from voice_study_companion.tutoring.glossary import (
    GlossaryScope,
    MedicalGlossary,
    ResolutionStatus,
    glossary_data_entry,
)
from voice_study_companion.tutoring.language import (
    EN_US,
    PT_BR,
    LanguageBasis,
    language_tagged_segments,
    select_response_language,
)
from voice_study_companion.tutoring.representations import (
    CardRepresentationBuilder,
)


class GlossaryAndLanguageTests(unittest.TestCase):
    def test_scope_precedence_is_note_then_study_scope_then_general(self) -> None:
        glossary = MedicalGlossary(
            scope_entries={
                "synthetic-clinic-demo": (
                    glossary_data_entry(
                        "Dx",
                        sense_id="scope_differential",
                        expansion_en="differential diagnosis",
                        expansion_pt_br="diagnóstico diferencial",
                        speech_policy=SpeechPolicy.EXPANSION_ONLY,
                    ),
                )
            },
            note_entries={
                "synthetic-note-42": (
                    glossary_data_entry(
                        "Dx",
                        sense_id="note_working_diagnosis",
                        expansion_en="working diagnosis",
                        expansion_pt_br="diagnóstico de trabalho",
                        speech_policy=SpeechPolicy.EXPANSION_ONLY,
                    ),
                )
            },
        )

        note = glossary.resolve(
            "Dx",
            context="",
            note_key="synthetic-note-42",
            scope="synthetic-clinic-demo",
        )
        scoped = glossary.resolve(
            "Dx",
            context="",
            note_key="synthetic-note-43",
            scope="synthetic-clinic-demo",
        )
        general = glossary.resolve(
            "Dx",
            context="",
            note_key="synthetic-note-43",
            scope="another-synthetic-scope",
        )

        self.assertIs(note.scope, GlossaryScope.NOTE)
        self.assertEqual(note.expansion("en-US"), "working diagnosis")
        self.assertIs(scoped.scope, GlossaryScope.STUDY_SCOPE)
        self.assertEqual(scoped.expansion("en-US"), "differential diagnosis")
        self.assertIs(general.scope, GlossaryScope.GENERAL)
        self.assertEqual(general.expansion("en-US"), "diagnosis")

    def test_ambiguous_senses_are_traced_and_never_guessed(self) -> None:
        glossary = MedicalGlossary()

        unresolved = glossary.resolve("VT", context="What is the normal VT?")
        cardiac = glossary.resolve("VT", context="wide complex arrhythmia on ECG")
        pulmonary = glossary.resolve("VT", context="ventilator setting in mL/kg")
        medical_mvp = glossary.resolve("MVP", context="mid-systolic click and murmur")
        product_mvp = glossary.resolve("MVP", context="startup software product")

        self.assertIs(unresolved.status, ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(unresolved.selected)
        self.assertEqual(
            {sense.sense_id for sense in unresolved.alternatives},
            {"ventricular_tachycardia", "tidal_volume"},
        )
        self.assertEqual(
            cardiac.selected and cardiac.selected.sense_id,
            "ventricular_tachycardia",
        )
        self.assertEqual(
            pulmonary.selected and pulmonary.selected.sense_id,
            "tidal_volume",
        )
        self.assertEqual(
            medical_mvp.selected and medical_mvp.selected.sense_id,
            "mitral_valve_prolapse",
        )
        self.assertEqual(
            product_mvp.selected and product_mvp.selected.sense_id,
            "minimum_viable_product",
        )

    def test_representations_preserve_display_and_trace_expansions(self) -> None:
        original = "Dx e Tx da VT em wide complex arrhythmia?"
        builder = CardRepresentationBuilder()

        pt = builder.build(original, response_language=PT_BR)
        en = builder.build(original, response_language=EN_US)

        self.assertEqual(pt.display_text, original)
        self.assertEqual(en.display_text, original)
        self.assertIn("diagnóstico", pt.semantic_text)
        self.assertIn(
            "tratamento",
            "".join(segment.text for segment in pt.speech_segments),
        )
        self.assertIn(
            "ventricular tachycardia",
            "".join(segment.text for segment in en.speech_segments),
        )
        self.assertEqual(
            [trace.surface for trace in pt.expansion_trace],
            ["Dx", "Tx", "VT"],
        )
        self.assertTrue(
            all(
                trace.glossary_version == "medical-abbreviations-v1"
                for trace in pt.expansion_trace
            )
        )
        self.assertIn("taquicardia ventricular", pt.stt_keyword_hints)

    def test_unresolved_abbreviation_is_spelled_not_expanded(self) -> None:
        representation = CardRepresentationBuilder().build(
            "What is VT?",
            response_language=EN_US,
            context="What is VT?",
        )

        trace = representation.expansion_trace[0]
        self.assertIs(trace.status, ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(trace.sense_id)
        self.assertIn(
            "V T",
            "".join(segment.text for segment in representation.speech_segments),
        )
        self.assertNotIn("ventricular tachycardia", representation.semantic_text)

    def test_language_precedence_and_code_switching(self) -> None:
        explicit = select_response_language(
            "Explique this treatment in English",
            session_preference=PT_BR,
        )
        dominant = select_response_language(
            "qual é o treatment para essa arritmia",
            session_preference=EN_US,
            conventional_english_terms=("treatment",),
        )
        fallback = select_response_language(
            "adenosine",
            session_preference=PT_BR,
            conventional_english_terms=("adenosine",),
        )

        self.assertEqual(explicit.language, EN_US)
        self.assertIs(explicit.basis, LanguageBasis.EXPLICIT_REQUEST)
        self.assertEqual(dominant.language, PT_BR)
        self.assertIs(dominant.basis, LanguageBasis.TURN_DOMINANCE)
        self.assertEqual(fallback.language, PT_BR)
        self.assertIs(fallback.basis, LanguageBasis.SESSION_PREFERENCE)

        segments = language_tagged_segments(
            "O termo é ventricular tachycardia neste exemplo.",
            response_language=PT_BR,
            conventional_english_terms=("ventricular tachycardia",),
        )
        self.assertTrue(
            any(
                segment.language == EN_US
                and segment.text == "ventricular tachycardia"
                for segment in segments
            )
        )


if __name__ == "__main__":
    unittest.main()
