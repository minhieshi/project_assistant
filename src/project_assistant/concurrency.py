from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class ConversationRunLease:
    """A scoped lease for one active model run in one conversation."""

    coordinator: "ConversationRunCoordinator"
    key: tuple[str, str]
    lock: Lock
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.lock.release()
        self.coordinator._released(self.key, self.lock)

    def __enter__(self) -> "ConversationRunLease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class ConversationRunCoordinator:
    """Allow different conversations to run concurrently, but serialize each one.

    A second request against the same conversation is rejected rather than queued.
    This prevents interleaved user/assistant entries in one Markdown conversation
    while still allowing independent conversations in the same project to stream at
    the same time.
    """

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, str], Lock] = {}

    def try_acquire(self, project_id: str, conversation_id: str) -> ConversationRunLease | None:
        key = (project_id, conversation_id)
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        if not lock.acquire(blocking=False):
            return None
        return ConversationRunLease(self, key, lock)

    def is_running(self, project_id: str, conversation_id: str) -> bool:
        key = (project_id, conversation_id)
        with self._guard:
            lock = self._locks.get(key)
            return bool(lock and lock.locked())

    def _released(self, key: tuple[str, str], lock: Lock) -> None:
        # Drop idle lock objects so a long-lived app does not retain one forever for
        # every conversation that has ever been opened.
        with self._guard:
            if self._locks.get(key) is lock and not lock.locked():
                self._locks.pop(key, None)
