import multiprocessing
from pathlib import Path

import pytest

from devbot.lock import ProcessLock


def _attempt_acquire_in_subprocess(lock_path: str, result_queue: multiprocessing.Queue) -> None:
    """Run in a separate OS process: try to acquire `lock_path` and report the outcome."""
    from devbot.lock import LockAcquisitionError, ProcessLock

    try:
        ProcessLock(lock_path).acquire()
    except LockAcquisitionError:
        result_queue.put("rejected")
    else:
        result_queue.put("acquired")


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

    try:
        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue = ctx.Queue()
        process = ctx.Process(
            target=_attempt_acquire_in_subprocess,
            args=(str(lock_path), result_queue),
        )
        process.start()
        process.join(timeout=10)

        assert process.exitcode == 0
        assert result_queue.get(timeout=5) == "rejected"
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
