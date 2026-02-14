"""File-system helpers: atomic writes and test-only delay hooks."""

from __future__ import annotations

import os
import time
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text to path atomically (temp file + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _test_delay_after_read(hook_env_var: str) -> None:
    """If hook_env_var is set to a positive float, sleep that many seconds (test-only)."""
    val = os.environ.get(hook_env_var, "")
    if not val:
        return
    try:
        secs = float(val)
        if secs > 0:
            time.sleep(secs)
    except ValueError:
        pass


def test_delay_meta_after_read() -> None:
    """Test hook: delay after reading run meta (set OVERSEER_TEST_DELAY_META_AFTER_READ)."""
    _test_delay_after_read("OVERSEER_TEST_DELAY_META_AFTER_READ")


def test_delay_taskstore_after_read() -> None:
    """Test hook: delay after reading task graph (set OVERSEER_TEST_DELAY_TASKSTORE_AFTER_READ)."""
    _test_delay_after_read("OVERSEER_TEST_DELAY_TASKSTORE_AFTER_READ")
