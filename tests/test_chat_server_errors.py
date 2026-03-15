from __future__ import annotations

import json
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

import pytest

from tests.test_chat_server import _setup_service

def test_post_invalid_json_message(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch)
    try:
        req = request.Request(
            f"{base_url}/message",
            data=b"{not-json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            request.urlopen(req, timeout=5)
        assert exc.value.code == 400
        assert json.loads(exc.value.read()) == {"error": "invalid json"}
    finally:
        server.shutdown()
        service.stop()

def test_post_non_object_payload_message(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch)
    try:
        req = request.Request(
            f"{base_url}/message",
            data=json.dumps(["not", "an", "object"]).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            request.urlopen(req, timeout=5)
        assert exc.value.code == 400
        assert json.loads(exc.value.read()) == {"error": "payload must be an object"}
    finally:
        server.shutdown()
        service.stop()

def test_post_invalid_content_length(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch)
    try:
        # Test non-integer Content-Length
        req = request.Request(
            f"{base_url}/message",
            data=b'{"text": "foo"}',
            method="POST",
            headers={"Content-Type": "application/json", "Content-Length": "abc"},
        )
        with pytest.raises(HTTPError) as exc:
            request.urlopen(req, timeout=5)
        assert exc.value.code == 400
        assert json.loads(exc.value.read()) == {"error": "invalid content length"}

        # Test negative Content-Length
        req = request.Request(
            f"{base_url}/message",
            data=b'{"text": "foo"}',
            method="POST",
            headers={"Content-Type": "application/json", "Content-Length": "-1"},
        )
        with pytest.raises(HTTPError) as exc:
            request.urlopen(req, timeout=5)
        assert exc.value.code == 400
        assert json.loads(exc.value.read()) == {"error": "invalid content length"}
    finally:
        server.shutdown()
        service.stop()

def test_post_not_found(tmp_path: Path, monkeypatch) -> None:
    service, server, base_url, _ = _setup_service(tmp_path, monkeypatch)
    try:
        req = request.Request(
            f"{base_url}/unknown",
            data=b'{"text": "foo"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            request.urlopen(req, timeout=5)
        assert exc.value.code == 404
        assert json.loads(exc.value.read()) == {"error": "not found"}
    finally:
        server.shutdown()
        service.stop()
