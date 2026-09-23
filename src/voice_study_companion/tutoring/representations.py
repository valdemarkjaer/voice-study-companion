"""Separate faithful display, semantic, and spoken card representations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from voice_study_companion.contracts import LanguageTag, SpeechSegment
from voice_study_companion.knowledge.medical_glossary_v1 import SpeechPolicy
from voice_study_companion.tutoring.glossary import (
    GlossaryScope,
    MedicalGlossary,
    ResolutionStatus,
)
from voice_study_companion.tutoring.language import (
    canonical_language,
    language_tagged_segments,
)

_SOUND_MARKUP = re.compile(r"\[sound:[^\]\r\n]+\]", re.IGNORECASE)
_FRONT_SIDE_TEMPLATE = re.compile(r"{{\s*FrontSide\s*}}", re.IGNORECASE)
_ANSWER_SEPARATOR = re.compile(
    r"<hr\b(?=[^>]*\bid\s*=\s*(?:[\"']answer[\"']|answer(?:\s|/?>)))[^>]*>",
    re.IGNORECASE,
)
_HORIZONTAL_RULE = re.compile(r"<hr\b[^>]*>", re.IGNORECASE)
_ID_ATTRIBUTE = re.compile(r"\bid\s*=", re.IGNORECASE)


class _VisibleCardTextParser(HTMLParser):
    _HIDDEN_CONTAINERS = {"audio", "object", "script", "style", "video"}
    _HIDDEN_VOID = {"embed", "source"}
    _BREAKS = {
        "br",
        "div",
        "li",
        "ol",
        "p",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in self._HIDDEN_CONTAINERS:
            self._hidden_depth += 1
        elif normalized in self._HIDDEN_VOID:
            return
        elif not self._hidden_depth and normalized in self._BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._HIDDEN_CONTAINERS:
            if self._hidden_depth:
                self._hidden_depth -= 1
        elif not self._hidden_depth and normalized in self._BREAKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def visible_card_text(card_html: str) -> str:
    """Derive text for semantics and speech while retaining display HTML."""

    parser = _VisibleCardTextParser()
    parser.feed(_SOUND_MARKUP.sub(" ", card_html))
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def visible_answer_text(
    answer_html: str,
    question_html: str | None = None,
) -> str:
    """Return only the answer side of a common spaced-repetition card back."""

    question = visible_card_text(question_html) if question_html is not None else ""
    separator = _ANSWER_SEPARATOR.search(answer_html)
    if separator is None and question:
        for candidate in _HORIZONTAL_RULE.finditer(answer_html):
            if _ID_ATTRIBUTE.search(candidate.group(0)):
                continue
            prefix = _FRONT_SIDE_TEMPLATE.sub(" ", answer_html[: candidate.start()])
            if visible_card_text(prefix) == question:
                separator = candidate
                break
    answer_side = answer_html[separator.end() :] if separator else answer_html
    had_front_side = _FRONT_SIDE_TEMPLATE.search(answer_side) is not None
    answer_side = _FRONT_SIDE_TEMPLATE.sub(" ", answer_side)
    visible = visible_card_text(answer_side)
    if had_front_side:
        visible = visible.lstrip(" \t\r\n-–—|:;")
    if separator is not None or question_html is None or not visible:
        return visible
    if not question:
        return visible
    if visible == question:
        return ""
    if visible.startswith(question):
        suffix = visible[len(question) :]
        if not suffix or suffix[0].isspace() or suffix[0] in "-–—|:;":
            return suffix.lstrip(" \t\r\n-–—|:;")
    return visible


@dataclass(frozen=True, slots=True)
class ExpansionTrace:
    surface: str
    start: int
    end: int
    status: ResolutionStatus
    glossary_version: str
    scope: GlossaryScope | None
    scope_key: str | None
    sense_id: str | None
    semantic_form: str
    spoken_form: str
    matched_context_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CardRepresentations:
    display_text: str
    semantic_text: str
    speech_segments: tuple[SpeechSegment, ...]
    expansion_trace: tuple[ExpansionTrace, ...]
    stt_keyword_hints: tuple[str, ...]


def _spoken_acronym(surface: str) -> str:
    letters = tuple(character.upper() for character in surface if character.isalnum())
    return " ".join(letters) if letters else surface


class CardRepresentationBuilder:
    def __init__(self, glossary: MedicalGlossary | None = None) -> None:
        self._glossary = glossary or MedicalGlossary()
        surfaces = sorted(self._glossary.surfaces, key=len, reverse=True)
        self._pattern = re.compile(
            rf"(?<!\w)({'|'.join(re.escape(item) for item in surfaces)})(?!\w)",
            re.IGNORECASE,
        )

    def build(
        self,
        display_text: str,
        *,
        response_language: str | LanguageTag,
        semantic_source: str | None = None,
        context: str | None = None,
        note_key: str | int | None = None,
        scope: str | None = None,
        conventional_english_terms: tuple[str, ...] = (),
    ) -> CardRepresentations:
        """Create derived forms while preserving ``display_text`` exactly."""

        language = canonical_language(response_language)
        visible_text = visible_card_text(
            semantic_source if semantic_source is not None else display_text
        )
        if not visible_text:
            raise ValueError("card_text_has_no_visible_speech")
        authorized_context = visible_card_text(
            context if context is not None else display_text
        )
        semantic_parts: list[str] = []
        speech_parts: list[str] = []
        traces: list[ExpansionTrace] = []
        matched_surfaces: list[str] = []
        position = 0

        for match in self._pattern.finditer(visible_text):
            unchanged = visible_text[position : match.start()]
            semantic_parts.append(unchanged)
            speech_parts.append(unchanged)
            surface = match.group(0)
            matched_surfaces.append(surface)
            resolution = self._glossary.resolve(
                surface,
                context=authorized_context,
                note_key=note_key,
                scope=scope,
            )
            expansion = resolution.expansion(language.value)
            if expansion is None:
                semantic_form = surface
                spoken_form = _spoken_acronym(surface)
            else:
                semantic_form = f"{surface} [{expansion}]"
                spoken_form = (
                    expansion
                    if resolution.speech_policy is SpeechPolicy.EXPANSION_ONLY
                    else f"{_spoken_acronym(surface)}, {expansion}"
                )
            semantic_parts.append(semantic_form)
            speech_parts.append(spoken_form)
            traces.append(
                ExpansionTrace(
                    surface=surface,
                    start=match.start(),
                    end=match.end(),
                    status=resolution.status,
                    glossary_version=resolution.glossary_version,
                    scope=resolution.scope,
                    scope_key=resolution.scope_key,
                    sense_id=(
                        resolution.selected.sense_id
                        if resolution.selected is not None
                        else None
                    ),
                    semantic_form=semantic_form,
                    spoken_form=spoken_form,
                    matched_context_terms=resolution.matched_context_terms,
                )
            )
            position = match.end()

        semantic_parts.append(visible_text[position:])
        speech_parts.append(visible_text[position:])
        speech_text = "".join(speech_parts)
        return CardRepresentations(
            display_text=display_text,
            semantic_text="".join(semantic_parts),
            speech_segments=language_tagged_segments(
                speech_text,
                response_language=language,
                conventional_english_terms=conventional_english_terms,
            ),
            expansion_trace=tuple(traces),
            stt_keyword_hints=self._glossary.keyword_hints(
                tuple(matched_surfaces),
                context=authorized_context,
                note_key=note_key,
                scope=scope,
            ),
        )
