from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminationPolicy:
    max_review_cycles: int = 3
    max_verifier_disputes: int = 2
    max_test_failures_without_progress: int = 2
