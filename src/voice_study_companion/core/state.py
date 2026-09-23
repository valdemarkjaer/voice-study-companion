"""Explicit, generation-aware lifecycle for a voice-study card."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import NoReturn

from .domain import GradeResult, TurnIdentity, UtterancePolicy


class VoicePhase(StrEnum):
    PREPARING_CARD = "preparing_card"
    SPEAKING_PROMPT = "speaking_prompt"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    CLARIFYING = "clarifying"
    SPEAKING_FEEDBACK = "speaking_feedback"
    SPEAKING_OFFICIAL_ANSWER = "speaking_official_answer"
    AWAITING_RATING = "awaiting_rating"
    DISCUSSING = "discussing"
    PLAYING_CARD_MEDIA = "playing_card_media"
    ADVANCING = "advancing"
    PAUSED = "paused"
    RECOVERING = "recovering"
    CLOSED = "closed"


class VoiceEventKind(StrEnum):
    CARD_READY = "card_ready"
    ANSWER_REVEALED = "answer_revealed"
    TURN_STARTED = "turn_started"
    SPEECH_COMMITTED = "speech_committed"
    TRANSCRIPT_READY = "transcript_ready"
    CORRECTION_STARTED = "correction_started"
    PROMPT_REPLAY_PLANNED = "prompt_replay_planned"
    OFFICIAL_ANSWER_PLANNED = "official_answer_planned"
    CLARIFICATION_PLANNED = "clarification_planned"
    GRADE_READY = "grade_ready"
    DISCUSSION_STARTED = "discussion_started"
    PLAYBACK_STARTED = "playback_started"
    PLAYBACK_PRODUCER_FINISHED = "playback_producer_finished"
    PLAYBACK_DRAINED = "playback_drained"
    RATING_CONFIRMED = "rating_confirmed"
    MEDIA_STARTED = "media_started"
    MEDIA_FINISHED = "media_finished"
    PAUSED = "paused"
    RESUMED = "resumed"
    RECOVERY_STARTED = "recovery_started"
    RECOVERED = "recovered"
    GENERATION_CANCELLED = "generation_cancelled"
    CLOSED = "closed"


class StateMachineError(RuntimeError):
    pass


class InvalidTransitionError(StateMachineError):
    pass


class StaleEventError(StateMachineError):
    pass


class DuplicateEventError(StateMachineError):
    pass


class OutOfOrderEventError(StateMachineError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    kind: VoiceEventKind
    identity: TurnIdentity
    grade: GradeResult | None = None
    next_turn_id: str | None = None
    utterance_id: str | None = None
    utterance_policy: UtterancePolicy | None = None

    def __post_init__(self) -> None:
        if self.kind is VoiceEventKind.GRADE_READY and self.grade is None:
            raise ValueError("grade_ready_event_requires_grade")
        if self.kind is not VoiceEventKind.GRADE_READY and self.grade is not None:
            raise ValueError("grade_only_allowed_on_grade_ready_event")
        if self.kind is VoiceEventKind.GENERATION_CANCELLED:
            if self.next_turn_id is None or not self.next_turn_id.strip():
                raise ValueError("cancel_event_requires_next_turn_id")
        elif self.next_turn_id is not None:
            raise ValueError("next_turn_id_only_allowed_on_cancel_event")
        if self.kind is VoiceEventKind.PLAYBACK_STARTED:
            if self.utterance_id is None or not self.utterance_id.strip():
                raise ValueError("playback_started_requires_utterance_id")
            if self.utterance_policy is None:
                raise ValueError("playback_started_requires_utterance_policy")
        elif self.kind in {
            VoiceEventKind.PLAYBACK_PRODUCER_FINISHED,
            VoiceEventKind.PLAYBACK_DRAINED,
        }:
            if self.utterance_id is None or not self.utterance_id.strip():
                raise ValueError("playback_event_requires_utterance_id")
            if self.utterance_policy is not None:
                raise ValueError("playback_policy_only_allowed_on_start")
        elif self.utterance_id is not None or self.utterance_policy is not None:
            raise ValueError("playback_metadata_only_allowed_on_playback_events")


@dataclass(frozen=True, slots=True)
class PlaybackBoundary:
    generation_id: int
    utterance_id: str
    policy: UtterancePolicy
    producer_complete: bool = False


@dataclass(frozen=True, slots=True)
class VoiceState:
    session_id: str
    phase: VoicePhase = VoicePhase.PREPARING_CARD
    identity: TurnIdentity | None = None
    grade: GradeResult | None = None
    resume_phase: VoicePhase | None = None
    playback: PlaybackBoundary | None = None


_SIMPLE_TRANSITIONS: dict[VoiceEventKind, tuple[frozenset[VoicePhase], VoicePhase]] = {
    VoiceEventKind.ANSWER_REVEALED: (
        frozenset({VoicePhase.LISTENING}),
        VoicePhase.AWAITING_RATING,
    ),
    VoiceEventKind.SPEECH_COMMITTED: (
        frozenset({VoicePhase.LISTENING}),
        VoicePhase.TRANSCRIBING,
    ),
    VoiceEventKind.TRANSCRIPT_READY: (
        frozenset({VoicePhase.TRANSCRIBING}),
        VoicePhase.THINKING,
    ),
    VoiceEventKind.CORRECTION_STARTED: (
        frozenset({VoicePhase.LISTENING, VoicePhase.AWAITING_RATING}),
        VoicePhase.THINKING,
    ),
    VoiceEventKind.CLARIFICATION_PLANNED: (
        frozenset({VoicePhase.THINKING}),
        VoicePhase.CLARIFYING,
    ),
    VoiceEventKind.OFFICIAL_ANSWER_PLANNED: (
        frozenset({VoicePhase.AWAITING_RATING}),
        VoicePhase.SPEAKING_OFFICIAL_ANSWER,
    ),
    VoiceEventKind.GRADE_READY: (
        frozenset({VoicePhase.THINKING}),
        VoicePhase.SPEAKING_FEEDBACK,
    ),
    VoiceEventKind.DISCUSSION_STARTED: (
        frozenset({VoicePhase.AWAITING_RATING}),
        VoicePhase.DISCUSSING,
    ),
    VoiceEventKind.RATING_CONFIRMED: (
        frozenset({VoicePhase.AWAITING_RATING}),
        VoicePhase.ADVANCING,
    ),
    VoiceEventKind.RECOVERY_STARTED: (
        frozenset(
            phase
            for phase in VoicePhase
            if phase not in {VoicePhase.CLOSED, VoicePhase.RECOVERING}
        ),
        VoicePhase.RECOVERING,
    ),
    VoiceEventKind.CLOSED: (
        frozenset(phase for phase in VoicePhase if phase is not VoicePhase.CLOSED),
        VoicePhase.CLOSED,
    ),
}


class VoiceStateMachine:
    """Serializes canonical events and rejects every stale async result."""

    def __init__(self, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("session_id_must_not_be_blank")
        self._state = VoiceState(session_id=session_id)
        self._lock = RLock()

    @property
    def snapshot(self) -> VoiceState:
        with self._lock:
            return self._state

    def apply(self, event: VoiceEvent) -> VoiceState:
        with self._lock:
            state = self._state
            self._validate_sequence(state, event)
            self._validate_identity(state, event)

            if event.kind is VoiceEventKind.CARD_READY:
                if state.phase not in {
                    VoicePhase.PREPARING_CARD,
                    VoicePhase.ADVANCING,
                }:
                    self._invalid(state, event)
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=VoicePhase.SPEAKING_PROMPT,
                    identity=event.identity,
                )
            elif event.kind is VoiceEventKind.TURN_STARTED:
                if state.phase is not VoicePhase.LISTENING:
                    self._invalid(state, event)
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=VoicePhase.LISTENING,
                    identity=event.identity,
                    grade=state.grade,
                )
            elif event.kind is VoiceEventKind.PLAYBACK_STARTED:
                if state.phase not in {
                    VoicePhase.SPEAKING_PROMPT,
                    VoicePhase.CLARIFYING,
                    VoicePhase.SPEAKING_FEEDBACK,
                    VoicePhase.SPEAKING_OFFICIAL_ANSWER,
                    VoicePhase.DISCUSSING,
                }:
                    self._invalid(state, event)
                if state.playback is not None:
                    raise DuplicateEventError("playback_is_already_active")
                assert event.utterance_id is not None
                assert event.utterance_policy is not None
                expected_policy = {
                    VoicePhase.SPEAKING_PROMPT: UtterancePolicy.LOCKED,
                    VoicePhase.CLARIFYING: UtterancePolicy.OPEN_BARGE_IN,
                    VoicePhase.SPEAKING_FEEDBACK: UtterancePolicy.LOCKED,
                    VoicePhase.SPEAKING_OFFICIAL_ANSWER: UtterancePolicy.LOCKED,
                    VoicePhase.DISCUSSING: UtterancePolicy.OPEN_BARGE_IN,
                }[state.phase]
                if event.utterance_policy is not expected_policy:
                    raise InvalidTransitionError(
                        f"{event.utterance_policy.value}_not_allowed_for_"
                        f"{state.phase.value}"
                    )
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=state.phase,
                    identity=event.identity,
                    grade=state.grade,
                    resume_phase=state.resume_phase,
                    playback=PlaybackBoundary(
                        generation_id=event.identity.generation_id,
                        utterance_id=event.utterance_id,
                        policy=event.utterance_policy,
                    ),
                )
            elif event.kind is VoiceEventKind.PLAYBACK_PRODUCER_FINISHED:
                playback = self._matching_playback(state, event)
                if playback.producer_complete:
                    raise DuplicateEventError("playback_producer_already_finished")
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=state.phase,
                    identity=event.identity,
                    grade=state.grade,
                    resume_phase=state.resume_phase,
                    playback=PlaybackBoundary(
                        generation_id=playback.generation_id,
                        utterance_id=playback.utterance_id,
                        policy=playback.policy,
                        producer_complete=True,
                    ),
                )
            elif event.kind is VoiceEventKind.PLAYBACK_DRAINED:
                playback = self._matching_playback(state, event)
                if not playback.producer_complete:
                    raise InvalidTransitionError(
                        "playback_drained_before_producer_finished"
                    )
                target_by_phase = {
                    VoicePhase.SPEAKING_PROMPT: (
                        state.resume_phase or VoicePhase.LISTENING
                    ),
                    VoicePhase.CLARIFYING: VoicePhase.LISTENING,
                    VoicePhase.SPEAKING_FEEDBACK: VoicePhase.AWAITING_RATING,
                    VoicePhase.SPEAKING_OFFICIAL_ANSWER: VoicePhase.AWAITING_RATING,
                    VoicePhase.DISCUSSING: VoicePhase.AWAITING_RATING,
                }
                target = target_by_phase.get(state.phase)
                if target is None:
                    self._invalid(state, event)
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=target,
                    identity=event.identity,
                    grade=state.grade,
                )
            elif event.kind is VoiceEventKind.PROMPT_REPLAY_PLANNED:
                if state.phase not in {
                    VoicePhase.THINKING,
                    VoicePhase.AWAITING_RATING,
                }:
                    self._invalid(state, event)
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=VoicePhase.SPEAKING_PROMPT,
                    identity=event.identity,
                    grade=state.grade,
                    resume_phase=(
                        VoicePhase.AWAITING_RATING
                        if state.phase is VoicePhase.AWAITING_RATING
                        else None
                    ),
                )
            elif event.kind is VoiceEventKind.GENERATION_CANCELLED:
                if state.phase in {
                    VoicePhase.PREPARING_CARD,
                    VoicePhase.ADVANCING,
                    VoicePhase.CLOSED,
                }:
                    self._invalid(state, event)
                assert state.identity is not None
                assert event.next_turn_id is not None
                post_answer = (
                    state.phase
                    in {
                        VoicePhase.SPEAKING_FEEDBACK,
                        VoicePhase.SPEAKING_OFFICIAL_ANSWER,
                        VoicePhase.DISCUSSING,
                    }
                    or state.resume_phase is VoicePhase.AWAITING_RATING
                )
                target = (
                    VoicePhase.AWAITING_RATING if post_answer else VoicePhase.LISTENING
                )
                next_identity = TurnIdentity(
                    session_id=state.identity.session_id,
                    card_id=state.identity.card_id,
                    turn_id=event.next_turn_id,
                    generation_id=state.identity.generation_id + 1,
                    sequence=event.identity.sequence,
                )
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=target,
                    identity=next_identity,
                    grade=(
                        state.grade if target is VoicePhase.AWAITING_RATING else None
                    ),
                )
            elif event.kind in {
                VoiceEventKind.MEDIA_STARTED,
                VoiceEventKind.PAUSED,
                VoiceEventKind.RECOVERY_STARTED,
            }:
                next_state = self._enter_interrupting_state(state, event)
            elif event.kind in {
                VoiceEventKind.MEDIA_FINISHED,
                VoiceEventKind.RESUMED,
                VoiceEventKind.RECOVERED,
            }:
                next_state = self._leave_interrupting_state(state, event)
            else:
                transition = _SIMPLE_TRANSITIONS.get(event.kind)
                if transition is None:
                    self._invalid(state, event)
                allowed, target = transition
                if state.phase not in allowed:
                    self._invalid(state, event)
                next_state = VoiceState(
                    session_id=state.session_id,
                    phase=target,
                    identity=event.identity,
                    grade=(
                        event.grade
                        if event.kind is VoiceEventKind.GRADE_READY
                        else state.grade
                    ),
                    playback=state.playback,
                )

            self._state = next_state
            return next_state

    @staticmethod
    def _matching_playback(
        state: VoiceState,
        event: VoiceEvent,
    ) -> PlaybackBoundary:
        playback = state.playback
        if playback is None:
            raise InvalidTransitionError("no_playback_is_awaiting_completion")
        if playback.generation_id != event.identity.generation_id:
            raise StaleEventError("playback_generation_is_not_active")
        if playback.utterance_id != event.utterance_id:
            raise StaleEventError("playback_utterance_is_not_active")
        return playback

    @staticmethod
    def _validate_sequence(state: VoiceState, event: VoiceEvent) -> None:
        if event.identity.session_id != state.session_id:
            raise StaleEventError("event_session_is_not_active")
        if state.identity is None:
            if event.identity.sequence != 1:
                raise OutOfOrderEventError("first_event_sequence_must_be_one")
            return
        current = state.identity.sequence
        if event.identity.sequence == current:
            raise DuplicateEventError("event_sequence_already_applied")
        if event.identity.sequence != current + 1:
            raise OutOfOrderEventError("event_sequence_is_not_contiguous")

    @staticmethod
    def _validate_identity(state: VoiceState, event: VoiceEvent) -> None:
        current = state.identity
        if current is None:
            if event.kind is not VoiceEventKind.CARD_READY:
                raise StaleEventError("card_has_not_been_prepared")
            if event.identity.generation_id != 1:
                raise StaleEventError("first_card_generation_must_be_one")
            return

        if event.kind is VoiceEventKind.CARD_READY:
            if event.identity.card_id == current.card_id:
                raise StaleEventError("next_card_must_have_new_identity")
            if event.identity.generation_id != current.generation_id + 1:
                raise StaleEventError("next_card_generation_is_not_contiguous")
            return

        if event.kind is VoiceEventKind.TURN_STARTED:
            if event.identity.card_id != current.card_id:
                raise StaleEventError("turn_started_for_inactive_card")
            if event.identity.turn_id == current.turn_id:
                raise StaleEventError("new_turn_requires_new_turn_id")
            if event.identity.generation_id != current.generation_id + 1:
                raise StaleEventError("new_turn_generation_is_not_contiguous")
            return

        if not event.identity.same_generation(current):
            raise StaleEventError("event_generation_is_not_active")
        if event.grade is not None and not event.grade.identity.same_generation(
            current
        ):
            raise StaleEventError("grade_generation_is_not_active")

    @staticmethod
    def _enter_interrupting_state(
        state: VoiceState,
        event: VoiceEvent,
    ) -> VoiceState:
        target_by_kind = {
            VoiceEventKind.MEDIA_STARTED: VoicePhase.PLAYING_CARD_MEDIA,
            VoiceEventKind.PAUSED: VoicePhase.PAUSED,
            VoiceEventKind.RECOVERY_STARTED: VoicePhase.RECOVERING,
        }
        target = target_by_kind[event.kind]
        if state.phase in {
            VoicePhase.PREPARING_CARD,
            VoicePhase.ADVANCING,
            VoicePhase.CLOSED,
            VoicePhase.PLAYING_CARD_MEDIA,
            VoicePhase.PAUSED,
            VoicePhase.RECOVERING,
        }:
            VoiceStateMachine._invalid(state, event)
        if (
            state.playback is not None
            and event.kind is not VoiceEventKind.RECOVERY_STARTED
        ):
            raise InvalidTransitionError(
                "active_playback_must_be_cancelled_before_audio_focus_changes"
            )
        return VoiceState(
            session_id=state.session_id,
            phase=target,
            identity=event.identity,
            grade=state.grade,
            resume_phase=state.phase,
            playback=state.playback,
        )

    @staticmethod
    def _leave_interrupting_state(
        state: VoiceState,
        event: VoiceEvent,
    ) -> VoiceState:
        source_by_kind = {
            VoiceEventKind.MEDIA_FINISHED: VoicePhase.PLAYING_CARD_MEDIA,
            VoiceEventKind.RESUMED: VoicePhase.PAUSED,
            VoiceEventKind.RECOVERED: VoicePhase.RECOVERING,
        }
        if state.phase is not source_by_kind[event.kind] or state.resume_phase is None:
            VoiceStateMachine._invalid(state, event)
        resume_phase = state.resume_phase
        assert resume_phase is not None
        return VoiceState(
            session_id=state.session_id,
            phase=resume_phase,
            identity=event.identity,
            grade=state.grade,
            playback=state.playback,
        )

    @staticmethod
    def _invalid(state: VoiceState, event: VoiceEvent) -> NoReturn:
        raise InvalidTransitionError(
            f"{event.kind.value}_not_allowed_from_{state.phase.value}"
        )
