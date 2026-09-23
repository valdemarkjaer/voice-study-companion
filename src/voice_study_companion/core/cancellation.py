"""Generation-scoped cancellation and bounded side-effect queues."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from .domain import TurnIdentity


class GenerationCancelledError(asyncio.CancelledError):
    """Raised when work observes cancellation for its own generation."""


class StaleGenerationError(RuntimeError):
    """Raised when a late result belongs to a superseded generation."""


class GenerationQueueFullError(RuntimeError):
    """Raised instead of silently dropping a bounded effect."""


class CancellationToken:
    def __init__(self, identity: TurnIdentity) -> None:
        self.identity = identity
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationCancelledError


class BoundedGenerationQueue[T]:
    """A small synchronous buffer that rejects stale generations."""

    def __init__(
        self,
        max_size: int,
        is_current: Callable[[TurnIdentity], bool],
    ) -> None:
        if max_size < 1:
            raise ValueError("queue_max_size_must_be_positive")
        self.max_size = max_size
        self._is_current = is_current
        self._items: deque[tuple[TurnIdentity, T]] = deque()
        self._lock = RLock()

    def put(self, identity: TurnIdentity, item: T) -> None:
        with self._lock:
            if not self._is_current(identity):
                raise StaleGenerationError("queue_item_generation_is_not_active")
            if len(self._items) >= self.max_size:
                raise GenerationQueueFullError("generation_queue_is_full")
            self._items.append((identity, item))

    def pop(self, identity: TurnIdentity) -> T:
        with self._lock:
            if not self._is_current(identity):
                raise StaleGenerationError("queue_consumer_generation_is_not_active")
            if not self._items:
                raise IndexError("generation_queue_is_empty")
            queued_identity, item = self._items.popleft()
            if not queued_identity.same_generation(identity):
                self._items.clear()
                raise StaleGenerationError("queued_item_generation_is_not_active")
            return item

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


@dataclass(frozen=True, slots=True)
class CancellationResult:
    cancelled_identity: TurnIdentity
    discarded_model_outputs: int
    discarded_audio_chunks: int
    discarded_actions: int


class GenerationRuntime:
    """Own one active generation and invalidate every queued side effect."""

    def __init__(
        self,
        identity: TurnIdentity,
        *,
        model_queue_size: int = 4,
        audio_queue_size: int = 64,
        action_queue_size: int = 4,
    ) -> None:
        self._lock = RLock()
        self._identity = identity
        self._token = CancellationToken(identity)
        self.model_outputs = BoundedGenerationQueue[object](
            model_queue_size,
            self.is_current,
        )
        self.audio_chunks = BoundedGenerationQueue[object](
            audio_queue_size,
            self.is_current,
        )
        self.actions = BoundedGenerationQueue[object](
            action_queue_size,
            self.is_current,
        )

    @property
    def identity(self) -> TurnIdentity:
        with self._lock:
            return self._identity

    @property
    def cancellation(self) -> CancellationToken:
        with self._lock:
            return self._token

    def is_current(self, identity: TurnIdentity) -> bool:
        with self._lock:
            return not self._token.cancelled and identity.same_generation(
                self._identity
            )

    def accept_result(self, identity: TurnIdentity) -> None:
        if not self.is_current(identity):
            raise StaleGenerationError("result_generation_is_not_active")

    def cancel(self) -> CancellationResult:
        with self._lock:
            cancelled_identity = self._identity
            self._token.cancel()
            return CancellationResult(
                cancelled_identity=cancelled_identity,
                discarded_model_outputs=self.model_outputs.clear(),
                discarded_audio_chunks=self.audio_chunks.clear(),
                discarded_actions=self.actions.clear(),
            )

    def rotate(self, identity: TurnIdentity) -> CancellationResult:
        with self._lock:
            if identity.session_id != self._identity.session_id:
                raise ValueError("generation_rotation_cannot_change_session")
            if identity.generation_id <= self._identity.generation_id:
                raise ValueError("generation_rotation_must_advance_generation")
            result = self.cancel()
            self._identity = identity
            self._token = CancellationToken(identity)
            return result
