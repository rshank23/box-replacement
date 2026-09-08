from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.audit import record_audit
from app.libs.common.config import Settings, get_settings
from app.libs.common.db import get_db
from app.libs.common.models import AuditAction, SamActionQueue, SamStatus
from app.libs.common.security import Principal, issue_dev_token, require_admin
from app.libs.common.vault_client import VaultClient

from ..deps import vault_dependency
from ..domain.validation import refresh_reference_data
from ..schemas import SamActionOut

router = APIRouter()


class PicklistRefreshResponse(BaseModel):
    refreshed_at: datetime
    counts: dict[str, int]


class DevTokenRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    roles: list[str] = Field(default_factory=list, max_length=20)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post(
    "/picklists/refresh",
    response_model=PicklistRefreshResponse,
    summary="Reload the local VTMF reference-data cache (Admin)",
)
async def refresh_picklist_cache(
    session: AsyncSession = Depends(get_db),
    vault: VaultClient = Depends(vault_dependency),
    principal: Principal = Depends(require_admin),
) -> PicklistRefreshResponse:
    await vault.authenticate()
    counts = await refresh_reference_data(session, vault)
    await record_audit(
        session,
        action=AuditAction.CONFIG_CHANGE,
        performed_by=principal.subject,
        source_system="integration-api",
        details={"operation": "PICKLIST_REFRESH", "counts": counts},
    )
    return PicklistRefreshResponse(refreshed_at=datetime.now(UTC), counts=counts)


@router.get(
    "/vault/versions",
    response_model=dict[str, str],
    summary="Retrieve the Vault API versions this Vault exposes (Admin)",
)
async def vault_api_versions(
    vault: VaultClient = Depends(vault_dependency),
    _: Principal = Depends(require_admin),
) -> dict[str, str]:
    await vault.authenticate()
    return await vault.get_api_versions()


@router.put(
    "/sam/{sam_id}/status",
    response_model=SamActionOut,
    summary="Advance a SAM action queue item (Admin)",
)
async def update_sam_status(
    sam_id: int,
    new_status: SamStatus,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
) -> SamActionOut:
    item = await session.get(SamActionQueue, sam_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SAM item not found")
    item.status = new_status.value
    if new_status is SamStatus.COMPLETED:
        item.completed_at = datetime.now(UTC)
    await record_audit(
        session,
        action=AuditAction.SAM_REQUEST,
        performed_by=principal.subject,
        source_system="integration-api",
        details={"sam_id": sam_id, "status": new_status.value},
    )
    await session.flush()
    return SamActionOut.model_validate(item)


@router.get("/sam", response_model=list[SamActionOut], summary="List SAM action queue items (Admin)")
async def list_sam(
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_admin),
) -> list[SamActionOut]:
    rows = (await session.execute(select(SamActionQueue).order_by(SamActionQueue.sam_id.desc()))).scalars().all()
    return [SamActionOut.model_validate(r) for r in rows]


@router.post("/dev-token", response_model=DevTokenResponse, summary="Mint a local development token")
async def dev_token(payload: DevTokenRequest, settings: Settings = Depends(get_settings)) -> DevTokenResponse:
    if not settings.dev_token_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    token = issue_dev_token(payload.subject, payload.roles, payload.ttl_seconds)
    return DevTokenResponse(access_token=token, expires_in=payload.ttl_seconds)
