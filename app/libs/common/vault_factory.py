from __future__ import annotations

from .config import Settings, get_settings
from .vault_client import FakeVaultClient, VaultClient

_singleton: VaultClient | None = None


def build_vault_client(settings: Settings | None = None) -> VaultClient:
    settings = settings or get_settings()
    if settings.vault_client == "real":
        from .vault_client_veeva import VeevaVaultClient

        return VeevaVaultClient(settings)
    if settings.vault_client == "filesystem":
        from .vault_client_fs import FilesystemVaultClient

        return FilesystemVaultClient(settings)
    return FakeVaultClient()


def get_vault_client() -> VaultClient:
    """Process-wide Vault client (connection pool and circuit breaker are shared)."""
    global _singleton
    if _singleton is None:
        _singleton = build_vault_client()
    return _singleton


def set_vault_client(client: VaultClient | None) -> None:
    global _singleton
    _singleton = client


async def close_vault_client() -> None:
    global _singleton
    if _singleton is not None:
        try:
            await _singleton.end_session()
        except Exception:  # a failed logout must not block shutdown
            pass
        await _singleton.aclose()
    _singleton = None
