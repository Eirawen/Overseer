from __future__ import annotations

from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.prompting import PromptPackBuilder, PromptPolicy


def _init_store(tmp_path: Path) -> CodexStore:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "codex").mkdir(parents=True, exist_ok=True)
    store = CodexStore(repo)
    store.init_structure()
    return store


def test_prompt_pack_has_required_sections_in_order(tmp_path: Path) -> None:
    store = _init_store(tmp_path)
    policy = PromptPolicy.from_codex(store)
    pack = PromptPackBuilder(policy=policy, codex_store=store).build_for_run(
        task_id="task-1",
        run_id="run-abc123",
        objective="Implement feature",
        worker_role="builder",
    )

    headings = [
        "# System Instructions (Always Insert)",
        "# Project Context",
        "# Run Objective",
        "# Execution Constraints",
    ]
    positions = [pack.composed_prompt.index(h) for h in headings]
    assert positions == sorted(positions)
    assert pack.composed_prompt.endswith("\n")


def test_prompt_metadata_includes_source_paths_and_warnings(tmp_path: Path) -> None:
    store = _init_store(tmp_path)
    (store.codex_root / "05_AGENTS" / "TERMINATION.md").unlink()
    policy = PromptPolicy.from_codex(store)
    pack = PromptPackBuilder(policy=policy, codex_store=store).build_for_run(
        task_id="task-2",
        run_id="run-def456",
        objective="Review change",
        worker_role="reviewer",
    )

    metadata = pack.metadata
    assert metadata["always_insert_source_path"] == "codex/01_PROJECT/ALWAYS_INSERT_PROMPT.md"
    assert isinstance(metadata["context_source_paths"], list)
    assert metadata["project_context_section_count"] == len(metadata["context_source_paths"])
    assert metadata["audit_paths"]["prompt_pack_md"] == "codex/08_TELEMETRY/runs/run-def456/prompt_pack.md"
    assert metadata["audit_paths"]["prompt_pack_json"] == "codex/08_TELEMETRY/runs/run-def456/prompt_pack.json"
    assert any("TERMINATION.md" in warning for warning in metadata["warnings"])


def test_missing_always_insert_uses_fallback_and_records_warning(tmp_path: Path) -> None:
    store = _init_store(tmp_path)
    (store.codex_root / "01_PROJECT" / "ALWAYS_INSERT_PROMPT.md").unlink()

    policy = PromptPolicy.from_codex(store)
    assert policy.always_insert_is_fallback is True
    assert any("always-insert prompt" in warning for warning in policy.warnings)

    pack = PromptPackBuilder(policy=policy, codex_store=store).build_for_run(
        task_id="task-3",
        run_id="run-ghi789",
        objective="Implement fallback",
    )
    assert "# System Instructions (Always Insert)" in pack.composed_prompt


def test_context_files_load_in_canonical_order_and_missing_are_skipped(tmp_path: Path) -> None:
    store = _init_store(tmp_path)
    (store.codex_root / "04_HUMAN_API" / "REQUEST_SCHEMA.md").unlink()

    policy = PromptPolicy.from_codex(store)
    source_paths = [section["source_path"] for section in policy.context_sections]
    assert source_paths == [
        "codex/01_PROJECT/OPERATING_MODE.md",
        "codex/05_AGENTS/TERMINATION.md",
        "codex/04_HUMAN_API/HUMAN_TASK_TYPES.json",
    ]
    assert any("REQUEST_SCHEMA.md" in warning for warning in policy.warnings)
