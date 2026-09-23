"""Deterministic controls and fail-closed fallback intent parsing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from voice_study_companion.contracts import LanguageTag, ReviewRating
from voice_study_companion.core.domain import DialogueIntent
from voice_study_companion.tutoring.glossary import (
    MedicalGlossary,
    ResolutionStatus,
)
from voice_study_companion.tutoring.language import (
    PT_BR,
    canonical_language,
    explicit_language_request,
)


class CommandKind(StrEnum):
    STOP = "stop"
    REPEAT = "repeat"
    READ_OFFICIAL_ANSWER = "read_official_answer"
    SWITCH_LANGUAGE = "switch_language"
    CONFIRM_RATING = "confirm_rating"
    OVERRIDE_RATING = "override_rating"
    GLOSSARY_QUERY = "glossary_query"


@dataclass(frozen=True, slots=True)
class DeterministicCommand:
    """A locally recognized command with no direct external side effect."""

    kind: CommandKind
    normalized_text: str
    response_text: str | None = None
    target_language: LanguageTag | None = None
    requested_rating: ReviewRating | None = None
    glossary_surface: str | None = None

    @property
    def requires_tutor_call(self) -> bool:
        return False

    @property
    def can_reveal(self) -> bool:
        return False

    @property
    def can_grade(self) -> bool:
        return False

    @property
    def can_submit_review(self) -> bool:
        # A phrase can update local review state, but it can never invoke an
        # adapter or persist a rating by itself.
        return False

    @property
    def can_advance(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: DialogueIntent | None
    confidence: float
    needs_clarification: bool
    clarification_question: str | None

    @property
    def can_grade(self) -> bool:
        return not self.needs_clarification and self.intent is DialogueIntent.ANSWER

    @property
    def can_submit_review(self) -> bool:
        return False


class IntentSchemaError(ValueError):
    """Raised when a fallback classifier response violates its exact schema."""


_STOP = frozenset(
    {
        "cancel",
        "cancelar",
        "pare",
        "para",
        "stop",
        "stop audio",
        "stop speaking",
        "pare de falar",
    }
)
_REPEAT = frozenset(
    {
        "de novo",
        "novamente",
        "pode repetir",
        "repita",
        "repita a pergunta",
        "releia a pergunta",
        "leia a pergunta novamente",
        "repeat",
        "repeat question",
        "repeat the question",
        "say it again",
    }
)
_CONFIRM = frozenset(
    {
        "confirm",
        "confirmar",
        "confirmo",
        "pode marcar",
        "sim, confirmar",
        "sim confirmar",
        "yes confirm",
    }
)
_RATING_WORDS: Mapping[str, ReviewRating] = {
    "again": ReviewRating.AGAIN,
    "de novo": ReviewRating.AGAIN,
    "errei": ReviewRating.AGAIN,
    "hard": ReviewRating.HARD,
    "difícil": ReviewRating.HARD,
    "dificil": ReviewRating.HARD,
    "good": ReviewRating.GOOD,
    "bom": ReviewRating.GOOD,
    "easy": ReviewRating.EASY,
    "fácil": ReviewRating.EASY,
    "facil": ReviewRating.EASY,
}
_RATING_COMMAND = re.compile(
    r"^(?:marque|marca|use|rate|set(?:\s+rating)?(?:\s+to)?)\s+(.+)$",
    re.IGNORECASE,
)
_GLOSSARY_QUERY = re.compile(
    r"^(?:o\s+que\s+(?:é|significa)|qual\s+o\s+significado\s+de|"
    r"what\s+(?:is|does)|define)\s+([A-Za-z][A-Za-z0-9.-]{0,15})"
    r"(?:\s+mean)?$",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r"[\s?.!,;:]+$")
_SPOKEN_CONTROL_PUNCTUATION = re.compile(r"[\s?.!,;:]+")
_INTENT_FIELDS = frozenset({"intent", "confidence", "clarification_question"})

_QUESTION_OBJECT = r"(?:pergunta|quest[aã]o|enunciado)"
_OFFICIAL_ANSWER_OBJECT = (
    r"(?:resposta(?: oficial| do cart[aã]o)?|gabarito|verso(?: do cart[aã]o)?)"
)
_ENGLISH_QUESTION_OBJECT = r"(?:question|prompt)"
_ENGLISH_ANSWER_OBJECT = (
    r"(?:(?:the )?(?:official answer|answer|card answer|answer side|card back)"
    r"|(?:the )?back of the card)"
)
_PORTUGUESE_OFFICIAL_ANSWER_IMPERATIVE = (
    rf"(?:(?:voc[eê] )?(?:pode|poderia) )?(?:me )?"
    rf"(?:leia|ler|releia|diga|dizer|fala|fale|falar) "
    rf"(?:(?:a|o) )?{_OFFICIAL_ANSWER_OBJECT}"
)
_EXPLICIT_QUESTION_REPEAT_PATTERN = re.compile(
    rf"^(?:(?:ok|okay) )?(?:"
    rf"(?:(?:voc[eê] )?(?:pode|poderia) (?:me )?)?"
    rf"(?:repita|repete|releia|leia de novo|leia novamente) "
    rf"(?:a )?{_QUESTION_OBJECT}"
    rf"|(?:(?:can|could|would) you )?(?:repeat|reread|read again) "
    rf"(?:the )?{_ENGLISH_QUESTION_OBJECT}"
    rf")$",
    re.IGNORECASE,
)
_EXPLICIT_OFFICIAL_ANSWER_PATTERN = re.compile(
    rf"^(?:(?:ok|okay) )?(?:"
    rf"{_PORTUGUESE_OFFICIAL_ANSWER_IMPERATIVE}"
    rf"|(?:me )?(?:diga|fale) qual (?:[ée] )?"
    rf"(?:(?:a|o) )?{_OFFICIAL_ANSWER_OBJECT}"
    rf"|(?:quero|gostaria de) (?:ouvir|escutar|saber) "
    rf"(?:qual [ée] )?(?:(?:a|o) )?{_OFFICIAL_ANSWER_OBJECT}"
    rf"|qual (?:[ée] )?(?:(?:a|o) )?{_OFFICIAL_ANSWER_OBJECT}"
    rf"|(?:(?:can|could|would) you )?"
    rf"(?:(?:read|reread)(?: me| out)?|say) {_ENGLISH_ANSWER_OBJECT}"
    rf"|(?:(?:can|could|would) you )?tell me {_ENGLISH_ANSWER_OBJECT}"
    rf"|let me hear {_ENGLISH_ANSWER_OBJECT}"
    rf"|(?:i want|i would like) to (?:hear|know) {_ENGLISH_ANSWER_OBJECT}"
    rf"|what(?: is|s) {_ENGLISH_ANSWER_OBJECT}"
    rf")$",
    re.IGNORECASE,
)
_EXPLICIT_OFFICIAL_ANSWER_ELLIPSIS_PATTERN = re.compile(
    rf"^(?:(?:ok|okay) )?(?:(?:e )?(?:da[ií]|ent[aã]o)|agora) "
    rf"(?:(?:a|o) )?{_OFFICIAL_ANSWER_OBJECT}$",
    re.IGNORECASE,
)
_EXPLICIT_OFFICIAL_ANSWER_IMPERATIVE_PATTERN = re.compile(
    rf"^{_PORTUGUESE_OFFICIAL_ANSWER_IMPERATIVE}$",
    re.IGNORECASE,
)
_EXPLICIT_ANSWER_ABANDONMENT_PATTERN = re.compile(
    r"^(?:(?:considere|considera)|(?:(?:pode|poderia) considerar)) "
    r"(?:(?:(?:a|o) )?(?:(?:minha|meu) )?(?:resposta|tentativa) )?"
    r"(?:equivocada|equivocado|errada|errado|incorreta|incorreto)$",
    re.IGNORECASE,
)
_OFFICIAL_ANSWER_TOOL_REFERENCE_PATTERN = re.compile(
    r"\b(?:resposta oficial|resposta do cart[aã]o|gabarito|"
    r"verso(?: do cart[aã]o)?|official answer|card answer|answer side|"
    r"card back|back of the card)\b",
    re.IGNORECASE,
)
_OFFICIAL_ANSWER_TOOL_UNSAFE_CONTEXT_PATTERN = re.compile(
    r"\b(?:n[aã]o|nunca|jamais|not|never|do not|don[' ]?t|"
    r"cannot|can[' ]?t)\b|"
    r"(?:^|\b)(?:se|caso|quando|if|when)\b|"
    r"\b(?:disse|dizia|falei|falou|disser|said|told)\b|"
    r"\b(?:por que|porque|why|explique|explica|explicar|explain|sobre|about)\b",
    re.IGNORECASE,
)
_SPOKEN_SENTENCE_BOUNDARY = re.compile(r"[.!?;]+")
_EXPLICIT_REVEAL_PATTERN = re.compile(
    r"^(?:(?:(?:voc[eê] )?(?:pode|poderia) )?"
    r"(?:revele|revelar|mostre|mostrar) (?:a )?"
    r"(?:resposta(?: oficial)?|gabarito|verso)"
    r"|(?:(?:can|could|would) you )?(?:reveal|show me) "
    r"(?:the )?(?:answer|official answer|card answer))$",
    re.IGNORECASE,
)
_EXPLICIT_NEXT_CARD = frozenset(
    {
        "próximo",
        "proximo",
        "próximo cartão",
        "proximo cartao",
        "próxima pergunta",
        "proxima pergunta",
        "pode ir para o próximo cartão",
        "pode ir para o proximo cartao",
        "pode passar para o próximo cartão",
        "pode passar para o proximo cartao",
        "vamos para o próximo cartão",
        "vamos para o proximo cartao",
        "next",
        "next card",
        "go to the next card",
        "move to the next card",
    }
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _PUNCTUATION.sub("", text.strip())).casefold()


def _normalize_spoken_control(text: str) -> str:
    normalized = text.casefold().replace("’", "'")
    normalized = re.sub(r"\b(?:por favor|please)\b", " ", normalized)
    normalized = normalized.replace("what's", "what is")
    return _SPOKEN_CONTROL_PUNCTUATION.sub(" ", normalized).strip()


def is_explicit_question_repeat_request(text: str) -> bool:
    """Return true only when the learner explicitly names the question."""

    return bool(
        _EXPLICIT_QUESTION_REPEAT_PATTERN.fullmatch(_normalize_spoken_control(text))
    )


def is_explicit_official_answer_request(text: str) -> bool:
    """Recognize a positive request to hear the answer, never nearby prose."""

    normalized = _normalize_spoken_control(text)
    if _EXPLICIT_OFFICIAL_ANSWER_PATTERN.fullmatch(
        normalized
    ) or _EXPLICIT_OFFICIAL_ANSWER_ELLIPSIS_PATTERN.fullmatch(normalized):
        return True

    sentences = tuple(
        sentence.strip()
        for sentence in _SPOKEN_SENTENCE_BOUNDARY.split(text)
        if sentence.strip()
    )
    if not sentences:
        return False
    final_sentence = sentences[-1]
    if len(sentences) > 1 and _EXPLICIT_OFFICIAL_ANSWER_IMPERATIVE_PATTERN.fullmatch(
        _normalize_spoken_control(final_sentence)
    ):
        return True

    clauses = tuple(clause.strip() for clause in final_sentence.split(","))
    return bool(
        len(clauses) == 2
        and _EXPLICIT_ANSWER_ABANDONMENT_PATTERN.fullmatch(
            _normalize_spoken_control(clauses[0])
        )
        and _EXPLICIT_OFFICIAL_ANSWER_IMPERATIVE_PATTERN.fullmatch(
            _normalize_spoken_control(clauses[1])
        )
    )


def is_safe_official_answer_tool_context(text: str) -> bool:
    """Validate transcript evidence for a provider-confirmed read tool call.

    This is only the textual half of a two-signal authorization. Callers must
    already be handling an answer-reading tool request after reveal.
    """

    if is_explicit_official_answer_request(text):
        return True
    normalized = _normalize_spoken_control(text)
    return bool(
        _OFFICIAL_ANSWER_TOOL_REFERENCE_PATTERN.search(normalized)
        and not _OFFICIAL_ANSWER_TOOL_UNSAFE_CONTEXT_PATTERN.search(normalized)
    )


def is_explicit_reveal_request(text: str) -> bool:
    """Recognize an explicit request to reveal the answer on screen."""

    return bool(_EXPLICIT_REVEAL_PATTERN.fullmatch(_normalize_spoken_control(text)))


def is_explicit_next_card_request(text: str) -> bool:
    """Recognize an explicit manual request to advance after review."""

    return _normalize_spoken_control(text) in _EXPLICIT_NEXT_CARD


def _glossary_response(
    surface: str,
    *,
    glossary: MedicalGlossary,
    language: LanguageTag,
    context: str,
    note_key: str | int | None,
    scope: str | None,
) -> str | None:
    resolution = glossary.resolve(
        surface,
        context=context,
        note_key=note_key,
        scope=scope,
    )
    if resolution.status is ResolutionStatus.UNKNOWN:
        return None
    if resolution.status is ResolutionStatus.RESOLVED:
        expansion = resolution.expansion(language.value)
        if language == PT_BR:
            return f"{surface} significa {expansion}."
        return f"{surface} means {expansion}."
    meanings = tuple(
        sense.expansion(language.value) for sense in resolution.alternatives
    )
    alternatives = ", ".join(meanings)
    if language == PT_BR:
        return (
            f"{surface} é ambígua neste contexto; pode significar {alternatives}. "
            "Pode dar mais contexto?"
        )
    return (
        f"{surface} is ambiguous here; it can mean {alternatives}. "
        "Can you provide more context?"
    )


def parse_deterministic_command(
    text: str,
    *,
    glossary: MedicalGlossary | None = None,
    response_language: str | LanguageTag = PT_BR,
    safe_context: str = "",
    note_key: str | int | None = None,
    scope: str | None = None,
    post_reveal: bool = False,
) -> DeterministicCommand | None:
    """Recognize only narrow, consequence-free bilingual control phrases."""

    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in _STOP:
        return DeterministicCommand(CommandKind.STOP, normalized)
    if is_explicit_question_repeat_request(text) or (
        not post_reveal and normalized in _REPEAT
    ):
        return DeterministicCommand(CommandKind.REPEAT, normalized)
    if post_reveal and is_explicit_official_answer_request(text):
        return DeterministicCommand(CommandKind.READ_OFFICIAL_ANSWER, normalized)

    requested_language = explicit_language_request(text)
    if requested_language is not None and re.search(
        r"\b(?:continue|fale|responda|explique|speak|answer|explain)\b",
        normalized,
    ):
        return DeterministicCommand(
            CommandKind.SWITCH_LANGUAGE,
            normalized,
            target_language=requested_language,
        )

    if normalized in _CONFIRM:
        return DeterministicCommand(CommandKind.CONFIRM_RATING, normalized)
    rating_match = _RATING_COMMAND.fullmatch(normalized)
    if rating_match is not None:
        rating = _RATING_WORDS.get(rating_match.group(1).strip())
        if rating is not None:
            return DeterministicCommand(
                CommandKind.OVERRIDE_RATING,
                normalized,
                requested_rating=rating,
            )

    query_text = re.sub(r"\s+", " ", _PUNCTUATION.sub("", text.strip()))
    glossary_match = _GLOSSARY_QUERY.fullmatch(query_text)
    if glossary_match is not None:
        active_glossary = glossary or MedicalGlossary()
        surface = glossary_match.group(1)
        language = canonical_language(response_language)
        response = _glossary_response(
            surface,
            glossary=active_glossary,
            language=language,
            context=safe_context,
            note_key=note_key,
            scope=scope,
        )
        if response is not None:
            return DeterministicCommand(
                CommandKind.GLOSSARY_QUERY,
                normalized,
                response_text=response,
                target_language=language,
                glossary_surface=surface,
            )
    return None


def parse_fallback_intent(
    raw: Mapping[str, object],
    *,
    confidence_threshold: float = 0.72,
    response_language: str | LanguageTag = PT_BR,
) -> IntentDecision:
    """Validate a model classifier result and fail closed on ambiguity."""

    if frozenset(raw) != _INTENT_FIELDS:
        raise IntentSchemaError("intent_invalid_fields")
    raw_intent = raw["intent"]
    if not isinstance(raw_intent, str):
        raise IntentSchemaError("intent_invalid_value")
    try:
        intent = DialogueIntent(raw_intent)
    except ValueError as exc:
        raise IntentSchemaError("intent_invalid_value") from exc
    raw_confidence = raw["confidence"]
    if isinstance(raw_confidence, bool) or not isinstance(
        raw_confidence,
        int | float,
    ):
        raise IntentSchemaError("intent_invalid_confidence")
    confidence = float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise IntentSchemaError("intent_invalid_confidence")
    question = raw["clarification_question"]
    if question is not None and (
        not isinstance(question, str) or not question.strip()
    ):
        raise IntentSchemaError("intent_invalid_clarification_question")

    if confidence < confidence_threshold:
        language = canonical_language(response_language)
        fallback = (
            "Isso foi uma resposta ao cartão ou uma pergunta de esclarecimento?"
            if language == PT_BR
            else "Was that an answer to the card or a clarification question?"
        )
        return IntentDecision(
            intent=None,
            confidence=confidence,
            needs_clarification=True,
            clarification_question=question or fallback,
        )
    if question is not None:
        raise IntentSchemaError("intent_unexpected_clarification_question")
    return IntentDecision(
        intent=intent,
        confidence=confidence,
        needs_clarification=False,
        clarification_question=None,
    )
