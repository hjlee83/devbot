from pathlib import Path

import pytest

from devbot.lock import LockAcquisitionError, ProcessLock


def test_lock_acquire_and_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "devbot.lock"
    lock = ProcessLock(lock_path)

    lock.acquire()
    assert lock_path.exists()
    lock.release()

    # A fresh lock on the same path can be acquired again after release.
    other = ProcessLock(lock_path)
    other.acquire()
    other.release()


def test_lock_rejects_second_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "devbot.lock"
    first = ProcessLock(lock_path)
    first.acquire()

    second = ProcessLock(lock_path)
    try:
        with pytest.raises(LockAcquisitionError):
            second.acquire()
    finally:
        first.release()


def test_lock_released_after_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "devbot.lock"

    with pytest.raises(RuntimeError, match="boom"):
        with ProcessLock(lock_path):
            raise RuntimeError("boom")

    # The lock must have been released by __exit__, so it can be re-acquired.
    other = ProcessLock(lock_path)
    other.acquire()
    other.release()
