"""Versioned, provider-neutral bilingual medical terminology.

The entries are independently authored factual terminology.  They contain no
study-card text and are immutable so model output cannot rewrite the glossary
used for speech or evaluation context.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

GLOSSARY_VERSION = "medical-abbreviations-v1"


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name}_required")


class SpeechPolicy(StrEnum):
    """How a resolved abbreviation is rendered for speech."""

    EXPANSION_ONLY = "expansion_only"
    ACRONYM_THEN_EXPANSION = "acronym_then_expansion"


@dataclass(frozen=True, slots=True)
class GlossarySenseData:
    sense_id: str
    expansion_en: str
    expansion_pt_br: str
    context_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.sense_id, "sense_id")
        _require_text(self.expansion_en, "expansion_en")
        _require_text(self.expansion_pt_br, "expansion_pt_br")
        if any(not term.strip() for term in self.context_terms):
            raise ValueError("context_terms_must_not_contain_blanks")
        if len(set(self.context_terms)) != len(self.context_terms):
            raise ValueError("context_terms_must_be_unique")


@dataclass(frozen=True, slots=True)
class GlossaryEntryData:
    surface: str
    speech_policy: SpeechPolicy
    senses: tuple[GlossarySenseData, ...]

    def __post_init__(self) -> None:
        _require_text(self.surface, "surface")
        if not self.senses:
            raise ValueError("glossary_entry_requires_a_sense")
        sense_ids = tuple(sense.sense_id for sense in self.senses)
        if len(set(sense_ids)) != len(sense_ids):
            raise ValueError("glossary_sense_ids_must_be_unique")


GENERAL_ENTRIES: tuple[GlossaryEntryData, ...] = (
    GlossaryEntryData(
        surface="Dx",
        speech_policy=SpeechPolicy.EXPANSION_ONLY,
        senses=(
            GlossarySenseData(
                sense_id="diagnosis",
                expansion_en="diagnosis",
                expansion_pt_br="diagnóstico",
            ),
        ),
    ),
    GlossaryEntryData(
        surface="Tx",
        speech_policy=SpeechPolicy.EXPANSION_ONLY,
        senses=(
            GlossarySenseData(
                sense_id="treatment",
                expansion_en="treatment",
                expansion_pt_br="tratamento",
            ),
        ),
    ),
    GlossaryEntryData(
        surface="VT",
        speech_policy=SpeechPolicy.ACRONYM_THEN_EXPANSION,
        senses=(
            GlossarySenseData(
                sense_id="ventricular_tachycardia",
                expansion_en="ventricular tachycardia",
                expansion_pt_br="taquicardia ventricular",
                context_terms=(
                    "arrhythmia",
                    "arritmia",
                    "cardioversion",
                    "cardioversão",
                    "ecg",
                    "rhythm",
                    "ritmo",
                    "tachycardia",
                    "taquicardia",
                    "wide complex",
                    "qrs largo",
                ),
            ),
            GlossarySenseData(
                sense_id="tidal_volume",
                expansion_en="tidal volume",
                expansion_pt_br="volume corrente",
                context_terms=(
                    "ventilation",
                    "ventilação",
                    "ventilator",
                    "ventilador",
                    "ml/kg",
                    "lung",
                    "pulmão",
                    "respiratory",
                    "respiratório",
                ),
            ),
        ),
    ),
    GlossaryEntryData(
        surface="ARB",
        speech_policy=SpeechPolicy.ACRONYM_THEN_EXPANSION,
        senses=(
            GlossarySenseData(
                sense_id="angiotensin_receptor_blocker",
                expansion_en="angiotensin receptor blocker",
                expansion_pt_br="bloqueador do receptor de angiotensina",
                context_terms=(
                    "blood pressure",
                    "hypertension",
                    "hipertensão",
                    "losartan",
                    "losartana",
                    "renin",
                    "renina",
                    "valsartan",
                    "valsartana",
                ),
            ),
        ),
    ),
    GlossaryEntryData(
        surface="SVT",
        speech_policy=SpeechPolicy.ACRONYM_THEN_EXPANSION,
        senses=(
            GlossarySenseData(
                sense_id="supraventricular_tachycardia",
                expansion_en="supraventricular tachycardia",
                expansion_pt_br="taquicardia supraventricular",
                context_terms=(
                    "adenosine",
                    "adenosina",
                    "arrhythmia",
                    "arritmia",
                    "narrow complex",
                    "qrs estreito",
                    "tachycardia",
                    "taquicardia",
                ),
            ),
        ),
    ),
    GlossaryEntryData(
        surface="MVP",
        speech_policy=SpeechPolicy.ACRONYM_THEN_EXPANSION,
        senses=(
            GlossarySenseData(
                sense_id="mitral_valve_prolapse",
                expansion_en="mitral valve prolapse",
                expansion_pt_br="prolapso da válvula mitral",
                context_terms=(
                    "click",
                    "clique",
                    "mitral",
                    "murmur",
                    "sopro",
                    "valve",
                    "válvula",
                ),
            ),
            GlossarySenseData(
                sense_id="minimum_viable_product",
                expansion_en="minimum viable product",
                expansion_pt_br="produto mínimo viável",
                context_terms=(
                    "business",
                    "negócio",
                    "product",
                    "produto",
                    "prototype",
                    "protótipo",
                    "software",
                    "startup",
                ),
            ),
        ),
    ),
)
