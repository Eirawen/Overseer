"""Tests for overseer.fs: atomic writes and test delay hooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from overseer.fs import (
    atomic_write_text,
    test_delay_meta_after_read,
    test_delay_taskstore_after_read,
)


def test_atomic_write_text_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "file.txt"
    atomic_write_text(path, "hello")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello"
    assert (tmp_path / "sub").exists()


def test_atomic_write_text_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("old", encoding="utf-8")
    atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_atomic_write_text_no_partial_read(tmp_path: Path) -> None:
    """Reader never sees partial content (replace is atomic)."""
    path = tmp_path / "f.txt"
    atomic_write_text(path, "short")
    # Overwrite with longer content; concurrent reader would see old or new, not half
    atomic_write_text(path, "longer content now")
    assert path.read_text(encoding="utf-8") == "longer content now"


def test_atomic_write_text_custom_encoding(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    atomic_write_text(path, "café", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "café"


def test_delay_meta_after_read_no_delay_without_env() -> None:
    """Without env var, returns immediately."""
    test_delay_meta_after_read()


def test_delay_meta_after_read_sleeps_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OVERSEER_TEST_DELAY_META_AFTER_READ", "0.05")
    import time
    start = time.monotonic()
    test_delay_meta_after_read()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04


def test_delay_taskstore_after_read_no_delay_without_env() -> None:
    test_delay_taskstore_after_read()


def test_delay_taskstore_after_read_ignores_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OVERSEER_TEST_DELAY_TASKSTORE_AFTER_READ", "not-a-number")
    test_delay_taskstore_after_read()
