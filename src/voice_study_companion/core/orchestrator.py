"""Provider-neutral voice-study orchestration with inert review intents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ..contracts import EvaluationResult, ReviewRating, StudyCard
from ..tutoring.contexts import (
    CardContextBuilder,
    DialogueRole,
    DialogueTurn,
    GradingContext,
    PostAnswerContext,
    PreAnswerContext,
    RevealedAnswerContext,
)
from ..tutoring.glossary import MedicalGlossary
from ..tutoring.grading import GradePolicy, GradePolicyDecision
from ..tutoring.intents import CommandKind, parse_deterministic_command
from ..tutoring.language import (
    PT_BR,
    canonical_language,
    language_tagged_segments,
    select_response_language,
)
from ..tutoring.representations import CardRepresentations
from ..tutoring.review import (
    ConfirmationSource,
    ConfirmedReview,
    DiscussionEntry,
    DiscussionProvenance,
    TutoringReviewState,
)

from .cancellation import CancellationResult, GenerationRuntime
from .domain import (
    AudioChunk,
    AudioFrame,
    AuthorizedUtterance,
    DialogueIntent,
    DialoguePlan,
    GradeResult,
    LanguageTag,
    SpeechHints,
    SpeechSegment,
    Transcript,
    TurnIdentity,
    Usage,
    UtteranceKind,
    UtterancePolicy,
)
from .ports import (
    AnswerGrader,
    DialogueModel,
    JsonValue,
    Synthesizer,
    Transcriber,
)
from .state import (
    VoiceEvent,
    VoiceEventKind,
    VoicePhase,
    VoiceState,
    VoiceStateMachine,
)


class OrchestratorResultKind(StrEnum):
    PROMPT = "prompt"
    REPEAT = "repeat"
    CLARIFICATION = "clarification"
    LANGUAGE_SWITCH = "language_switch"
    STOPPED = "stopped"
    GRADED = "graded"
    CORRECTED_GRADE = "corrected_grade"
    REVEALED = "revealed"
    OFFICIAL_ANSWER = "official_answer"
    DISCUSSION = "discussion"
    RATING_OVERRIDDEN = "rating_overridden"
    RATING_CONFIRMED = "rating_confirmed"


_UTTERANCE_POLICIES: dict[UtteranceKind, UtterancePolicy] = {
    UtteranceKind.CARD_PROMPT: UtterancePolicy.LOCKED,
    UtteranceKind.FEEDBACK: UtterancePolicy.LOCKED,
    UtteranceKind.OFFICIAL_ANSWER: UtterancePolicy.LOCKED,
    UtteranceKind.CLARIFICATION: UtterancePolicy.OPEN_BARGE_IN,
    UtteranceKind.DISCUSSION: UtterancePolicy.OPEN_BARGE_IN,
    UtteranceKind.SYSTEM: UtterancePolicy.OPEN_BARGE_IN,
}


class OrchestratorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PendingReviewAction:
    """An inert value; only a user-owned adapter may execute it."""

    card_id: str
    selection_token: str
    rating: ReviewRating
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class OrchestratorResult:
    kind: OrchestratorResultKind
    identity: TurnIdentity
    transcript: Transcript | None = None
    dialogue: DialoguePlan | None = None
    grade_decision: GradePolicyDecision | None = None
    utterance: AuthorizedUtterance | None = None
    audio_chunks: tuple[AudioChunk, ...] = ()
    usage: tuple[Usage, ...] = ()
    confirmed_review: ConfirmedReview | None = None
    pending_review: PendingReviewAction | None = None


@dataclass(frozen=True, slots=True)
class OutputLengthPolicy:
    """Keep routine speech concise while allowing explicitly requested detail."""

    concise_characters: int = 600
    requested_detail_characters: int = 4_000

    def __post_init__(self) -> None:
        if self.concise_characters < 80:
            raise ValueError("concise_output_limit_too_small")
        if self.requested_detail_characters < self.concise_characters:
            raise ValueError("detailed_output_limit_must_cover_concise_limit")

    def apply(
        self,
        segments: tuple[SpeechSegment, ...],
        *,
        detail_requested: bool,
    ) -> tuple[SpeechSegment, ...]:
        if not segments:
            raise ValueError("speech_segments_required")
        limit = (
            self.requested_detail_characters
            if detail_requested
            else self.concise_characters
        )
        if sum(len(segment.text) for segment in segments) <= limit:
            return segments
        output: list[SpeechSegment] = []
        remaining = limit
        for segment in segments:
            if remaining <= 0:
                break
            if len(segment.text) <= remaining:
                output.append(segment)
                remaining -= len(segment.text)
                continue
            clipped = _clip_semantically(segment.text, remaining)
            if clipped:
                output.append(SpeechSegment(language=segment.language, text=clipped))
            break
        return tuple(output)


@dataclass(frozen=True, slots=True)
class _ProviderContext:
    identity: TurnIdentity
    payload: Mapping[str, JsonValue]

    def to_provider_payload(self) -> Mapping[str, JsonValue]:
        return self.payload


class VoiceStudyOrchestrator:
    """Coordinate provider ports and tutoring rules without applying reviews."""

    def __init__(
        self,
        *,
        session_id: str,
        transcriber: Transcriber,
        dialogue: DialogueModel,
        post_answer_dialogue: DialogueModel | None = None,
        grader: AnswerGrader,
        synthesizer: Synthesizer,
        response_language: str | LanguageTag = PT_BR,
        context_builder: CardContextBuilder | None = None,
        glossary: MedicalGlossary | None = None,
        grade_policy: GradePolicy | None = None,
        output_policy: OutputLengthPolicy | None = None,
        audio_sink: (
            Callable[[AuthorizedUtterance, AudioChunk], Awaitable[None]] | None
        ) = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id_must_not_be_blank")
        self._session_id = session_id
        self._transcriber = transcriber
        self._dialogue = dialogue
        self._post_answer_dialogue = post_answer_dialogue or dialogue
        self._grader = grader
        self._synthesizer = synthesizer
        self._response_language = canonical_language(response_language)
        self._context_builder = context_builder or CardContextBuilder()
        self._glossary = glossary or MedicalGlossary()
        self._grade_policy = grade_policy or GradePolicy()
        self._output_policy = output_policy or OutputLengthPolicy()
        self._audio_sink = audio_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._machine = VoiceStateMachine(session_id)
        self._runtime: GenerationRuntime | None = None
        self._card: StudyCard | None = None
        self._review: TutoringReviewState | None = None
        self._grade_decision: GradePolicyDecision | None = None
        self._conventional_english_terms: tuple[str, ...] = ()
        self._pre_answer_discussion: list[DialogueTurn] = []
        self._turn_counter = 0
        self._utterance_counter = 0
        self._generation_has_input = False

    @property
    def state(self) -> VoiceState:
        return self._machine.snapshot

    @property
    def response_language(self) -> LanguageTag:
        return self._response_language

    @property
    def confirmed_review(self) -> ConfirmedReview | None:
        return self._review.confirmed_review if self._review is not None else None

    def set_audio_sink(
        self,
        sink: Callable[[AuthorizedUtterance, AudioChunk], Awaitable[None]] | None,
    ) -> None:
        """Attach the transport stream without coupling this core to WebSockets."""

        self._audio_sink = sink

    async def prepare_card(
        self,
        card: StudyCard,
        *,
        conventional_english_terms: tuple[str, ...] = (),
    ) -> OrchestratorResult:
        state = self._machine.snapshot
        if state.phase not in {VoicePhase.PREPARING_CARD, VoicePhase.ADVANCING}:
            raise OrchestratorError("card_cannot_be_prepared_from_current_phase")
        if (
            any(not term.strip() for term in conventional_english_terms)
            or len(set(conventional_english_terms))
            != len(conventional_english_terms)
        ):
            raise ValueError("conventional_english_terms_must_be_unique_text")
        card_id = card.card_id
        selection_token = card.selection_token
        allowed_ratings = card.allowed_ratings
        self._turn_counter += 1
        if state.identity is None:
            identity = TurnIdentity(
                session_id=self._session_id,
                card_id=card_id,
                turn_id=f"turn-{self._turn_counter}",
                generation_id=1,
                sequence=1,
            )
            self._runtime = GenerationRuntime(identity)
        else:
            identity = TurnIdentity(
                session_id=self._session_id,
                card_id=card_id,
                turn_id=f"turn-{self._turn_counter}",
                generation_id=state.identity.generation_id + 1,
                sequence=state.identity.sequence + 1,
            )
            assert self._runtime is not None
            self._runtime.rotate(identity)
        self._machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, identity))
        self._card = card
        self._review = TutoringReviewState(
            card_id=card_id,
            selection_token=selection_token,
            allowed_ratings=allowed_ratings,
        )
        self._grade_decision = None
        self._conventional_english_terms = tuple(conventional_english_terms)
        self._pre_answer_discussion.clear()
        self._generation_has_input = False

        pre_answer = self._pre_answer_context()
        utterance, chunks = await self._speak(
            UtteranceKind.CARD_PROMPT,
            pre_answer.question.speech_segments,
        )
        return OrchestratorResult(
            kind=OrchestratorResultKind.PROMPT,
            identity=self._current_identity(),
            utterance=utterance,
            audio_chunks=chunks,
        )

    async def transcribe_and_respond(
        self,
        pcm_frames: Iterable[bytes],
        *,
        frame_duration_ms: int = 0,
    ) -> OrchestratorResult:
        await self._start_turn_if_needed()
        self._require_phase(VoicePhase.LISTENING)
        runtime = self._require_runtime()
        identity = runtime.identity
        pre_answer = self._pre_answer_context()
        hints = SpeechHints(
            languages=(LanguageTag("pt-BR"), LanguageTag("en-US")),
            keywords=tuple(dict.fromkeys(pre_answer.question.stt_keyword_hints)),
        )
        session = await self._transcriber.open(identity, hints, runtime.cancellation)
        pushed = 0
        try:
            for data in pcm_frames:
                runtime.cancellation.raise_if_cancelled()
                await session.push(
                    AudioFrame(
                        identity=identity,
                        data=data,
                        duration_ms=frame_duration_ms,
                    )
                )
                pushed += 1
            if pushed < 1:
                raise ValueError("voice_turn_requires_audio")
            self._apply(VoiceEventKind.SPEECH_COMMITTED)
            transcript = await session.commit(runtime.cancellation)
        finally:
            await session.close()
        runtime.accept_result(transcript.identity)
        self._generation_has_input = True
        self._apply(VoiceEventKind.TRANSCRIPT_READY)
        return await self._respond_to_transcript(transcript)

    async def submit_text(
        self,
        text: str,
        *,
        detected_languages: tuple[LanguageTag, ...] = (),
    ) -> OrchestratorResult:
        if not text.strip():
            raise ValueError("submitted_text_must_not_be_blank")
        await self._start_turn_if_needed()
        self._require_phase(VoicePhase.LISTENING)
        identity = self._require_runtime().identity
        transcript = Transcript(
            identity=identity,
            raw_text=text,
            normalized_text=" ".join(text.split()),
            detected_languages=detected_languages,
            confidence=None,
            final=True,
        )
        self._apply(VoiceEventKind.SPEECH_COMMITTED)
        self._generation_has_input = True
        self._apply(VoiceEventKind.TRANSCRIPT_READY)
        return await self._respond_to_transcript(transcript)

    async def correct_transcript(self, corrected_text: str) -> OrchestratorResult:
        if not corrected_text.strip():
            raise ValueError("corrected_transcript_must_not_be_blank")
        self._require_phase(VoicePhase.AWAITING_RATING)
        review = self._require_review()
        if review.transcript is None:
            raise OrchestratorError("transcript_not_available_for_correction")
        self.cancel_active()
        identity = self._require_runtime().identity
        transcript = Transcript(
            identity=identity,
            raw_text=corrected_text,
            normalized_text=" ".join(corrected_text.split()),
            detected_languages=(),
            confidence=None,
            final=True,
        )
        self._apply(VoiceEventKind.CORRECTION_STARTED)
        self._generation_has_input = True
        review.correct_transcript(transcript.normalized_text)
        self._grade_decision = None
        return await self._grade_and_respond(
            transcript,
            kind=OrchestratorResultKind.CORRECTED_GRADE,
            dialogue=None,
            dialogue_usage=(),
            record_transcript=False,
        )

    async def transcribe_post_answer(
        self,
        pcm_frames: Iterable[bytes],
        *,
        frame_duration_ms: int = 0,
    ) -> OrchestratorResult:
        """Handle an explicitly revealed follow-up or rating command by voice.

        The grading turn has already finished, so this transcription does not
        mutate the grade or submit a review.  A recognized confirmation emits
        only an inert :class:`PendingReviewAction`; the browser/API boundary
        remains responsible for any learner-authorized adapter call.
        """

        self._require_phase(VoicePhase.AWAITING_RATING)
        runtime = self._require_runtime()
        identity = runtime.identity
        pre_answer = self._pre_answer_context()
        hints = SpeechHints(
            languages=(LanguageTag("pt-BR"), LanguageTag("en-US")),
            keywords=tuple(
                dict.fromkeys(
                    (
                        *pre_answer.question.stt_keyword_hints,
                        *self._require_card().expected_concepts,
                        "again",
                        "hard",
                        "good",
                        "easy",
                        "confirmar",
                        "próximo",
                        "resposta oficial",
                        "official answer",
                        "repita a pergunta",
                        "repeat question",
                    )
                )
            ),
        )
        session = await self._transcriber.open(
            identity,
            hints,
            runtime.cancellation,
        )
        pushed = 0
        try:
            for data in pcm_frames:
                runtime.cancellation.raise_if_cancelled()
                await session.push(
                    AudioFrame(
                        identity=identity,
                        data=data,
                        duration_ms=frame_duration_ms,
                    )
                )
                pushed += 1
            if pushed < 1:
                raise ValueError("voice_turn_requires_audio")
            transcript = await session.commit(runtime.cancellation)
        finally:
            await session.close()
        runtime.accept_result(transcript.identity)
        return await self.respond_post_answer_text(
            transcript.normalized_text,
            transcript=transcript,
        )

    async def respond_post_answer_text(
        self,
        text: str,
        *,
        transcript: Transcript | None = None,
    ) -> OrchestratorResult:
        """Route a revealed-card follow-up without granting adapter authority."""

        if not text.strip():
            raise ValueError("post_answer_text_must_not_be_blank")
        self._require_phase(VoicePhase.AWAITING_RATING)
        identity = self._require_runtime().identity
        active_transcript = transcript or Transcript(
            identity=identity,
            raw_text=text,
            normalized_text=" ".join(text.split()),
            detected_languages=(),
            confidence=None,
            final=True,
        )
        if not active_transcript.identity.same_generation(identity):
            raise OrchestratorError("post_answer_transcript_is_stale")
        command = parse_deterministic_command(
            active_transcript.normalized_text,
            response_language=self._response_language,
            post_reveal=True,
        )
        if command is not None and command.kind is CommandKind.STOP:
            return OrchestratorResult(
                kind=OrchestratorResultKind.STOPPED,
                identity=self._current_identity(),
                transcript=active_transcript,
            )
        if command is not None and command.kind is CommandKind.REPEAT:
            self._apply(VoiceEventKind.PROMPT_REPLAY_PLANNED)
            utterance, chunks = await self._speak(
                UtteranceKind.CARD_PROMPT,
                self._pre_answer_context().question.speech_segments,
            )
            return OrchestratorResult(
                kind=OrchestratorResultKind.REPEAT,
                identity=self._current_identity(),
                transcript=active_transcript,
                utterance=utterance,
                audio_chunks=chunks,
            )
        if command is not None and command.kind is CommandKind.READ_OFFICIAL_ANSWER:
            return await self.speak_official_answer(transcript=active_transcript)
        if command is not None and command.kind in {
            CommandKind.CONFIRM_RATING,
            CommandKind.OVERRIDE_RATING,
        }:
            review = self._require_review()
            confirmed = review.apply_command(
                command,
                source=ConfirmationSource.VOICE,
            )
            if confirmed is not None:
                result = self.confirm_rating(
                    source=ConfirmationSource.VOICE,
                    _already_confirmed=confirmed,
                )
                return OrchestratorResult(
                    kind=result.kind,
                    identity=result.identity,
                    transcript=active_transcript,
                    confirmed_review=result.confirmed_review,
                    pending_review=result.pending_review,
                )

            rating = review.proposed_rating
            if rating is None:
                raise OrchestratorError("rating_override_was_not_applied")
            label = {
                ReviewRating.AGAIN: "novamente",
                ReviewRating.HARD: "difícil",
                ReviewRating.GOOD: "bom",
                ReviewRating.EASY: "fácil",
            }[rating]
            self._apply(VoiceEventKind.DISCUSSION_STARTED)
            utterance, chunks = await self._speak(
                UtteranceKind.SYSTEM,
                (
                    SpeechSegment(
                        self._response_language,
                        f"Rating alterado para {label}. Diga confirmar para aplicar.",
                    ),
                ),
            )
            return OrchestratorResult(
                kind=OrchestratorResultKind.RATING_OVERRIDDEN,
                identity=self._current_identity(),
                transcript=active_transcript,
                utterance=utterance,
                audio_chunks=chunks,
            )
        return await self.discuss(active_transcript.normalized_text)

    async def discuss(
        self,
        text: str,
        *,
        detail_requested: bool = True,
    ) -> OrchestratorResult:
        if not text.strip():
            raise ValueError("discussion_text_must_not_be_blank")
        self._require_phase(VoicePhase.AWAITING_RATING)
        review = self._require_review()
        grade = review.grade
        self._apply(VoiceEventKind.DISCUSSION_STARTED)
        review.append_discussion(DiscussionEntry(DiscussionProvenance.LEARNER, text))
        identity = self._require_runtime().identity
        transcript = Transcript(
            identity=identity,
            raw_text=text,
            normalized_text=" ".join(text.split()),
            detected_languages=(),
            confidence=None,
            final=True,
        )
        discussion = tuple(
            DialogueTurn(
                DialogueRole.LEARNER
                if item.provenance is DiscussionProvenance.LEARNER
                else DialogueRole.TUTOR,
                item.text,
            )
            for item in review.discussion
        )
        card = self._require_card()
        if grade is None:
            post_answer: PostAnswerContext | RevealedAnswerContext = (
                self._context_builder.revealed_answer(
                    card,
                    response_language=self._response_language,
                    discussion=discussion,
                    conventional_english_terms=self._conventional_english_terms,
                )
            )
        else:
            post_answer = self._context_builder.post_answer(
                card,
                decision=self._require_grade_decision(),
                response_language=self._response_language,
                discussion=discussion,
                conventional_english_terms=self._conventional_english_terms,
            )
        dialogue, usage = await self._post_answer_dialogue.respond(
            _ProviderContext(identity, _post_answer_payload(post_answer)),
            transcript,
            self._require_runtime().cancellation,
        )
        self._require_runtime().accept_result(dialogue.identity)
        if dialogue.intent not in {
            DialogueIntent.DISCUSSION,
            DialogueIntent.CLARIFICATION,
        }:
            raise OrchestratorError("post_answer_model_returned_invalid_intent")
        if not dialogue.supplemental:
            raise OrchestratorError("post_answer_discussion_must_be_supplemental")
        review.append_discussion(
            DiscussionEntry(
                DiscussionProvenance.SUPPLEMENTAL,
                dialogue.display_text,
            )
        )
        segments = self._output_policy.apply(
            dialogue.speech_segments,
            detail_requested=detail_requested,
        )
        utterance, chunks = await self._speak(
            UtteranceKind.DISCUSSION,
            segments,
        )
        return OrchestratorResult(
            kind=OrchestratorResultKind.DISCUSSION,
            identity=self._current_identity(),
            transcript=transcript,
            dialogue=dialogue,
            utterance=utterance,
            audio_chunks=chunks,
            usage=(usage,),
        )

    def confirm_rating(
        self,
        *,
        source: ConfirmationSource,
        rating: ReviewRating | None = None,
        _already_confirmed: ConfirmedReview | None = None,
    ) -> OrchestratorResult:
        self._require_phase(VoicePhase.AWAITING_RATING)
        review = self._require_review()
        if _already_confirmed is None:
            if rating is not None:
                review.override_rating(rating)
            confirmed = review.confirm(source=source)
        else:
            confirmed = _already_confirmed
        self._apply(VoiceEventKind.RATING_CONFIRMED)
        pending = PendingReviewAction(
            card_id=confirmed.card_id,
            selection_token=confirmed.selection_token,
            rating=confirmed.rating,
            idempotency_key=_review_idempotency_key(
                self._session_id,
                confirmed,
            ),
        )
        return OrchestratorResult(
            kind=OrchestratorResultKind.RATING_CONFIRMED,
            identity=self._current_identity(),
            confirmed_review=confirmed,
            pending_review=pending,
        )

    def cancel_active(self) -> CancellationResult:
        state = self._machine.snapshot
        if state.identity is None:
            raise OrchestratorError("no_active_generation")
        self._turn_counter += 1
        event_identity = _with_sequence(
            state.identity,
            state.identity.sequence + 1,
        )
        next_state = self._machine.apply(
            VoiceEvent(
                VoiceEventKind.GENERATION_CANCELLED,
                event_identity,
                next_turn_id=f"turn-{self._turn_counter}",
            )
        )
        assert next_state.identity is not None
        result = self._require_runtime().rotate(next_state.identity)
        self._generation_has_input = False
        return result

    def cancel_active_for_recovery(self) -> CancellationResult:
        """Invalidate the generation and remain fail-closed for explicit recovery."""

        result = self.cancel_active()
        self._apply(VoiceEventKind.RECOVERY_STARTED)
        return result

    async def _respond_to_transcript(
        self, transcript: Transcript
    ) -> OrchestratorResult:
        card = self._require_card()
        language = select_response_language(
            transcript.normalized_text,
            session_preference=self._response_language,
            conventional_english_terms=self._conventional_english_terms,
        )
        self._response_language = language.language
        pre_answer = self._pre_answer_context()
        command = parse_deterministic_command(
            transcript.normalized_text,
            glossary=self._glossary,
            response_language=self._response_language,
            safe_context=pre_answer.question.semantic_text,
            note_key=card.card_id,
            scope=card.scope,
        )
        if command is not None:
            if command.kind is CommandKind.STOP:
                self.cancel_active()
                return OrchestratorResult(
                    kind=OrchestratorResultKind.STOPPED,
                    identity=self._current_identity(),
                    transcript=transcript,
                )
            if command.kind is CommandKind.REPEAT:
                return await self._clarify_deterministically(
                    transcript,
                    pre_answer.question.speech_segments,
                    result_kind=OrchestratorResultKind.REPEAT,
                    utterance_kind=UtteranceKind.CARD_PROMPT,
                )
            if command.kind is CommandKind.SWITCH_LANGUAGE:
                assert command.target_language is not None
                self._response_language = command.target_language
                response = (
                    "Continuarei em português."
                    if command.target_language == PT_BR
                    else "I will continue in English."
                )
                return await self._clarify_deterministically(
                    transcript,
                    (SpeechSegment(command.target_language, response),),
                    result_kind=OrchestratorResultKind.LANGUAGE_SWITCH,
                    utterance_kind=UtteranceKind.SYSTEM,
                )
            if command.kind is CommandKind.GLOSSARY_QUERY:
                assert command.response_text is not None
                assert command.target_language is not None
                return await self._clarify_deterministically(
                    transcript,
                    (
                        SpeechSegment(
                            command.target_language,
                            command.response_text,
                        ),
                    ),
                    result_kind=OrchestratorResultKind.CLARIFICATION,
                    utterance_kind=UtteranceKind.CLARIFICATION,
                )
            raise OrchestratorError("rating_command_not_expected_before_grade")

        dialogue, dialogue_usage = await self._dialogue.respond(
            _ProviderContext(
                self._require_runtime().identity,
                _pre_answer_payload(pre_answer),
            ),
            transcript,
            self._require_runtime().cancellation,
        )
        self._require_runtime().accept_result(dialogue.identity)
        if dialogue.intent is DialogueIntent.ANSWER:
            if not dialogue.requires_grading:
                raise OrchestratorError("answer_plan_must_require_grading")
            return await self._grade_and_respond(
                transcript,
                kind=OrchestratorResultKind.GRADED,
                dialogue=dialogue,
                dialogue_usage=(dialogue_usage,),
                record_transcript=True,
            )
        if dialogue.intent is DialogueIntent.REPEAT:
            segments = pre_answer.question.speech_segments
            utterance_kind = UtteranceKind.CARD_PROMPT
            result_kind = OrchestratorResultKind.REPEAT
        else:
            segments = self._output_policy.apply(
                dialogue.speech_segments,
                detail_requested=False,
            )
            utterance_kind = UtteranceKind.CLARIFICATION
            result_kind = OrchestratorResultKind.CLARIFICATION
        self._pre_answer_discussion.extend(
            (
                DialogueTurn(DialogueRole.LEARNER, transcript.normalized_text),
                DialogueTurn(DialogueRole.TUTOR, dialogue.display_text),
            )
        )
        self._apply(
            VoiceEventKind.PROMPT_REPLAY_PLANNED
            if utterance_kind is UtteranceKind.CARD_PROMPT
            else VoiceEventKind.CLARIFICATION_PLANNED
        )
        utterance, chunks = await self._speak(utterance_kind, segments)
        return OrchestratorResult(
            kind=result_kind,
            identity=self._current_identity(),
            transcript=transcript,
            dialogue=dialogue,
            utterance=utterance,
            audio_chunks=chunks,
            usage=(dialogue_usage,),
        )

    async def _clarify_deterministically(
        self,
        transcript: Transcript,
        segments: tuple[SpeechSegment, ...],
        *,
        result_kind: OrchestratorResultKind,
        utterance_kind: UtteranceKind,
    ) -> OrchestratorResult:
        self._pre_answer_discussion.append(
            DialogueTurn(DialogueRole.LEARNER, transcript.normalized_text)
        )
        self._pre_answer_discussion.append(
            DialogueTurn(
                DialogueRole.TUTOR,
                " ".join(segment.text for segment in segments),
            )
        )
        self._apply(
            VoiceEventKind.PROMPT_REPLAY_PLANNED
            if utterance_kind is UtteranceKind.CARD_PROMPT
            else VoiceEventKind.CLARIFICATION_PLANNED
        )
        utterance, chunks = await self._speak(utterance_kind, segments)
        return OrchestratorResult(
            kind=result_kind,
            identity=self._current_identity(),
            transcript=transcript,
            utterance=utterance,
            audio_chunks=chunks,
        )

    async def _grade_and_respond(
        self,
        transcript: Transcript,
        *,
        kind: OrchestratorResultKind,
        dialogue: DialoguePlan | None,
        dialogue_usage: tuple[Usage, ...],
        record_transcript: bool,
    ) -> OrchestratorResult:
        card = self._require_card()
        review = self._require_review()
        if record_transcript:
            review.record_transcript(
                transcript.raw_text,
                transcript.normalized_text,
            )
        grading_context = self._context_builder.grading(
            card,
            learner_attempt=transcript.normalized_text,
            response_language=self._response_language,
            conventional_english_terms=self._conventional_english_terms,
        )
        grade, grade_usage = await self._grader.grade(
            _ProviderContext(
                self._require_runtime().identity,
                _grading_payload(grading_context),
            ),
            transcript,
            self._require_runtime().cancellation,
        )
        self._require_runtime().accept_result(grade.identity)
        evaluation = _to_evaluation_result(grade)
        decision = self._grade_policy.evaluate(evaluation, card=card)
        self._grade_decision = decision
        review.apply_grade(decision)
        review.reveal()
        self._apply(VoiceEventKind.GRADE_READY, grade=grade)
        feedback_segments = language_tagged_segments(
            grade.explanation,
            response_language=self._response_language,
            conventional_english_terms=self._conventional_english_terms,
        )
        feedback_segments = self._output_policy.apply(
            feedback_segments,
            detail_requested=False,
        )
        utterance, chunks = await self._speak(
            UtteranceKind.FEEDBACK,
            feedback_segments,
        )
        return OrchestratorResult(
            kind=kind,
            identity=self._current_identity(),
            transcript=transcript,
            dialogue=dialogue,
            grade_decision=decision,
            utterance=utterance,
            audio_chunks=chunks,
            usage=(*dialogue_usage, grade_usage),
        )

    async def _start_turn_if_needed(self) -> None:
        if not self._generation_has_input:
            return
        state = self._machine.snapshot
        self._require_phase(VoicePhase.LISTENING)
        assert state.identity is not None
        self._turn_counter += 1
        identity = TurnIdentity(
            session_id=self._session_id,
            card_id=state.identity.card_id,
            turn_id=f"turn-{self._turn_counter}",
            generation_id=state.identity.generation_id + 1,
            sequence=state.identity.sequence + 1,
        )
        self._machine.apply(VoiceEvent(VoiceEventKind.TURN_STARTED, identity))
        self._require_runtime().rotate(identity)
        self._generation_has_input = False

    def acknowledge_playback(self, *, utterance_id: str) -> VoiceState:
        """Leave a speaking phase only after the matching local buffer drained."""

        if not utterance_id.strip():
            raise ValueError("utterance_id_must_not_be_blank")
        return self._apply(
            VoiceEventKind.PLAYBACK_DRAINED,
            utterance_id=utterance_id,
        )

    async def reveal_without_attempt(self) -> OrchestratorResult:
        """Reveal visually and wait without narrating or offering the back."""

        self._require_phase(VoicePhase.LISTENING)
        review = self._require_review()
        if review.transcript is not None or review.grade is not None:
            raise OrchestratorError("ungraded_reveal_requires_no_attempt_or_grade")
        review.reveal_without_grade()
        self._apply(VoiceEventKind.ANSWER_REVEALED)
        return OrchestratorResult(
            kind=OrchestratorResultKind.REVEALED,
            identity=self._current_identity(),
        )

    async def speak_official_answer(
        self,
        *,
        transcript: Transcript | None = None,
    ) -> OrchestratorResult:
        """Narrate the answer-only back after an explicit post-reveal request."""

        self._require_phase(VoicePhase.AWAITING_RATING)
        review = self._require_review()
        grade = review.grade
        card = self._require_card()
        post_answer: PostAnswerContext | RevealedAnswerContext
        if grade is None:
            post_answer = self._context_builder.revealed_answer(
                card,
                response_language=self._response_language,
                conventional_english_terms=self._conventional_english_terms,
            )
        else:
            post_answer = self._context_builder.post_answer(
                card,
                decision=self._require_grade_decision(),
                response_language=self._response_language,
                conventional_english_terms=self._conventional_english_terms,
            )
        self._apply(VoiceEventKind.OFFICIAL_ANSWER_PLANNED)
        utterance, chunks = await self._speak(
            UtteranceKind.OFFICIAL_ANSWER,
            post_answer.official_answer.speech_segments,
        )
        return OrchestratorResult(
            kind=OrchestratorResultKind.OFFICIAL_ANSWER,
            identity=self._current_identity(),
            transcript=transcript,
            utterance=utterance,
            audio_chunks=chunks,
        )

    async def _speak(
        self,
        kind: UtteranceKind,
        segments: tuple[SpeechSegment, ...],
    ) -> tuple[AuthorizedUtterance, tuple[AudioChunk, ...]]:
        runtime = self._require_runtime()
        runtime.cancellation.raise_if_cancelled()
        self._utterance_counter += 1
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("orchestrator_clock_must_be_timezone_aware")
        utterance = AuthorizedUtterance(
            utterance_id=f"{self._session_id}-utterance-{self._utterance_counter}",
            identity=runtime.identity,
            kind=kind,
            segments=segments,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            policy=_UTTERANCE_POLICIES[kind],
        )
        self._apply(
            VoiceEventKind.PLAYBACK_STARTED,
            utterance_id=utterance.utterance_id,
            utterance_policy=utterance.policy,
        )
        chunks: list[AudioChunk] = []
        final_chunk: AudioChunk | None = None
        async for chunk in self._synthesizer.stream(utterance, runtime.cancellation):
            runtime.accept_result(chunk.identity)
            final_chunk = chunk
            if self._audio_sink is None:
                chunks.append(chunk)
            else:
                await self._audio_sink(utterance, chunk)
        if final_chunk is None or not final_chunk.final:
            raise OrchestratorError("synthesizer_returned_incomplete_audio")
        self._apply(
            VoiceEventKind.PLAYBACK_PRODUCER_FINISHED,
            utterance_id=utterance.utterance_id,
        )
        return utterance, tuple(chunks)

    def _pre_answer_context(self) -> PreAnswerContext:
        return self._context_builder.pre_answer(
            self._require_card(),
            response_language=self._response_language,
            discussion=tuple(self._pre_answer_discussion),
            conventional_english_terms=(
                self._conventional_english_terms
            ),
        )

    def _apply(
        self,
        kind: VoiceEventKind,
        *,
        grade: GradeResult | None = None,
        utterance_id: str | None = None,
        utterance_policy: UtterancePolicy | None = None,
    ) -> VoiceState:
        identity = self._current_identity()
        event_identity = _with_sequence(identity, identity.sequence + 1)
        return self._machine.apply(
            VoiceEvent(
                kind=kind,
                identity=event_identity,
                grade=grade,
                utterance_id=utterance_id,
                utterance_policy=utterance_policy,
            )
        )

    def _current_identity(self) -> TurnIdentity:
        identity = self._machine.snapshot.identity
        if identity is None:
            raise OrchestratorError("no_active_identity")
        return identity

    def _require_runtime(self) -> GenerationRuntime:
        if self._runtime is None:
            raise OrchestratorError("no_active_generation")
        return self._runtime

    def _require_card(self) -> StudyCard:
        if self._card is None:
            raise OrchestratorError("no_active_card")
        return self._card

    def _require_review(self) -> TutoringReviewState:
        if self._review is None:
            raise OrchestratorError("no_active_review")
        return self._review

    def _require_grade_decision(self) -> GradePolicyDecision:
        if self._grade_decision is None:
            raise OrchestratorError("no_active_grade_decision")
        return self._grade_decision

    def _require_phase(self, phase: VoicePhase) -> None:
        if self._machine.snapshot.phase is not phase:
            raise OrchestratorError(f"operation_requires_{phase.value}_phase")


def _representations_payload(
    value: CardRepresentations,
) -> dict[str, JsonValue]:
    return {
        "display_text": value.display_text,
        "semantic_text": value.semantic_text,
        "speech_segments": [
            {"language": segment.language.value, "text": segment.text}
            for segment in value.speech_segments
        ],
        "stt_keyword_hints": list(value.stt_keyword_hints),
    }


def _pre_answer_payload(context: PreAnswerContext) -> dict[str, JsonValue]:
    return {
        "phase": "pre_answer",
        "question": _representations_payload(context.question),
        "metadata": {
            "scope": context.metadata.scope,
            "tags": list(context.metadata.tags),
            "languages": [
                language.value for language in context.metadata.languages
            ],
            "media_ids": list(context.metadata.media_ids),
        },
        "discussion": [
            {"role": item.role.value, "text": item.text} for item in context.discussion
        ],
    }


def _grading_payload(context: GradingContext) -> dict[str, JsonValue]:
    return {
        "phase": "grading",
        "question": _representations_payload(context.question),
        "official_answer": _representations_payload(context.official_answer),
        "expected_concepts": list(context.expected_concepts),
        "learner_attempt": context.learner_attempt,
        "response_language": context.response_language.value,
    }


def _post_answer_payload(
    context: PostAnswerContext | RevealedAnswerContext,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "phase": "post_answer",
        "question": _representations_payload(context.question),
        "official_answer": _representations_payload(context.official_answer),
        "grade": None,
        "discussion": [
            {"role": item.role.value, "text": item.text} for item in context.discussion
        ],
    }
    if isinstance(context, PostAnswerContext):
        payload["grade"] = {
            "verdict": context.grade.verdict.value,
            "covered_concepts": list(context.grade.covered_concepts),
            "missing_concepts": list(context.grade.missing_concepts),
            "incorrect_concepts": list(context.grade.incorrect_concepts),
            "confidence": context.grade.confidence,
            "feedback": context.grade.feedback,
            "proposed_rating": (
                context.grade.proposed_rating.value
                if context.grade.proposed_rating is not None
                else None
            ),
        }
    return payload


def _to_evaluation_result(grade: GradeResult) -> EvaluationResult:
    return EvaluationResult(
        turn_id=grade.identity.turn_id,
        verdict=grade.verdict,
        feedback=grade.explanation,
        covered_concepts=grade.covered_concepts,
        missing_concepts=grade.missing_concepts,
        incorrect_concepts=grade.incorrect_concepts,
        confidence=grade.confidence,
        proposed_rating=grade.proposed_rating,
    )


def _with_sequence(identity: TurnIdentity, sequence: int) -> TurnIdentity:
    return TurnIdentity(
        session_id=identity.session_id,
        card_id=identity.card_id,
        turn_id=identity.turn_id,
        generation_id=identity.generation_id,
        sequence=sequence,
    )


def _clip_semantically(text: str, limit: int) -> str:
    if limit < 1:
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    minimum = max(1, limit // 2)
    candidates = [
        window.rfind(marker, minimum)
        for marker in (". ", "? ", "! ", "; ", ": ", "\n", " ")
    ]
    cut = max(candidates)
    if cut < minimum:
        cut = limit
    elif window[cut : cut + 2] in {". ", "? ", "! ", "; ", ": "}:
        cut += 1
    return window[:cut].strip()


def _review_idempotency_key(
    session_id: str,
    review: ConfirmedReview,
) -> str:
    canonical = json.dumps(
        {
            "session_id": session_id,
            "card_id": review.card_id,
            "selection_token": review.selection_token,
            "rating": review.rating.value,
            "transcript_revision": review.transcript.revision,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
