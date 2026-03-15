from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError
from overseer.llm.base import Message, FakeLLM

def test_message_initialization():
    msg = Message(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"

def test_message_frozen():
    msg = Message(role="user", content="hello")
    with pytest.raises(FrozenInstanceError):
        msg.role = "assistant" # type: ignore

def test_fake_llm_default_response():
    llm = FakeLLM()
    assert llm.generate("system", []) == "ACK"
    assert llm.generate("system", [Message("user", "hi")]) == "ACK"

def test_fake_llm_custom_default():
    llm = FakeLLM(default_response="No idea")
    assert llm.generate("system", []) == "No idea"
    assert llm.generate("system", [Message("user", "hi")]) == "No idea"

def test_fake_llm_fragment_matching():
    responses = {"hello": "Greetings!", "bye": "Farewell!"}
    llm = FakeLLM(responses=responses)

    assert llm.generate("system", [Message("user", "Say hello to me")]) == "Greetings!"
    assert llm.generate("system", [Message("user", "Time to say bye")]) == "Farewell!"

def test_fake_llm_case_insensitivity():
    responses = {"HELLO": "Greetings!"}
    llm = FakeLLM(responses=responses)

    assert llm.generate("system", [Message("user", "hello")]) == "Greetings!"
    assert llm.generate("system", [Message("user", "HELLO")]) == "Greetings!"

    responses = {"hello": "Greetings!"}
    llm = FakeLLM(responses=responses)
    assert llm.generate("system", [Message("user", "HELLO")]) == "Greetings!"

def test_fake_llm_last_message_only():
    responses = {"first": "one", "second": "two"}
    llm = FakeLLM(responses=responses)

    messages = [
        Message("user", "first"),
        Message("user", "second")
    ]
    assert llm.generate("system", messages) == "two"

def test_fake_llm_no_match():
    responses = {"hello": "Greetings!"}
    llm = FakeLLM(responses=responses, default_response="DEFAULT")

    assert llm.generate("system", [Message("user", "something else")]) == "DEFAULT"
