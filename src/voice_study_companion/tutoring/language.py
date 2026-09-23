"""Mixed Portuguese/English response selection and speech tagging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from voice_study_companion.contracts import LanguageTag, SpeechSegment

PT_BR = LanguageTag("pt-BR")
EN_US = LanguageTag("en-US")

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)?", re.UNICODE)
_PT_MARKERS = frozenset(
    {
        "a",
        "agora",
        "como",
        "continuar",
        "continue",
        "da",
        "de",
        "do",
        "em",
        "essa",
        "esse",
        "explica",
        "explique",
        "falar",
        "fale",
        "isso",
        "o",
        "para",
        "por",
        "português",
        "qual",
        "que",
        "responda",
        "uma",
    }
)
_EN_MARKERS = frozenset(
    {
        "a",
        "answer",
        "can",
        "continue",
        "does",
        "english",
        "explain",
        "for",
        "how",
        "in",
        "is",
        "it",
        "please",
        "speak",
        "tell",
        "the",
        "this",
        "to",
        "treatment",
        "what",
        "why",
    }
)
_EXPLICIT_PT = re.compile(
    r"\b(?:em|para)\s+portugu[eê]s\b|"
    r"\b(?:fale|responda|explique)\s+(?:em\s+)?portugu[eê]s\b",
    re.IGNORECASE,
)
_EXPLICIT_EN = re.compile(
    r"\b(?:in|to)\s+english\b|"
    r"\b(?:speak|answer|explain)\s+(?:in\s+)?english\b|"
    r"\bem\s+ingl[eê]s\b",
    re.IGNORECASE,
)


class LanguageBasis(StrEnum):
    EXPLICIT_REQUEST = "explicit_request"
    TURN_DOMINANCE = "turn_dominance"
    SESSION_PREFERENCE = "session_preference"


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    language: LanguageTag
    basis: LanguageBasis
    portuguese_markers: int
    english_markers: int


def canonical_language(value: str | LanguageTag) -> LanguageTag:
    raw = value.value if isinstance(value, LanguageTag) else value
    normalized = raw.strip().casefold().replace("_", "-")
    if normalized in {"pt", "pt-br", "portuguese", "português", "br"}:
        return PT_BR
    if normalized in {"en", "en-us", "english", "inglês"}:
        return EN_US
    raise ValueError("unsupported_response_language")


def explicit_language_request(text: str) -> LanguageTag | None:
    pt_match = _EXPLICIT_PT.search(text)
    en_match = _EXPLICIT_EN.search(text)
    if pt_match and en_match:
        return PT_BR if pt_match.start() > en_match.start() else EN_US
    if pt_match:
        return PT_BR
    if en_match:
        return EN_US
    return None


def select_response_language(
    text: str,
    *,
    session_preference: str | LanguageTag = PT_BR,
    explicit_request: str | LanguageTag | None = None,
    conventional_english_terms: tuple[str, ...] = (),
) -> LanguageDecision:
    """Apply explicit request, current-turn dominance, then preference."""

    explicit = (
        canonical_language(explicit_request)
        if explicit_request is not None
        else explicit_language_request(text)
    )
    ignored = {
        token.casefold()
        for term in conventional_english_terms
        for token in _TOKEN_RE.findall(term)
    }
    tokens = tuple(token.casefold() for token in _TOKEN_RE.findall(text))
    pt_count = sum(token in _PT_MARKERS for token in tokens if token not in ignored)
    en_count = sum(token in _EN_MARKERS for token in tokens if token not in ignored)
    if explicit is not None:
        return LanguageDecision(
            language=explicit,
            basis=LanguageBasis.EXPLICIT_REQUEST,
            portuguese_markers=pt_count,
            english_markers=en_count,
        )
    if pt_count != en_count:
        return LanguageDecision(
            language=PT_BR if pt_count > en_count else EN_US,
            basis=LanguageBasis.TURN_DOMINANCE,
            portuguese_markers=pt_count,
            english_markers=en_count,
        )
    return LanguageDecision(
        language=canonical_language(session_preference),
        basis=LanguageBasis.SESSION_PREFERENCE,
        portuguese_markers=pt_count,
        english_markers=en_count,
    )


def language_tagged_segments(
    text: str,
    *,
    response_language: str | LanguageTag,
    conventional_english_terms: tuple[str, ...] = (),
) -> tuple[SpeechSegment, ...]:
    """Tag configured English terms without translating the source text."""

    if not text.strip():
        raise ValueError("speech_text_required")
    primary = canonical_language(response_language)
    if primary == EN_US or not conventional_english_terms:
        return (SpeechSegment(language=primary, text=text),)

    escaped = sorted(
        (re.escape(term) for term in conventional_english_terms if term.strip()),
        key=len,
        reverse=True,
    )
    if not escaped:
        return (SpeechSegment(language=primary, text=text),)
    pattern = re.compile(rf"(?<!\w)({'|'.join(escaped)})(?!\w)", re.IGNORECASE)
    segments: list[SpeechSegment] = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            segments.append(
                SpeechSegment(language=primary, text=text[position : match.start()])
            )
        segments.append(SpeechSegment(language=EN_US, text=match.group(0)))
        position = match.end()
    if position < len(text):
        segments.append(SpeechSegment(language=primary, text=text[position:]))
    return tuple(segment for segment in segments if segment.text.strip())
