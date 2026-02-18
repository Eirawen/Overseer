from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatCommand:
    group: str
    action: str
    args: tuple[str, ...] = ()


def parse_chat_command(text: str) -> ChatCommand:
    tokens = shlex.split(text)
    if not tokens or not tokens[0].startswith("/"):
        raise ValueError("not a command")

    head = tokens[0].lstrip("/")
    if head in {"quit", "exit"}:
        return ChatCommand(group="session", action="quit")

    if head == "open":
        if len(tokens) != 2:
            raise ValueError("usage: /open <run_id>")
        return ChatCommand(group="run", action="open", args=(tokens[1],))

    if head == "run":
        if len(tokens) < 2:
            raise ValueError("usage: /run <list|status|cancel> [run_id]")
        action = tokens[1]
        if action == "list" and len(tokens) == 2:
            return ChatCommand(group="run", action="list")
        if action in {"status", "cancel"} and len(tokens) == 3:
            return ChatCommand(group="run", action=action, args=(tokens[2],))
        raise ValueError("usage: /run <list|status|cancel> [run_id]")

    if head == "queue":
        if len(tokens) < 2:
            raise ValueError("usage: /queue <list|resolve>")
        action = tokens[1]
        if action == "list" and len(tokens) == 2:
            return ChatCommand(group="queue", action="list")
        if action == "resolve" and len(tokens) >= 5:
            if "--choice" not in tokens or "--rationale" not in tokens:
                raise ValueError(
                    "usage: /queue resolve <request_id> --choice <choice> --rationale <rationale> [--artifact-path <path>]"
                )
            return ChatCommand(group="queue", action="resolve", args=tuple(tokens[2:]))
        raise ValueError(
            "usage: /queue resolve <request_id> --choice <choice> --rationale <rationale> [--artifact-path <path>]"
        )

    raise ValueError(f"unknown command: {tokens[0]}")
