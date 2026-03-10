from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from overseer.fs import atomic_write_text
from overseer.locks import file_lock


@dataclass(frozen=True)
class OAuthCredentialRecord:
    kind: str
    provider_id: str
    access_token: str
    refresh_token: str
    expires_at: int
    account_id: str | None = None
    email: str | None = None
    metadata: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OAuthCredentialRecord":
        metadata = payload.get("metadata")
        normalized_metadata = {str(k): str(v) for k, v in metadata.items()} if isinstance(metadata, dict) else None
        return cls(
            kind=str(payload.get("kind", "oauth")),
            provider_id=str(payload["provider_id"]),
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=int(payload["expires_at"]),
            account_id=_optional_str(payload.get("account_id")),
            email=_optional_str(payload.get("email")),
            metadata=normalized_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OAuthCredentialStore:
    def get(self, provider_id: str, profile_id: str = "default") -> OAuthCredentialRecord | None:
        raise NotImplementedError

    def put(self, provider_id: str, credential: OAuthCredentialRecord, profile_id: str = "default") -> None:
        raise NotImplementedError

    def delete(self, provider_id: str, profile_id: str = "default") -> bool:
        raise NotImplementedError

    def list(self, provider_id: str | None = None) -> list[tuple[str, str, OAuthCredentialRecord]]:
        raise NotImplementedError


class JsonOAuthCredentialStore(OAuthCredentialStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "oauth-profiles.json"
        self.lock_path = self.root / "oauth-profiles.lock"

    def get(self, provider_id: str, profile_id: str = "default") -> OAuthCredentialRecord | None:
        with file_lock(self.lock_path):
            data = self._read_unlocked()
            raw = data.get(provider_id, {}).get(profile_id)
            return OAuthCredentialRecord.from_dict(raw) if isinstance(raw, dict) else None

    def put(self, provider_id: str, credential: OAuthCredentialRecord, profile_id: str = "default") -> None:
        with file_lock(self.lock_path):
            data = self._read_unlocked()
            provider_bucket = data.setdefault(provider_id, {})
            provider_bucket[profile_id] = credential.to_dict()
            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def delete(self, provider_id: str, profile_id: str = "default") -> bool:
        with file_lock(self.lock_path):
            data = self._read_unlocked()
            provider_bucket = data.get(provider_id)
            if not isinstance(provider_bucket, dict) or profile_id not in provider_bucket:
                return False
            del provider_bucket[profile_id]
            if not provider_bucket:
                del data[provider_id]
            atomic_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
            return True

    def list(self, provider_id: str | None = None) -> list[tuple[str, str, OAuthCredentialRecord]]:
        with file_lock(self.lock_path):
            data = self._read_unlocked()
            out: list[tuple[str, str, OAuthCredentialRecord]] = []
            for current_provider, profiles in sorted(data.items()):
                if provider_id is not None and current_provider != provider_id:
                    continue
                if not isinstance(profiles, dict):
                    continue
                for profile_id, raw in sorted(profiles.items()):
                    if isinstance(raw, dict):
                        out.append((current_provider, profile_id, OAuthCredentialRecord.from_dict(raw)))
            return out

    def _read_unlocked(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}


class OAuthRefreshCoordinator:
    def __init__(self, lock_root: Path) -> None:
        self.lock_root = lock_root
        self.lock_root.mkdir(parents=True, exist_ok=True)

    def with_refresh_lock(self, provider_id: str, profile_id: str, run):
        lock_path = self.lock_root / f"oauth-refresh-{provider_id}-{profile_id}.lock"
        with file_lock(lock_path):
            return run()


def ensure_fresh_credential(
    auth_adapter,
    store: OAuthCredentialStore,
    coordinator: OAuthRefreshCoordinator,
    provider_id: str,
    profile_id: str,
    credential: OAuthCredentialRecord | None,
    *,
    now_ms: int | None = None,
    skew_ms: int = 60_000,
) -> OAuthCredentialRecord:
    now_ms = now_ms if now_ms is not None else _now_ms()
    if credential is None:
        raise RuntimeError(f"No stored credential for {provider_id}:{profile_id}")
    if credential.expires_at > now_ms + skew_ms:
        return credential

    def _refresh() -> OAuthCredentialRecord:
        latest = store.get(provider_id, profile_id)
        current_now = _now_ms()
        if latest is not None and latest.expires_at > current_now + skew_ms:
            return latest
        refreshed = auth_adapter.refresh(latest or credential)
        store.put(provider_id, refreshed, profile_id)
        return refreshed

    return coordinator.with_refresh_lock(provider_id, profile_id, _refresh)


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
