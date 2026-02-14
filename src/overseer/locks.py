from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(
    lock_path: Path, timeout_seconds: float = 30.0, poll_seconds: float = 0.1
) -> Iterator[None]:
    """Cross-platform best-effort file lock (Linux/WSL primary)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    start = time.monotonic()
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt  # type: ignore

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() - start >= timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                time.sleep(poll_seconds)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt  # type: ignore

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
