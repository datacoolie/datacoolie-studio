from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from threading import local

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised on Unix CI/production hosts
    import fcntl


class ProcessLockTimeoutError(RuntimeError):
    """Raised when an OS-backed process lock cannot be acquired in time."""

    def __init__(self, path: Path, owner_label: str) -> None:
        self.path = path
        self.owner_label = owner_label
        super().__init__(
            f"Timed out acquiring {owner_label} process lock (lock: {path})"
        )


_thread_ownership = local()


def process_lock_owned(path: Path) -> bool:
    key = os.path.normcase(str(path.expanduser().resolve()))
    return key in getattr(_thread_ownership, "held", set())


@contextmanager
def reentrant_process_lock(
    path: Path, *, timeout_seconds: float | None = 0, owner_label: str
) -> Iterator[Path]:
    """Reuse an OS lock only within the owning thread's nested workflow."""
    path = path.expanduser().resolve()
    held = getattr(_thread_ownership, "held", None)
    if held is None:
        held = _thread_ownership.held = set()
    key = os.path.normcase(str(path))
    if key in held:
        yield path
        return
    with process_lock(path, timeout_seconds=timeout_seconds, owner_label=owner_label):
        held.add(key)
        try:
            yield path
        finally:
            held.remove(key)


@contextmanager
def process_lock(
    path: Path,
    *,
    timeout_seconds: float | None,
    owner_label: str,
) -> Iterator[Path]:
    """Hold an OS-backed lock file until the context exits.

    ``timeout_seconds=0`` gives callers non-blocking ownership semantics. A
    ``None`` timeout waits indefinitely, while a positive timeout polls until
    the deadline. The open descriptor makes ownership automatically recoverable
    when the owning process exits.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()

        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + max(0.0, timeout_seconds)
        )
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - exercised on Unix CI/production hosts
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProcessLockTimeoutError(path, owner_label)
                    time.sleep(min(0.05, remaining))
                else:
                    time.sleep(0.05)

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        handle.flush()
        yield path
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on Unix CI/production hosts
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        else:
            handle.close()
