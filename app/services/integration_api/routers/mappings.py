from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.audit import record_audit
from app.libs.common.db import get_db
from app.libs.common.models import AuditAction, MappingRule
from app.libs.common.security import Principal, current_principal, require_admin

from ..schemas import MappingRuleCreate, MappingRuleOut, MappingRuleUpdate

router = APIRouter()


@router.get("", response_model=list[MappingRuleOut], summary="List mapping rules")
async def list_mappings(
    active_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(current_principal),
) -> list[MappingRuleOut]:
    stmt = select(MappingRule).order_by(MappingRule.mapping_id).limit(limit).offset(offset)
    if active_only:
        stmt = stmt.where(MappingRule.is_active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return [MappingRuleOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=MappingRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a mapping rule (Admin)",
)
async def create_mapping(
    payload: MappingRuleCreate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
) -> MappingRuleOut:
    rule = MappingRule(
        **payload.model_dump(),
        created_by=principal.subject,
        updated_by=principal.subject,
    )
    session.add(rule)
    await session.flush()
    await record_audit(
        session,
        action=AuditAction.CONFIG_CHANGE,
        performed_by=principal.subject,
        source_system="integration-api",
        details={"operation": "CREATE_MAPPING", "mapping_id": rule.mapping_id, "values": payload.model_dump()},
    )
    return MappingRuleOut.model_validate(rule)


@router.put("/{mapping_id}", response_model=MappingRuleOut, summary="Update a mapping rule (Admin)")
async def update_mapping(
    mapping_id: int,
    payload: MappingRuleUpdate,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
) -> MappingRuleOut:
    rule = await session.get(MappingRule, mapping_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping rule not found")

    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields supplied")

    before = {key: getattr(rule, key) for key in changes}
    for key, value in changes.items():
        setattr(rule, key, value)
    rule.version = (rule.version or 1) + 1
    rule.updated_by = principal.subject
    await session.flush()
    await session.refresh(rule)

    await record_audit(
        session,
        action=AuditAction.CONFIG_CHANGE,
        performed_by=principal.subject,
        source_system="integration-api",
        details={
            "operation": "UPDATE_MAPPING",
            "mapping_id": mapping_id,
            "before": {k: str(v) for k, v in before.items()},
            "after": {k: str(v) for k, v in changes.items()},
            "version": rule.version,
        },
    )
    return MappingRuleOut.model_validate(rule)


@router.delete("/{mapping_id}", response_model=MappingRuleOut, summary="Deactivate a mapping rule (Admin)")
async def deactivate_mapping(
    mapping_id: int,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_admin),
) -> MappingRuleOut:
    rule = await session.get(MappingRule, mapping_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping rule not found")
    rule.is_active = False
    rule.version = (rule.version or 1) + 1
    rule.updated_by = principal.subject
    await session.flush()
    await session.refresh(rule)
    await record_audit(
        session,
        action=AuditAction.DELETE,
        performed_by=principal.subject,
        source_system="integration-api",
        details={"operation": "DEACTIVATE_MAPPING", "mapping_id": mapping_id},
    )
    return MappingRuleOut.model_validate(rule)
