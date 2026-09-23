from __future__ import annotations

import unittest

from voice_study_companion.contracts import ReviewRating
from voice_study_companion.core.domain import (
    GradeResult,
    GradeVerdict,
    TurnIdentity,
    UtterancePolicy,
)
from voice_study_companion.core.state import (
    DuplicateEventError,
    InvalidTransitionError,
    OutOfOrderEventError,
    StaleEventError,
    VoiceEvent,
    VoiceEventKind,
    VoicePhase,
    VoiceStateMachine,
)


def _identity(
    sequence: int,
    *,
    card: str = "synthetic-card-1",
    turn: str = "turn-1",
    generation: int = 1,
) -> TurnIdentity:
    return TurnIdentity("session-1", card, turn, generation, sequence)


def _grade(identity: TurnIdentity) -> GradeResult:
    return GradeResult(
        identity=identity,
        verdict=GradeVerdict.PARTIAL,
        covered_concepts=("producer-ended",),
        missing_concepts=("local-buffer-drained",),
        incorrect_concepts=(),
        confidence=0.9,
        explanation="The local drain signal was missing.",
        proposed_rating=ReviewRating.HARD,
    )


def _complete_playback(
    machine: VoiceStateMachine,
    sequence: int,
    *,
    utterance_id: str,
    policy: UtterancePolicy,
    expected_speaking_phase: VoicePhase,
    expected_drained_phase: VoicePhase,
) -> int:
    state = machine.apply(
        VoiceEvent(
            VoiceEventKind.PLAYBACK_STARTED,
            _identity(sequence),
            utterance_id=utterance_id,
            utterance_policy=policy,
        )
    )
    assert state.phase is expected_speaking_phase
    assert state.playback is not None
    assert state.playback.policy is policy
    state = machine.apply(
        VoiceEvent(
            VoiceEventKind.PLAYBACK_PRODUCER_FINISHED,
            _identity(sequence + 1),
            utterance_id=utterance_id,
        )
    )
    assert state.phase is expected_speaking_phase
    assert state.playback is not None
    assert state.playback.producer_complete
    state = machine.apply(
        VoiceEvent(
            VoiceEventKind.PLAYBACK_DRAINED,
            _identity(sequence + 2),
            utterance_id=utterance_id,
        )
    )
    assert state.phase is expected_drained_phase
    assert state.playback is None
    return sequence + 3


def _reach_awaiting_rating(machine: VoiceStateMachine) -> int:
    machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
    sequence = _complete_playback(
        machine,
        2,
        utterance_id="prompt-1",
        policy=UtterancePolicy.LOCKED,
        expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
        expected_drained_phase=VoicePhase.LISTENING,
    )
    machine.apply(
        VoiceEvent(VoiceEventKind.SPEECH_COMMITTED, _identity(sequence))
    )
    sequence += 1
    machine.apply(
        VoiceEvent(VoiceEventKind.TRANSCRIPT_READY, _identity(sequence))
    )
    sequence += 1
    grade_identity = _identity(sequence)
    machine.apply(
        VoiceEvent(
            VoiceEventKind.GRADE_READY,
            grade_identity,
            grade=_grade(grade_identity),
        )
    )
    return _complete_playback(
        machine,
        sequence + 1,
        utterance_id="feedback-1",
        policy=UtterancePolicy.LOCKED,
        expected_speaking_phase=VoicePhase.SPEAKING_FEEDBACK,
        expected_drained_phase=VoicePhase.AWAITING_RATING,
    )


class CoreStateTests(unittest.TestCase):
    def test_complete_answer_path_and_next_card(self) -> None:
        machine = VoiceStateMachine("session-1")
        sequence = _reach_awaiting_rating(machine)

        state = machine.apply(
            VoiceEvent(
                VoiceEventKind.OFFICIAL_ANSWER_PLANNED,
                _identity(sequence),
            )
        )
        self.assertIs(state.phase, VoicePhase.SPEAKING_OFFICIAL_ANSWER)
        sequence = _complete_playback(
            machine,
            sequence + 1,
            utterance_id="official-answer-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_OFFICIAL_ANSWER,
            expected_drained_phase=VoicePhase.AWAITING_RATING,
        )
        machine.apply(
            VoiceEvent(VoiceEventKind.DISCUSSION_STARTED, _identity(sequence))
        )
        sequence = _complete_playback(
            machine,
            sequence + 1,
            utterance_id="discussion-1",
            policy=UtterancePolicy.OPEN_BARGE_IN,
            expected_speaking_phase=VoicePhase.DISCUSSING,
            expected_drained_phase=VoicePhase.AWAITING_RATING,
        )
        state = machine.apply(
            VoiceEvent(VoiceEventKind.RATING_CONFIRMED, _identity(sequence))
        )
        self.assertIs(state.phase, VoicePhase.ADVANCING)
        next_card = _identity(
            sequence + 1,
            card="synthetic-card-2",
            turn="turn-2",
            generation=2,
        )
        state = machine.apply(
            VoiceEvent(VoiceEventKind.CARD_READY, next_card)
        )
        self.assertIs(state.phase, VoicePhase.SPEAKING_PROMPT)
        self.assertEqual(state.identity, next_card)
        self.assertIsNone(state.grade)

    def test_clarification_new_turn_and_cancellation_are_explicit(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        sequence = _complete_playback(
            machine,
            2,
            utterance_id="prompt-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        machine.apply(
            VoiceEvent(VoiceEventKind.SPEECH_COMMITTED, _identity(sequence))
        )
        sequence += 1
        machine.apply(
            VoiceEvent(VoiceEventKind.TRANSCRIPT_READY, _identity(sequence))
        )
        sequence += 1
        machine.apply(
            VoiceEvent(
                VoiceEventKind.CLARIFICATION_PLANNED,
                _identity(sequence),
            )
        )
        sequence = _complete_playback(
            machine,
            sequence + 1,
            utterance_id="clarification-1",
            policy=UtterancePolicy.OPEN_BARGE_IN,
            expected_speaking_phase=VoicePhase.CLARIFYING,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        new_turn = _identity(sequence, turn="turn-2", generation=2)
        state = machine.apply(
            VoiceEvent(VoiceEventKind.TURN_STARTED, new_turn)
        )
        self.assertEqual(state.identity, new_turn)
        state = machine.apply(
            VoiceEvent(
                VoiceEventKind.GENERATION_CANCELLED,
                _identity(sequence + 1, turn="turn-2", generation=2),
                next_turn_id="turn-3",
            )
        )
        assert state.identity is not None
        self.assertEqual(state.identity.generation_id, 3)
        self.assertEqual(state.identity.turn_id, "turn-3")
        late = _identity(sequence + 2, turn="turn-2", generation=2)
        with self.assertRaises(StaleEventError):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.GRADE_READY,
                    late,
                    grade=_grade(late),
                )
            )
        self.assertIsNone(machine.snapshot.grade)

    def test_interrupting_substates_resume_actual_prior_state(self) -> None:
        cases = (
            (
                VoiceEventKind.MEDIA_STARTED,
                VoicePhase.PLAYING_CARD_MEDIA,
                VoiceEventKind.MEDIA_FINISHED,
            ),
            (VoiceEventKind.PAUSED, VoicePhase.PAUSED, VoiceEventKind.RESUMED),
            (
                VoiceEventKind.RECOVERY_STARTED,
                VoicePhase.RECOVERING,
                VoiceEventKind.RECOVERED,
            ),
        )
        for start_kind, entered, finish_kind in cases:
            with self.subTest(start_kind=start_kind):
                machine = VoiceStateMachine("session-1")
                machine.apply(
                    VoiceEvent(VoiceEventKind.CARD_READY, _identity(1))
                )
                sequence = _complete_playback(
                    machine,
                    2,
                    utterance_id="prompt-1",
                    policy=UtterancePolicy.LOCKED,
                    expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
                    expected_drained_phase=VoicePhase.LISTENING,
                )
                state = machine.apply(
                    VoiceEvent(start_kind, _identity(sequence))
                )
                self.assertIs(state.phase, entered)
                state = machine.apply(
                    VoiceEvent(finish_kind, _identity(sequence + 1))
                )
                self.assertIs(state.phase, VoicePhase.LISTENING)

    def test_duplicate_gap_stale_and_invalid_events_fail_closed(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        with self.assertRaises(DuplicateEventError):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(1),
                    utterance_id="prompt-1",
                    utterance_policy=UtterancePolicy.LOCKED,
                )
            )
        with self.assertRaises(OutOfOrderEventError):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(3),
                    utterance_id="prompt-1",
                    utterance_policy=UtterancePolicy.LOCKED,
                )
            )
        with self.assertRaises(StaleEventError):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(2, card="old-card"),
                    utterance_id="prompt-1",
                    utterance_policy=UtterancePolicy.LOCKED,
                )
            )
        with self.assertRaises(InvalidTransitionError):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.GRADE_READY,
                    _identity(2),
                    grade=_grade(_identity(2)),
                )
            )
        self.assertIs(machine.snapshot.phase, VoicePhase.SPEAKING_PROMPT)

    def test_phase_specific_utterance_policies_are_enforced(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        with self.assertRaisesRegex(InvalidTransitionError, "not_allowed"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(2),
                    utterance_id="wrong-prompt-policy",
                    utterance_policy=UtterancePolicy.OPEN_BARGE_IN,
                )
            )

        sequence = _complete_playback(
            machine,
            2,
            utterance_id="prompt-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        machine.apply(
            VoiceEvent(VoiceEventKind.SPEECH_COMMITTED, _identity(sequence))
        )
        sequence += 1
        machine.apply(
            VoiceEvent(VoiceEventKind.TRANSCRIPT_READY, _identity(sequence))
        )
        sequence += 1
        machine.apply(
            VoiceEvent(
                VoiceEventKind.CLARIFICATION_PLANNED,
                _identity(sequence),
            )
        )
        with self.assertRaisesRegex(InvalidTransitionError, "not_allowed"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(sequence + 1),
                    utterance_id="wrong-clarification-policy",
                    utterance_policy=UtterancePolicy.LOCKED,
                )
            )

    def test_playback_requires_producer_end_then_correlated_local_drain(
        self,
    ) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        machine.apply(
            VoiceEvent(
                VoiceEventKind.PLAYBACK_STARTED,
                _identity(2),
                utterance_id="prompt-current",
                utterance_policy=UtterancePolicy.LOCKED,
            )
        )
        with self.assertRaisesRegex(InvalidTransitionError, "before_producer"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_DRAINED,
                    _identity(3),
                    utterance_id="prompt-current",
                )
            )
        self.assertIs(machine.snapshot.phase, VoicePhase.SPEAKING_PROMPT)
        machine.apply(
            VoiceEvent(
                VoiceEventKind.PLAYBACK_PRODUCER_FINISHED,
                _identity(3),
                utterance_id="prompt-current",
            )
        )
        with self.assertRaisesRegex(StaleEventError, "utterance"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_DRAINED,
                    _identity(4),
                    utterance_id="prompt-stale",
                )
            )
        self.assertIs(machine.snapshot.phase, VoicePhase.SPEAKING_PROMPT)
        self.assertIsNotNone(machine.snapshot.playback)

    def test_official_answer_requires_locked_policy(self) -> None:
        machine = VoiceStateMachine("session-1")
        sequence = _reach_awaiting_rating(machine)
        machine.apply(
            VoiceEvent(
                VoiceEventKind.OFFICIAL_ANSWER_PLANNED,
                _identity(sequence),
            )
        )
        with self.assertRaisesRegex(InvalidTransitionError, "not_allowed"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_STARTED,
                    _identity(sequence + 1),
                    utterance_id="official-answer-1",
                    utterance_policy=UtterancePolicy.OPEN_BARGE_IN,
                )
            )
        self.assertIs(
            machine.snapshot.phase,
            VoicePhase.SPEAKING_OFFICIAL_ANSWER,
        )
        self.assertIsNone(machine.snapshot.playback)

    def test_reveal_waits_silently_until_explicit_answer_request(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        sequence = _complete_playback(
            machine,
            2,
            utterance_id="prompt-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        revealed = machine.apply(
            VoiceEvent(VoiceEventKind.ANSWER_REVEALED, _identity(sequence))
        )
        self.assertIs(revealed.phase, VoicePhase.AWAITING_RATING)
        self.assertIsNone(revealed.grade)
        requested = machine.apply(
            VoiceEvent(
                VoiceEventKind.OFFICIAL_ANSWER_PLANNED,
                _identity(sequence + 1),
            )
        )
        self.assertIs(
            requested.phase,
            VoicePhase.SPEAKING_OFFICIAL_ANSWER,
        )

    def test_post_answer_question_repeat_returns_to_awaiting_rating(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        sequence = _complete_playback(
            machine,
            2,
            utterance_id="prompt-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        machine.apply(
            VoiceEvent(VoiceEventKind.ANSWER_REVEALED, _identity(sequence))
        )
        replay = machine.apply(
            VoiceEvent(
                VoiceEventKind.PROMPT_REPLAY_PLANNED,
                _identity(sequence + 1),
            )
        )
        self.assertIs(replay.phase, VoicePhase.SPEAKING_PROMPT)
        self.assertIs(replay.resume_phase, VoicePhase.AWAITING_RATING)
        _complete_playback(
            machine,
            sequence + 2,
            utterance_id="post-answer-question",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.AWAITING_RATING,
        )

    def test_explicit_transcript_correction_reenters_thinking(self) -> None:
        machine = VoiceStateMachine("session-1")
        sequence = _reach_awaiting_rating(machine)
        state = machine.apply(
            VoiceEvent(
                VoiceEventKind.CORRECTION_STARTED,
                _identity(sequence),
            )
        )
        self.assertIs(state.phase, VoicePhase.THINKING)
        self.assertIsNotNone(state.grade)

    def test_cancel_requested_answer_stays_post_reveal(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        sequence = _complete_playback(
            machine,
            2,
            utterance_id="prompt-1",
            policy=UtterancePolicy.LOCKED,
            expected_speaking_phase=VoicePhase.SPEAKING_PROMPT,
            expected_drained_phase=VoicePhase.LISTENING,
        )
        machine.apply(
            VoiceEvent(VoiceEventKind.ANSWER_REVEALED, _identity(sequence))
        )
        sequence += 1
        machine.apply(
            VoiceEvent(
                VoiceEventKind.OFFICIAL_ANSWER_PLANNED,
                _identity(sequence),
            )
        )
        sequence += 1
        machine.apply(
            VoiceEvent(
                VoiceEventKind.PLAYBACK_STARTED,
                _identity(sequence),
                utterance_id="official-answer",
                utterance_policy=UtterancePolicy.LOCKED,
            )
        )
        stopped = machine.apply(
            VoiceEvent(
                VoiceEventKind.GENERATION_CANCELLED,
                _identity(sequence + 1),
                next_turn_id="turn-2",
            )
        )
        self.assertIs(stopped.phase, VoicePhase.AWAITING_RATING)
        self.assertIsNone(stopped.playback)
        assert stopped.identity is not None
        self.assertEqual(stopped.identity.generation_id, 2)

    def test_cancellation_invalidates_pending_playback_ack(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        machine.apply(
            VoiceEvent(
                VoiceEventKind.PLAYBACK_STARTED,
                _identity(2),
                utterance_id="prompt-old",
                utterance_policy=UtterancePolicy.LOCKED,
            )
        )
        state = machine.apply(
            VoiceEvent(
                VoiceEventKind.GENERATION_CANCELLED,
                _identity(3),
                next_turn_id="turn-2",
            )
        )
        self.assertIs(state.phase, VoicePhase.LISTENING)
        self.assertIsNone(state.playback)
        with self.assertRaisesRegex(StaleEventError, "generation"):
            machine.apply(
                VoiceEvent(
                    VoiceEventKind.PLAYBACK_DRAINED,
                    _identity(4),
                    utterance_id="prompt-old",
                )
            )

    def test_active_session_closes_once_and_rejects_later_events(self) -> None:
        machine = VoiceStateMachine("session-1")
        machine.apply(VoiceEvent(VoiceEventKind.CARD_READY, _identity(1)))
        state = machine.apply(
            VoiceEvent(VoiceEventKind.CLOSED, _identity(2))
        )
        self.assertIs(state.phase, VoicePhase.CLOSED)
        with self.assertRaises(InvalidTransitionError):
            machine.apply(
                VoiceEvent(VoiceEventKind.CLOSED, _identity(3))
            )


if __name__ == "__main__":
    unittest.main()
