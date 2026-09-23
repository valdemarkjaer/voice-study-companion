"""Deterministic, scoped medical-abbreviation resolution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from voice_study_companion.knowledge.medical_glossary_v1 import (
    GENERAL_ENTRIES,
    GLOSSARY_VERSION,
    GlossaryEntryData,
    GlossarySenseData,
    SpeechPolicy,
)


class GlossaryScope(StrEnum):
    NOTE = "note"
    STUDY_SCOPE = "study_scope"
    GENERAL = "general"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GlossarySense:
    sense_id: str
    expansion_en: str
    expansion_pt_br: str
    context_terms: tuple[str, ...] = ()

    def expansion(self, language: str) -> str:
        return self.expansion_pt_br if language.casefold().startswith("pt") else self.expansion_en


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    surface: str
    speech_policy: SpeechPolicy
    senses: tuple[GlossarySense, ...]


@dataclass(frozen=True, slots=True)
class GlossaryResolution:
    surface: str
    status: ResolutionStatus
    glossary_version: str
    scope: GlossaryScope | None
    scope_key: str | None
    speech_policy: SpeechPolicy | None
    selected: GlossarySense | None
    alternatives: tuple[GlossarySense, ...]
    matched_context_terms: tuple[str, ...] = ()

    def expansion(self, language: str) -> str | None:
        return None if self.selected is None else self.selected.expansion(language)


def _entry(data: GlossaryEntryData) -> GlossaryEntry:
    return GlossaryEntry(
        surface=data.surface,
        speech_policy=data.speech_policy,
        senses=tuple(
            GlossarySense(
                sense_id=sense.sense_id,
                expansion_en=sense.expansion_en,
                expansion_pt_br=sense.expansion_pt_br,
                context_terms=sense.context_terms,
            )
            for sense in data.senses
        ),
    )


def _normalized_scope(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _entry_map(entries: tuple[GlossaryEntryData, ...]) -> Mapping[str, GlossaryEntry]:
    mapped = {entry.surface.casefold(): _entry(entry) for entry in entries}
    if len(mapped) != len(entries):
        raise ValueError("glossary_surfaces_must_be_unique")
    return MappingProxyType(mapped)


class MedicalGlossary:
    """Immutable glossary with note, study-scope, then general precedence."""

    def __init__(
        self,
        *,
        general_entries: tuple[GlossaryEntryData, ...] = GENERAL_ENTRIES,
        scope_entries: Mapping[str, tuple[GlossaryEntryData, ...]] | None = None,
        note_entries: Mapping[str, tuple[GlossaryEntryData, ...]] | None = None,
        version: str = GLOSSARY_VERSION,
    ) -> None:
        if not version.strip():
            raise ValueError("glossary_version_required")
        self._version = version
        self._general = _entry_map(general_entries)
        self._scopes = MappingProxyType(
            {
                _normalized_scope(key): _entry_map(entries)
                for key, entries in (scope_entries or {}).items()
            }
        )
        self._notes = MappingProxyType(
            {
                str(key): _entry_map(entries)
                for key, entries in (note_entries or {}).items()
            }
        )

    @property
    def version(self) -> str:
        return self._version

    @property
    def surfaces(self) -> tuple[str, ...]:
        values: dict[str, str] = {}
        indexes = (*self._notes.values(), *self._scopes.values(), self._general)
        for entries in indexes:
            for token, entry in entries.items():
                values.setdefault(token, entry.surface)
        return tuple(values.values())

    def known(self, surface: str) -> bool:
        token = surface.strip().casefold()
        if token in self._general:
            return True
        return any(
            token in entries
            for entries in (*self._scopes.values(), *self._notes.values())
        )

    def resolve(
        self,
        surface: str,
        *,
        context: str,
        note_key: str | int | None = None,
        scope: str | None = None,
    ) -> GlossaryResolution:
        token = surface.strip().casefold()
        if not token:
            return self._unknown(surface)

        candidates: list[tuple[GlossaryScope, str, GlossaryEntry]] = []
        if note_key is not None:
            normalized_note = str(note_key)
            note_entry = self._notes.get(normalized_note, {}).get(token)
            if note_entry is not None:
                candidates.append((GlossaryScope.NOTE, normalized_note, note_entry))

        if scope is not None:
            normalized_scope = _normalized_scope(scope)
            scope_entry = self._scopes.get(normalized_scope, {}).get(token)
            if scope_entry is not None:
                candidates.append(
                    (GlossaryScope.STUDY_SCOPE, normalized_scope, scope_entry)
                )

        general_entry = self._general.get(token)
        if general_entry is not None:
            candidates.append((GlossaryScope.GENERAL, "general", general_entry))
        if not candidates:
            return self._unknown(surface)

        selected_scope, scope_key, entry = candidates[0]
        sense, matched_terms = self._resolve_sense(entry, context)
        return GlossaryResolution(
            surface=surface,
            status=(
                ResolutionStatus.RESOLVED
                if sense is not None
                else ResolutionStatus.AMBIGUOUS
            ),
            glossary_version=self._version,
            scope=selected_scope,
            scope_key=scope_key,
            speech_policy=entry.speech_policy,
            selected=sense,
            alternatives=entry.senses,
            matched_context_terms=matched_terms,
        )

    def keyword_hints(
        self,
        surfaces: tuple[str, ...],
        *,
        context: str,
        note_key: str | int | None = None,
        scope: str | None = None,
    ) -> tuple[str, ...]:
        values: list[str] = []
        for surface in surfaces:
            resolution = self.resolve(
                surface,
                context=context,
                note_key=note_key,
                scope=scope,
            )
            values.append(surface)
            senses = (
                (resolution.selected,)
                if resolution.selected is not None
                else resolution.alternatives
            )
            for sense in senses:
                values.extend((sense.expansion_en, sense.expansion_pt_br))
        return tuple(dict.fromkeys(value for value in values if value))

    def _unknown(self, surface: str) -> GlossaryResolution:
        return GlossaryResolution(
            surface=surface,
            status=ResolutionStatus.UNKNOWN,
            glossary_version=self._version,
            scope=None,
            scope_key=None,
            speech_policy=None,
            selected=None,
            alternatives=(),
        )

    @staticmethod
    def _resolve_sense(
        entry: GlossaryEntry,
        context: str,
    ) -> tuple[GlossarySense | None, tuple[str, ...]]:
        if len(entry.senses) == 1:
            return entry.senses[0], ()

        normalized = context.casefold()
        scored: list[tuple[int, GlossarySense, tuple[str, ...]]] = []
        for sense in entry.senses:
            matched = tuple(
                term for term in sense.context_terms if term.casefold() in normalized
            )
            scored.append((len(matched), sense, matched))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or scored[0][0] == 0:
            return None, ()
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            tied_terms = (*scored[0][2], *scored[1][2])
            return None, tuple(dict.fromkeys(tied_terms))
        return scored[0][1], scored[0][2]


def glossary_data_entry(
    surface: str,
    *,
    sense_id: str,
    expansion_en: str,
    expansion_pt_br: str,
    context_terms: tuple[str, ...] = (),
    speech_policy: SpeechPolicy = SpeechPolicy.ACRONYM_THEN_EXPANSION,
) -> GlossaryEntryData:
    """Construct a validated immutable entry for a public test scope."""

    return GlossaryEntryData(
        surface=surface,
        speech_policy=speech_policy,
        senses=(
            GlossarySenseData(
                sense_id=sense_id,
                expansion_en=expansion_en,
                expansion_pt_br=expansion_pt_br,
                context_terms=context_terms,
            ),
        ),
    )
