from __future__ import annotations

import base64
import json
from pathlib import Path

from overseer.codex_store import CodexStore
from overseer.llm.codex import CODEX_PROVIDER_ID, CodexProviderConfig, import_codex_cli_credential
from overseer.llm.oauth import JsonOAuthCredentialStore, OAuthCredentialRecord, OAuthRefreshCoordinator
from overseer.llm.runtime import CodexLLM


def _fake_jwt(payload: dict[str, object]) -> str:
    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64(payload)
    return f"{header}.{body}.signature"


def _b64(payload: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8").rstrip("=")


ACCESS_TOKEN = _fake_jwt(
    {
        "exp": 4102444800,
        "client_id": "app_test123",
        "https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"},
        "https://api.openai.com/profile": {"email": "test@example.com"},
    }
)


def test_json_oauth_credential_store_round_trips(tmp_path: Path) -> None:
    store = JsonOAuthCredentialStore(tmp_path)
    credential = OAuthCredentialRecord(
        kind="oauth",
        provider_id=CODEX_PROVIDER_ID,
        access_token="access",
        refresh_token="refresh",
        expires_at=4102444800000,
        account_id="acct-1",
        email="test@example.com",
        metadata={"client_id": "app_test123"},
    )

    store.put(CODEX_PROVIDER_ID, credential, "default")

    loaded = store.get(CODEX_PROVIDER_ID, "default")
    assert loaded == credential
    listed = store.list(CODEX_PROVIDER_ID)
    assert listed == [(CODEX_PROVIDER_ID, "default", credential)]
    assert store.delete(CODEX_PROVIDER_ID, "default") is True
    assert store.get(CODEX_PROVIDER_ID, "default") is None


def test_import_codex_cli_credential_reads_codex_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    auth_dir = home / ".codex"
    auth_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    payload = {
        "tokens": {
            "access_token": ACCESS_TOKEN,
            "refresh_token": "refresh-token",
            "account_id": "acct-1",
            "id_token": ACCESS_TOKEN,
        }
    }
    (auth_dir / "auth.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    credential = import_codex_cli_credential()

    assert credential.provider_id == CODEX_PROVIDER_ID
    assert credential.account_id == "acct-1"
    assert credential.email == "test@example.com"
    assert credential.metadata == {"source": "codex-cli-import", "client_id": "app_test123"}


def test_codex_llm_health_reports_missing_credentials(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "codex").mkdir()
    store = CodexStore(repo)
    store.init_structure()
    monkeypatch.setenv("HOME", str(tmp_path / "missing-home"))
    llm = CodexLLM(
        config=CodexProviderConfig(),
        credential_store=JsonOAuthCredentialStore(store.codex_root / "10_OVERSEER" / "auth"),
        refresh_coordinator=OAuthRefreshCoordinator(store.codex_root / "10_OVERSEER" / "locks"),
    )
    health = llm.health()
    assert health["mode"] == "missing_credentials"
    assert health["status"] == "degraded"
