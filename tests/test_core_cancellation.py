from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from voice_study_companion.core.cancellation import (
    GenerationCancelledError,
    GenerationQueueFullError,
    GenerationRuntime,
    StaleGenerationError,
)
from voice_study_companion.core.domain import TurnIdentity


def _identity(generation: int, turn: str) -> TurnIdentity:
    return TurnIdentity("session", "synthetic-card", turn, generation, generation)


class CoreCancellationTests(unittest.TestCase):
    def test_cancel_invalidates_all_queued_effects_and_token(self) -> None:
        first = _identity(1, "turn-1")
        runtime = GenerationRuntime(
            first,
            model_queue_size=1,
            audio_queue_size=2,
            action_queue_size=1,
        )
        runtime.model_outputs.put(first, "grade")
        runtime.audio_chunks.put(first, b"one")
        runtime.audio_chunks.put(first, b"two")
        runtime.actions.put(first, "review-intent")
        with self.assertRaises(GenerationQueueFullError):
            runtime.audio_chunks.put(first, b"three")

        token = runtime.cancellation
        result = runtime.cancel()
        self.assertTrue(token.cancelled)
        with self.assertRaises(GenerationCancelledError):
            token.raise_if_cancelled()
        self.assertEqual(
            (
                result.discarded_model_outputs,
                result.discarded_audio_chunks,
                result.discarded_actions,
            ),
            (1, 2, 1),
        )
        with self.assertRaises(StaleGenerationError):
            runtime.model_outputs.put(first, "late grade")

    def test_rotation_rejects_late_results_under_race(self) -> None:
        first = _identity(1, "turn-1")
        second = _identity(2, "turn-2")
        runtime = GenerationRuntime(first)

        def late_result() -> bool:
            try:
                runtime.accept_result(first)
            except StaleGenerationError:
                return False
            return True

        runtime.rotate(second)
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = tuple(executor.map(lambda _: late_result(), range(64)))
        self.assertFalse(any(accepted))
        runtime.accept_result(second)

    def test_rotation_requires_monotonic_generation_and_same_session(self) -> None:
        runtime = GenerationRuntime(_identity(2, "turn-2"))
        with self.assertRaisesRegex(ValueError, "advance"):
            runtime.rotate(_identity(2, "turn-other"))
        other_session = TurnIdentity("other", "synthetic-card", "turn-3", 3, 3)
        with self.assertRaisesRegex(ValueError, "session"):
            runtime.rotate(other_session)


if __name__ == "__main__":
    unittest.main()
