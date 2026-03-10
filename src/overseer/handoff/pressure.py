from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


PressureBand = Literal["normal", "observe_recommended", "switch_recommended"]


@dataclass(frozen=True)
class PressureInputs:
    session_state_bytes: int
    conversation_turn_count: int
    conversation_bytes: int
    active_run_count: int
    plan_step_count: int


@dataclass(frozen=True)
class PressurePolicy:
    state_bytes_budget: int = 96_000
    conversation_turn_budget: int = 120
    conversation_bytes_budget: int = 64_000
    observe_threshold: float = 0.65
    switch_threshold: float = 0.85


@dataclass(frozen=True)
class PressureAssessment:
    score: float
    band: PressureBand
    trigger_reasons: list[str]
    inputs: dict[str, object]
    policy: dict[str, object]


def assess_pressure(inputs: PressureInputs, policy: PressurePolicy) -> PressureAssessment:
    state_ratio = (inputs.session_state_bytes / policy.state_bytes_budget) if policy.state_bytes_budget > 0 else 0.0
    turn_ratio = (
        inputs.conversation_turn_count / policy.conversation_turn_budget if policy.conversation_turn_budget > 0 else 0.0
    )
    conversation_ratio = (
        inputs.conversation_bytes / policy.conversation_bytes_budget if policy.conversation_bytes_budget > 0 else 0.0
    )
    component_ratios = {
        "session_state_bytes": state_ratio,
        "conversation_turn_count": turn_ratio,
        "conversation_bytes": conversation_ratio,
    }
    score = min(1.5, max(component_ratios.values(), default=0.0))
    if score >= policy.switch_threshold:
        band: PressureBand = "switch_recommended"
    elif score >= policy.observe_threshold:
        band = "observe_recommended"
    else:
        band = "normal"

    dominant = [name for name, ratio in component_ratios.items() if ratio == max(component_ratios.values())]
    trigger_reasons = [f"{name} ratio={component_ratios[name]:.3f}" for name in sorted(dominant)]
    return PressureAssessment(
        score=score,
        band=band,
        trigger_reasons=trigger_reasons,
        inputs=asdict(inputs),
        policy=asdict(policy),
    )
