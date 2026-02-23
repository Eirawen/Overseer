from __future__ import annotations

from overseer.handoff.pressure import PressureInputs, PressurePolicy, assess_pressure


def test_pressure_assessment_is_deterministic() -> None:
    policy = PressurePolicy()
    inputs = PressureInputs(
        session_state_bytes=10_000,
        conversation_turn_count=20,
        conversation_bytes=5_000,
        active_run_count=1,
        plan_step_count=2,
    )
    a = assess_pressure(inputs, policy)
    b = assess_pressure(inputs, policy)
    assert a == b


def test_pressure_band_transitions() -> None:
    policy = PressurePolicy(
        state_bytes_budget=100,
        conversation_turn_budget=100,
        conversation_bytes_budget=100,
        observe_threshold=0.65,
        switch_threshold=0.85,
    )
    normal = assess_pressure(
        PressureInputs(10, 10, 10, 0, 0),
        policy,
    )
    observe = assess_pressure(
        PressureInputs(70, 10, 10, 0, 0),
        policy,
    )
    switch = assess_pressure(
        PressureInputs(90, 10, 10, 0, 0),
        policy,
    )
    assert normal.band == "normal"
    assert observe.band == "observe_recommended"
    assert switch.band == "switch_recommended"
    assert any("session_state_bytes" in reason for reason in switch.trigger_reasons)
