"""Tests for overseer.locks: file_lock acquire, release, timeout."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from overseer.locks import file_lock


def test_file_lock_acquires_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "locks" / "test.lock"
    with file_lock(lock_path):
        assert lock_path.exists()
    assert lock_path.exists()


def test_file_lock_serializes_two_threads(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    order: list[int] = []

    def hold(ident: int) -> None:
        with file_lock(lock_path):
            order.append(ident)
            time.sleep(0.05)

    t1 = threading.Thread(target=hold, args=(1,))
    t2 = threading.Thread(target=hold, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert order == [1, 2] or order == [2, 1]


def test_file_lock_timeout_when_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    with file_lock(lock_path, timeout_seconds=0.3, poll_seconds=0.05):
        with pytest.raises(TimeoutError, match="Timed out waiting for lock"):
            with file_lock(lock_path, timeout_seconds=0.2, poll_seconds=0.03):
                pass
