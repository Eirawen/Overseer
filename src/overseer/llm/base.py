from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class LLMAdapter(Protocol):
    def generate(self, system_prompt: str, messages: list[Message]) -> str: ...


class FakeLLM:
    """Deterministic test double keyed by user message fragments."""

    def __init__(self, responses: dict[str, str] | None = None, default_response: str = "ACK") -> None:
        self.responses = responses or {}
        self.default_response = default_response

    def generate(self, system_prompt: str, messages: list[Message]) -> str:
        _ = system_prompt
        if not messages:
            return self.default_response
        last = messages[-1].content.lower()
        for key, value in self.responses.items():
            if key.lower() in last:
                return value
        return self.default_response
