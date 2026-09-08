from __future__ import annotations

import uuid

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.config import Settings, get_settings
from app.libs.common.db import get_db
from app.libs.common.logging_config import bind_correlation_id
from app.libs.common.security import Principal, current_principal, require_admin
from app.libs.common.vault_client import VaultClient
from app.libs.common.vault_factory import get_vault_client

from .domain.orchestrator import Orchestrator


async def correlation_id(x_correlation_id: str | None = Header(default=None)) -> uuid.UUID:
    try:
        cid = uuid.UUID(x_correlation_id) if x_correlation_id else uuid.uuid4()
    except ValueError:
        cid = uuid.uuid4()
    bind_correlation_id(cid)
    return cid


async def vault_dependency() -> VaultClient:
    return get_vault_client()


async def orchestrator_dependency(
    session: AsyncSession = Depends(get_db),
    vault: VaultClient = Depends(vault_dependency),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(current_principal),
) -> Orchestrator:
    return Orchestrator(session, vault, settings=settings, performed_by=principal.subject)


__all__ = [
    "Principal",
    "correlation_id",
    "current_principal",
    "get_db",
    "get_settings",
    "orchestrator_dependency",
    "require_admin",
    "vault_dependency",
]
