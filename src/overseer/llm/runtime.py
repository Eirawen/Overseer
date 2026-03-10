from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from overseer.codex_store import CodexStore
from overseer.llm.base import LLMAdapter, Message
from overseer.llm.codex import (
    CODEX_DEFAULT_BASE_URL,
    CODEX_DEFAULT_MODEL,
    CODEX_PROVIDER_ID,
    CodexOAuthAdapter,
    CodexProviderConfig,
    CodexRuntimeClient,
    import_codex_cli_credential,
)
from overseer.llm.oauth import (
    JsonOAuthCredentialStore,
    OAuthCredentialRecord,
    OAuthRefreshCoordinator,
    ensure_fresh_credential,
)


@dataclass(frozen=True)
class ProviderHealth:
    adapter: str
    mode: str
    status: str
    provider_id: str | None = None
    profile_id: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "adapter": self.adapter,
            "mode": self.mode,
            "status": self.status,
        }
        if self.provider_id is not None:
            payload["provider_id"] = self.provider_id
        if self.profile_id is not None:
            payload["profile_id"] = self.profile_id
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class CodexLLM(LLMAdapter):
    def __init__(
        self,
        config: CodexProviderConfig,
        credential_store: JsonOAuthCredentialStore,
        refresh_coordinator: OAuthRefreshCoordinator,
        auth_adapter: CodexOAuthAdapter | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self.refresh_coordinator = refresh_coordinator
        self.auth_adapter = auth_adapter or CodexOAuthAdapter(client_id=config.client_id)

    def generate(self, system_prompt: str, messages: list[Message]) -> str:
        credential = self._resolve_runtime_credential()
        client = CodexRuntimeClient(credential=credential, base_url=self.config.base_url)
        chunks: list[str] = []
        final_message: str | None = None
        error_message: str | None = None
        for event in client.stream_chat(system_prompt=system_prompt, messages=messages, model=self.config.model):
            if event.type == "text-delta" and event.text:
                chunks.append(event.text)
            elif event.type == "assistant-message" and event.text:
                final_message = event.text
            elif event.type == "error" and event.message:
                error_message = event.message
                break
        if error_message is not None:
            raise RuntimeError(error_message)
        if chunks:
            return "".join(chunks)
        if final_message:
            return final_message
        return "No response returned from Codex."

    def health(self) -> dict[str, Any]:
        stored = self.credential_store.get(self.config.provider_id, self.config.profile_id)
        if stored is not None:
            return ProviderHealth(
                adapter=self.__class__.__name__,
                mode="configured",
                status="ok",
                provider_id=self.config.provider_id,
                profile_id=self.config.profile_id,
            ).to_dict()
        import_error = self._import_probe_error()
        if import_error is None:
            return ProviderHealth(
                adapter=self.__class__.__name__,
                mode="cli_reusable",
                status="ok",
                provider_id=self.config.provider_id,
                profile_id=self.config.profile_id,
                detail="Existing Codex CLI credentials can be imported automatically.",
            ).to_dict()
        return ProviderHealth(
            adapter=self.__class__.__name__,
            mode="missing_credentials",
            status="degraded",
            provider_id=self.config.provider_id,
            profile_id=self.config.profile_id,
            detail=import_error,
        ).to_dict()

    def _resolve_runtime_credential(self) -> OAuthCredentialRecord:
        stored = self.credential_store.get(self.config.provider_id, self.config.profile_id)
        if stored is None:
            stored = self._maybe_import_codex_cli_credential()
        if stored is None:
            raise RuntimeError(
                "No Codex OAuth credentials are available. Run `overseer auth import-codex-cli` or `overseer auth login --provider openai-codex`."
            )
        return ensure_fresh_credential(
            self.auth_adapter,
            self.credential_store,
            self.refresh_coordinator,
            self.config.provider_id,
            self.config.profile_id,
            stored,
        )

    def _maybe_import_codex_cli_credential(self) -> OAuthCredentialRecord | None:
        try:
            credential = import_codex_cli_credential()
        except (FileNotFoundError, RuntimeError):
            return None
        self.credential_store.put(self.config.provider_id, credential, self.config.profile_id)
        return credential

    def _import_probe_error(self) -> str | None:
        try:
            import_codex_cli_credential()
        except FileNotFoundError:
            return "No Overseer credential stored and no ~/.codex/auth.json found."
        except RuntimeError as exc:
            return str(exc)
        return None


def build_runtime_llm(codex_store: CodexStore) -> CodexLLM:
    auth_root = codex_store.codex_root / "10_OVERSEER" / "auth"
    codex_store.assert_write_allowed("overseer", auth_root)
    store = JsonOAuthCredentialStore(auth_root)
    coordinator = OAuthRefreshCoordinator(codex_store.codex_root / "10_OVERSEER" / "locks")
    return CodexLLM(
        config=CodexProviderConfig(
            provider_id=CODEX_PROVIDER_ID,
            profile_id="default",
            base_url=_env("OVERSEER_LLM_BASE_URL", default=CODEX_DEFAULT_BASE_URL),
            model=_env("OVERSEER_LLM_MODEL", default=CODEX_DEFAULT_MODEL),
            client_id=_env("OVERSEER_OPENAI_CODEX_CLIENT_ID"),
        ),
        credential_store=store,
        refresh_coordinator=coordinator,
    )


def _env(name: str, *, default: str | None = None) -> str | None:
    import os

    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    return value
