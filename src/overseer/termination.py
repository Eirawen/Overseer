from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TerminationPolicy:
    max_review_cycles: int
    max_verifier_disputes: int
    max_test_failures_without_progress: int

    @classmethod
    def from_codex(cls, codex_root: Path) -> "TerminationPolicy":
        termination_file = codex_root / "05_AGENTS" / "TERMINATION.md"
        text = termination_file.read_text(encoding="utf-8")

        review_cycles = _extract_int(text, r"max review cycles per task:\s*(\d+)", 3)
        verifier_disputes = _extract_int(
            text,
            r"Reviewer and Verifier disagree\s*(\w+)\s*=> escalate",
            2,
            allow_word_number=True,
        )
        test_failures = _extract_int(text, r"tests fail\s*(\w+)\s*without progress", 2, allow_word_number=True)
        return cls(
            max_review_cycles=review_cycles,
            max_verifier_disputes=verifier_disputes,
            max_test_failures_without_progress=test_failures,
        )


_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


def _extract_int(text: str, pattern: str, default: int, allow_word_number: bool = False) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return default
    raw = match.group(1).lower()
    if raw.isdigit():
        return int(raw)
    if allow_word_number and raw in _WORD_NUMBERS:
        return _WORD_NUMBERS[raw]
    return default
