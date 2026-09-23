"""Strict loaders and deterministic renderers for the public synthetic corpus."""

from __future__ import annotations

import json
import math
import random
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import DialogueIntent, GradeVerdict, LanguageTag

FIXTURE_SCHEMA_VERSION = 1


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name}_must_be_an_object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name}_must_be_an_array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_must_not_be_blank")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name}_must_be_an_integer")
    return value


def _number(value: object, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{name}_must_be_a_number")
    return float(value)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name}_must_be_a_boolean")
    return value


def _texts(value: object, name: str) -> tuple[str, ...]:
    items = _list(value, name)
    return tuple(_text(item, f"{name}_item") for item in items)


def _load_root(path: Path, expected_kind: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"fixture_file_unreadable:{path.name}") from exc
    root = _object(parsed, "fixture_root")
    if root.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported_fixture_schema_version")
    if root.get("kind") != expected_kind:
        raise ValueError(f"unexpected_fixture_kind:{root.get('kind')}")
    return root


@dataclass(frozen=True, slots=True)
class AbbreviationExpectation:
    surface: str
    meanings: tuple[str, ...]
    expected_expansion: str | None
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class CardFixture:
    fixture_id: str
    languages: tuple[LanguageTag, ...]
    question: str
    official_answer: str
    learner_utterance: str
    expected_intent: DialogueIntent
    expected_grade: GradeVerdict | None
    labels: frozenset[str]
    abbreviations: tuple[AbbreviationExpectation, ...]
    must_not_reveal: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AudioRecipe:
    waveform: str
    frequency_hz: float
    amplitude: float
    noise_amplitude: float
    seed: int


@dataclass(frozen=True, slots=True)
class AudioFixture:
    fixture_id: str
    card_fixture_id: str
    sample_rate_hz: int
    channels: int
    duration_ms: int
    expected_transcript: str
    labels: frozenset[str]
    recipe: AudioRecipe


def load_card_fixtures(path: Path) -> tuple[CardFixture, ...]:
    root = _load_root(path, "card_corpus")
    fixtures: list[CardFixture] = []
    for index, raw in enumerate(_list(root.get("fixtures"), "fixtures")):
        item = _object(raw, f"fixture_{index}")
        abbreviation_items: list[AbbreviationExpectation] = []
        for abbreviation in _list(item.get("abbreviations", []), "abbreviations"):
            value = _object(abbreviation, "abbreviation")
            expected = value.get("expected_expansion")
            abbreviation_items.append(
                AbbreviationExpectation(
                    surface=_text(value.get("surface"), "abbreviation_surface"),
                    meanings=_texts(value.get("meanings"), "abbreviation_meanings"),
                    expected_expansion=(
                        None
                        if expected is None
                        else _text(expected, "expected_expansion")
                    ),
                    ambiguous=_boolean(value.get("ambiguous"), "ambiguous"),
                )
            )
        grade_value = item.get("expected_grade")
        fixture = CardFixture(
            fixture_id=_text(item.get("fixture_id"), "fixture_id"),
            languages=tuple(
                LanguageTag(value)
                for value in _texts(item.get("languages"), "languages")
            ),
            question=_text(item.get("question"), "question"),
            official_answer=_text(item.get("official_answer"), "official_answer"),
            learner_utterance=_text(item.get("learner_utterance"), "learner_utterance"),
            expected_intent=DialogueIntent(
                _text(item.get("expected_intent"), "expected_intent")
            ),
            expected_grade=(
                None
                if grade_value is None
                else GradeVerdict(_text(grade_value, "expected_grade"))
            ),
            labels=frozenset(_texts(item.get("labels"), "labels")),
            abbreviations=tuple(abbreviation_items),
            must_not_reveal=_texts(item.get("must_not_reveal", []), "must_not_reveal"),
        )
        if not fixture.languages:
            raise ValueError("card_fixture_requires_languages")
        if not fixture.labels:
            raise ValueError("card_fixture_requires_labels")
        if fixture.expected_intent is DialogueIntent.ANSWER:
            if fixture.expected_grade is None:
                raise ValueError("answer_fixture_requires_expected_grade")
        elif fixture.expected_grade is not None:
            raise ValueError("non_answer_fixture_cannot_have_expected_grade")
        fixtures.append(fixture)
    if not fixtures:
        raise ValueError("card_corpus_must_not_be_empty")
    ids = [fixture.fixture_id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise ValueError("card_fixture_ids_must_be_unique")
    return tuple(fixtures)


def load_audio_fixtures(
    path: Path,
    *,
    card_ids: frozenset[str],
) -> tuple[AudioFixture, ...]:
    root = _load_root(path, "audio_corpus")
    fixtures: list[AudioFixture] = []
    for index, raw in enumerate(_list(root.get("fixtures"), "fixtures")):
        item = _object(raw, f"audio_fixture_{index}")
        recipe_raw = _object(item.get("recipe"), "audio_recipe")
        fixture = AudioFixture(
            fixture_id=_text(item.get("fixture_id"), "fixture_id"),
            card_fixture_id=_text(item.get("card_fixture_id"), "card_fixture_id"),
            sample_rate_hz=_integer(item.get("sample_rate_hz"), "sample_rate_hz"),
            channels=_integer(item.get("channels"), "channels"),
            duration_ms=_integer(item.get("duration_ms"), "duration_ms"),
            expected_transcript=_text(
                item.get("expected_transcript"), "expected_transcript"
            ),
            labels=frozenset(_texts(item.get("labels"), "labels")),
            recipe=AudioRecipe(
                waveform=_text(recipe_raw.get("waveform"), "waveform"),
                frequency_hz=_number(recipe_raw.get("frequency_hz"), "frequency_hz"),
                amplitude=_number(recipe_raw.get("amplitude"), "amplitude"),
                noise_amplitude=_number(
                    recipe_raw.get("noise_amplitude"), "noise_amplitude"
                ),
                seed=_integer(recipe_raw.get("seed"), "seed"),
            ),
        )
        if fixture.card_fixture_id not in card_ids:
            raise ValueError(
                f"audio_fixture_references_unknown_card:{fixture.card_fixture_id}"
            )
        if fixture.sample_rate_hz < 8_000:
            raise ValueError("audio_fixture_sample_rate_too_low")
        if fixture.channels not in {1, 2}:
            raise ValueError("audio_fixture_channels_must_be_one_or_two")
        if not 0 < fixture.duration_ms <= 5_000:
            raise ValueError("audio_fixture_duration_out_of_bounds")
        if fixture.recipe.waveform not in {"sine", "noise", "sine_noise"}:
            raise ValueError("unsupported_audio_fixture_waveform")
        if fixture.recipe.frequency_hz < 0.0:
            raise ValueError("audio_fixture_frequency_must_be_non_negative")
        if not 0.0 <= fixture.recipe.amplitude <= 1.0:
            raise ValueError("audio_fixture_amplitude_out_of_bounds")
        if not 0.0 <= fixture.recipe.noise_amplitude <= 1.0:
            raise ValueError("audio_fixture_noise_out_of_bounds")
        if not fixture.labels:
            raise ValueError("audio_fixture_requires_labels")
        fixtures.append(fixture)
    if not fixtures:
        raise ValueError("audio_corpus_must_not_be_empty")
    ids = [fixture.fixture_id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise ValueError("audio_fixture_ids_must_be_unique")
    return tuple(fixtures)


def render_pcm_s16le(fixture: AudioFixture) -> bytes:
    """Render a short deterministic signal; it contains no human recording."""

    rng = random.Random(fixture.recipe.seed)
    frame_count = fixture.sample_rate_hz * fixture.duration_ms // 1000
    samples = bytearray()
    for frame_index in range(frame_count):
        phase = (
            2.0
            * math.pi
            * fixture.recipe.frequency_hz
            * (frame_index / fixture.sample_rate_hz)
        )
        sine = math.sin(phase) if fixture.recipe.waveform != "noise" else 0.0
        noise = (
            rng.uniform(-1.0, 1.0)
            if fixture.recipe.waveform in {"noise", "sine_noise"}
            else 0.0
        )
        value = sine * fixture.recipe.amplitude + noise * fixture.recipe.noise_amplitude
        signed = max(-32_768, min(32_767, round(value * 32_767)))
        packed = struct.pack("<h", signed)
        samples.extend(packed * fixture.channels)
    return bytes(samples)
