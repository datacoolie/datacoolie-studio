from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock


@dataclass
class _LockEntry:
    lock: Lock
    users: int = 0


class InProcessResultBuildCoordinator:
    """Coalesce identical result builds inside the current Studio process."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._entries: dict[str, _LockEntry] = {}

    @contextmanager
    def acquire(self, identity: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.setdefault(identity, _LockEntry(lock=Lock()))
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(identity) is entry:
                    self._entries.pop(identity, None)

    def active_keys(self) -> int:
        with self._guard:
            return len(self._entries)


default_result_build_coordinator = InProcessResultBuildCoordinator()

