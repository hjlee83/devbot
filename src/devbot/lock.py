"""File-based single-process lock.

Uses an advisory `flock(2)` on a lock file so exactly one DevBot process can
run at a time. `flock` is supported on both macOS and Linux, and is held per
open file description, so it is automatically released by the kernel if the
process exits or crashes without calling `release()`.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class LockAcquisitionError(RuntimeError):
    """Raised when the lock is already held by another owner."""


class ProcessLock:
    """A single-process advisory lock backed by a file path."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LockAcquisitionError(f"Lock already held: {self.path}") from exc

        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return

        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
