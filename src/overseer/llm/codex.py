from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from typing import Any, Iterator

from overseer.llm.base import Message
from overseer.llm.oauth import OAuthCredentialRecord

CODEX_PROVIDER_ID = "openai-codex"
CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_DEFAULT_MODEL = "gpt-5.4"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_HOST = "127.0.0.1"
CODEX_REDIRECT_PORT = 1455
CODEX_REDIRECT_PATH = "/auth/callback"
CODEX_DEFAULT_SCOPE = "openid profile email offline_access"


@dataclass(frozen=True)
class LLMStreamEvent:
    type: str
    text: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class CodexProviderConfig:
    provider_id: str = CODEX_PROVIDER_ID
    base_url: str = CODEX_DEFAULT_BASE_URL
    model: str = CODEX_DEFAULT_MODEL
    profile_id: str = "default"
    client_id: str | None = None


class CodexOAuthAdapter:
    def __init__(self, client_id: str | None = None) -> None:
        self.client_id = client_id

    def login(self, *, is_headless: bool = False, timeout_seconds: float = 300.0) -> OAuthCredentialRecord:
        client_id = self._require_client_id()
        verifier = _random_string(64)
        challenge = _pkce_challenge(verifier)
        state = _random_string(24)
        redirect_uri = f"http://{CODEX_REDIRECT_HOST}:{CODEX_REDIRECT_PORT}{CODEX_REDIRECT_PATH}"
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": CODEX_DEFAULT_SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        authorize_url = f"{CODEX_AUTHORIZE_URL}?{query}"
        print("Open this URL to authenticate with ChatGPT Codex OAuth:")
        print(authorize_url)

        code: str | None = None
        if is_headless:
            redirect_url = input("Paste the final redirect URL: ").strip()
            code = _extract_code_from_redirect(redirect_url, expected_state=state)
        else:
            callback = _LocalOAuthCallbackServer(expected_state=state)
            callback.start()
            try:
                webbrowser.open(authorize_url)
            except Exception:
                pass
            print(f"Waiting for OAuth callback on {redirect_uri}")
            try:
                code = callback.wait_for_code(timeout_seconds=timeout_seconds)
            except TimeoutError:
                print("Local callback timed out. Paste the final redirect URL instead.")
                redirect_url = input("Redirect URL: ").strip()
                code = _extract_code_from_redirect(redirect_url, expected_state=state)
            finally:
                callback.close()

        if not code:
            raise RuntimeError("OAuth login did not return an authorization code")
        payload = _post_form_json(
            CODEX_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        return _credential_from_token_payload(payload, provider_id=CODEX_PROVIDER_ID, client_id=client_id)

    def refresh(self, credential: OAuthCredentialRecord) -> OAuthCredentialRecord:
        client_id = self._resolve_client_id_from_credential(credential)
        payload = _post_form_json(
            CODEX_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": credential.refresh_token,
                "client_id": client_id,
            },
        )
        refreshed = _credential_from_token_payload(payload, provider_id=credential.provider_id, client_id=client_id)
        return OAuthCredentialRecord(
            kind=credential.kind,
            provider_id=credential.provider_id,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            expires_at=refreshed.expires_at,
            account_id=refreshed.account_id or credential.account_id,
            email=refreshed.email or credential.email,
            metadata={**(credential.metadata or {}), **(refreshed.metadata or {})},
        )

    def _require_client_id(self) -> str:
        client_id = self.client_id or _resolve_client_id_from_codex_home()
        if not client_id:
            raise RuntimeError(
                "Missing Codex OAuth client id. Set OVERSEER_OPENAI_CODEX_CLIENT_ID or import existing Codex CLI credentials first."
            )
        return client_id

    def _resolve_client_id_from_credential(self, credential: OAuthCredentialRecord) -> str:
        metadata_client_id = (credential.metadata or {}).get("client_id")
        if metadata_client_id:
            return metadata_client_id
        access_payload = _try_extract_jwt_payload(credential.access_token) or {}
        token_client_id = access_payload.get("client_id")
        if isinstance(token_client_id, str) and token_client_id.strip():
            return token_client_id
        return self._require_client_id()


class CodexRuntimeClient:
    def __init__(self, credential: OAuthCredentialRecord, base_url: str = CODEX_DEFAULT_BASE_URL) -> None:
        self.credential = credential
        self.base_url = base_url.rstrip("/")

    def stream_chat(self, *, system_prompt: str, messages: list[Message], model: str) -> Iterator[LLMStreamEvent]:
        request_messages = []
        if system_prompt.strip():
            request_messages.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})
        for message in messages:
            request_messages.append(
                {
                    "role": message.role,
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
        body = {
            "model": model,
            "instructions": system_prompt.strip() or "You are a helpful assistant.",
            "input": request_messages,
            "stream": True,
            "store": False,
        }
        headers = {
            "Authorization": f"Bearer {self.credential.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        if self.credential.account_id:
            headers["ChatGPT-Account-Id"] = self.credential.account_id
        request = urllib.request.Request(
            f"{self.base_url}/codex/responses",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
                event_lines: list[str] = []
                saw_sse_payload = False
                raw_chunks: list[str] = []
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    raw_chunks.append(line)
                    if not line:
                        event = _parse_sse_event(event_lines)
                        event_lines.clear()
                        if event is None:
                            continue
                        saw_sse_payload = True
                        if event == "[DONE]":
                            yield LLMStreamEvent(type="done")
                            return
                        try:
                            payload = json.loads(event)
                        except json.JSONDecodeError:
                            continue
                        yield from _events_from_json_payload(payload)
                        continue
                    event_lines.append(line)
                if event_lines:
                    event = _parse_sse_event(event_lines)
                    if event and event != "[DONE]":
                        try:
                            payload = json.loads(event)
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict):
                            yield from _events_from_json_payload(payload)
                            saw_sse_payload = True
                if not saw_sse_payload:
                    raw_text = "\n".join(raw_chunks).strip()
                    if raw_text:
                        payload = json.loads(raw_text)
                        yield from _events_from_json_payload(payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            yield LLMStreamEvent(type="error", message=f"Codex request failed: HTTP {exc.code} {detail}".strip())
            return
        except urllib.error.URLError as exc:
            yield LLMStreamEvent(type="error", message=f"Codex request failed: {exc.reason}")
            return
        yield LLMStreamEvent(type="done")


def import_codex_cli_credential(path: Path | None = None) -> OAuthCredentialRecord:
    source_path = path or (Path.home() / ".codex" / "auth.json")
    if not source_path.exists():
        raise FileNotFoundError(f"Codex CLI auth file not found: {source_path}")
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        raise RuntimeError(f"Invalid Codex CLI auth file: {source_path}")
    access_token = _expect_str(tokens, "access_token")
    refresh_token = _expect_str(tokens, "refresh_token")
    account_id = _optional_str(tokens.get("account_id")) or _extract_codex_account_id(access_token)
    jwt_payload = _try_extract_jwt_payload(access_token) or {}
    email = _extract_email(jwt_payload)
    client_id = _extract_client_id(access_token, payload.get("tokens", {}).get("id_token"))
    credential_metadata = {"source": "codex-cli-import"}
    if client_id:
        credential_metadata["client_id"] = client_id
    expires_at = _extract_expiry(access_token)
    return OAuthCredentialRecord(
        kind="oauth",
        provider_id=CODEX_PROVIDER_ID,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
        email=email,
        metadata=credential_metadata,
    )


def _events_from_json_payload(payload: dict[str, Any]) -> Iterator[LLMStreamEvent]:
    payload_type = payload.get("type")
    extracted = False
    for text in _extract_text_chunks(payload):
        extracted = True
        yield LLMStreamEvent(type="text-delta", text=text)
    if payload_type in {"assistant-message", "response.output_text.done"}:
        final_text = payload.get("text") or payload.get("output_text")
        if isinstance(final_text, str) and final_text:
            yield LLMStreamEvent(type="assistant-message", text=final_text)
    if not extracted and payload.get("type") in {"error", "response.error"}:
        yield LLMStreamEvent(type="error", message=str(payload.get("message", "unknown error")))
    if payload.get("type") in {"done", "response.completed"}:
        yield LLMStreamEvent(type="done")


def _extract_text_chunks(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        payload_type = payload.get("type")
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            if payload_type in {
                "response.output_text.delta",
                "message.delta",
                "text-delta",
                None,
            } or "delta" in str(payload_type):
                yield delta
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text and payload_type not in {"response.output_text.done"}:
            yield output_text
        if isinstance(payload.get("content"), list):
            for item in payload["content"]:
                yield from _extract_text_chunks(item)
        if isinstance(payload.get("output"), list):
            for item in payload["output"]:
                yield from _extract_text_chunks(item)
        for key in ("message", "item", "response"):
            child = payload.get(key)
            if isinstance(child, (dict, list)):
                yield from _extract_text_chunks(child)
    elif isinstance(payload, list):
        for item in payload:
            yield from _extract_text_chunks(item)


def _parse_sse_event(lines: list[str]) -> str | None:
    parts: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            parts.append(line[5:].lstrip())
    if not parts:
        return None
    return "\n".join(parts)


def _credential_from_token_payload(
    payload: dict[str, Any], *, provider_id: str, client_id: str
) -> OAuthCredentialRecord:
    access_token = _expect_str(payload, "access_token")
    refresh_token = _expect_str(payload, "refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(expires_in, int):
        raise RuntimeError("Codex OAuth token payload missing expires_in")
    access_payload = _try_extract_jwt_payload(access_token) or {}
    email = _extract_email(access_payload)
    account_id = _extract_codex_account_id(access_token)
    metadata = {"client_id": client_id}
    return OAuthCredentialRecord(
        kind="oauth",
        provider_id=provider_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=int(time.time() * 1000) + expires_in * 1000,
        account_id=account_id,
        email=email,
        metadata=metadata,
    )


def _post_form_json(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Codex OAuth request failed: HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Codex OAuth request failed: {exc.reason}") from exc


def _expect_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    raise RuntimeError(f"Missing required string field: {key}")


def _extract_expiry(access_token: str) -> int:
    payload = _try_extract_jwt_payload(access_token)
    if not payload or not isinstance(payload.get("exp"), int):
        raise RuntimeError("Codex access token is missing expiry information")
    return int(payload["exp"]) * 1000


def _extract_client_id(access_token: str, id_token: Any = None) -> str | None:
    access_payload = _try_extract_jwt_payload(access_token) or {}
    client_id = access_payload.get("client_id")
    if isinstance(client_id, str) and client_id.strip():
        return client_id
    if isinstance(id_token, str):
        id_payload = _try_extract_jwt_payload(id_token) or {}
        audience = id_payload.get("aud")
        if isinstance(audience, list):
            for item in audience:
                if isinstance(item, str) and item.startswith("app_"):
                    return item
        if isinstance(audience, str) and audience.startswith("app_"):
            return audience
    return None


def _extract_email(payload: dict[str, Any]) -> str | None:
    profile = payload.get("https://api.openai.com/profile")
    if isinstance(profile, dict):
        email = profile.get("email")
        if isinstance(email, str) and email.strip():
            return email
    email = payload.get("email")
    if isinstance(email, str) and email.strip():
        return email
    return None


def _extract_codex_account_id(access_token: str) -> str | None:
    payload = _try_extract_jwt_payload(access_token) or {}
    direct = payload.get("account_id")
    if isinstance(direct, str) and direct.strip():
        return direct
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        account_id = auth_claim.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id
    return None


def _try_extract_jwt_payload(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    raw = parts[1]
    padding = "=" * ((4 - len(raw) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw + padding)
        payload = json.loads(decoded.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def _resolve_client_id_from_codex_home() -> str | None:
    env_client_id = os.environ.get("OVERSEER_OPENAI_CODEX_CLIENT_ID", "").strip()
    if env_client_id:
        return env_client_id
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return None
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token")
    id_token = tokens.get("id_token")
    if isinstance(access_token, str):
        return _extract_client_id(access_token, id_token)
    return None


def _extract_code_from_redirect(redirect_url: str, *, expected_state: str) -> str:
    parsed = urllib.parse.urlparse(redirect_url)
    query = urllib.parse.parse_qs(parsed.query)
    state = query.get("state", [""])[0]
    if state != expected_state:
        raise RuntimeError("OAuth state mismatch")
    code = query.get("code", [""])[0]
    if not code:
        raise RuntimeError("Redirect URL did not include an authorization code")
    return code


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _random_string(length: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


class _LocalOAuthCallbackServer:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self._queue: Queue[str] = Queue(maxsize=1)
        self._server = ThreadingHTTPServer((CODEX_REDIRECT_HOST, CODEX_REDIRECT_PORT), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_class(self):
        queue = self._queue
        expected_state = self.expected_state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CODEX_REDIRECT_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                state = params.get("state", [""])[0]
                code = params.get("code", [""])[0]
                if state != expected_state or not code:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid OAuth callback")
                    return
                if queue.empty():
                    queue.put(code)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication complete.</h1>You can return to Overseer.</body></html>")

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def start(self) -> None:
        self._thread.start()

    def wait_for_code(self, timeout_seconds: float) -> str:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except Exception as exc:  # pragma: no cover - queue raises Empty, kept narrow in behavior
            raise TimeoutError("Timed out waiting for OAuth callback") from exc

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)
