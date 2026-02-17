from __future__ import annotations

import pytest

from overseer.chat_commands import ChatCommand, parse_chat_command


def test_parse_run_list() -> None:
    assert parse_chat_command("/run list") == ChatCommand(group="run", action="list")


def test_parse_run_status() -> None:
    assert parse_chat_command("/run status run-123") == ChatCommand(
        group="run", action="status", args=("run-123",)
    )


def test_parse_queue_resolve_with_flags() -> None:
    cmd = parse_chat_command('/queue resolve hr-123 --choice "Redirect implementation approach" --rationale "safe"')
    assert cmd.group == "queue"
    assert cmd.action == "resolve"
    assert cmd.args[0] == "hr-123"


def test_parse_open() -> None:
    assert parse_chat_command("/open run-abc") == ChatCommand(
        group="run", action="open", args=("run-abc",)
    )


def test_parse_rejects_unknown_command() -> None:
    with pytest.raises(ValueError, match="unknown command"):
        parse_chat_command("/nope")



def test_parse_quit_alias() -> None:
    assert parse_chat_command("/exit") == ChatCommand(group="session", action="quit")


def test_parse_run_requires_run_id_for_status() -> None:
    with pytest.raises(ValueError, match="usage: /run"):
        parse_chat_command("/run status")


def test_parse_queue_resolve_requires_flags() -> None:
    with pytest.raises(ValueError, match="usage: /queue resolve"):
        parse_chat_command("/queue resolve hr-123 --choice only")
