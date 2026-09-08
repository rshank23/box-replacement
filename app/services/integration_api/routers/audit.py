from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.db import get_db
from app.libs.common.models import AuditTrail
from app.libs.common.security import Principal, current_principal

from ..schemas import AuditOut

router = APIRouter()


@router.get("", response_model=list[AuditOut], summary="Query the immutable audit trail")
async def query_audit(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    action: str | None = Query(default=None, max_length=40),
    performed_by: str | None = Query(default=None, max_length=100),
    correlation_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(current_principal),
) -> list[AuditOut]:
    stmt = select(AuditTrail).order_by(AuditTrail.timestamp.desc()).limit(limit).offset(offset)
    if from_:
        stmt = stmt.where(AuditTrail.timestamp >= from_)
    if to:
        stmt = stmt.where(AuditTrail.timestamp <= to)
    if action:
        stmt = stmt.where(AuditTrail.action == action.upper())
    if performed_by:
        stmt = stmt.where(AuditTrail.performed_by == performed_by)
    if correlation_id:
        stmt = stmt.where(AuditTrail.correlation_id == correlation_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [AuditOut.model_validate(r) for r in rows]
