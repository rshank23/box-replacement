from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.libs.common.audit import record_audit
from app.libs.common.models import (
    AuditAction,
    FailureQueue,
    MappingRule,
    ResolutionStatus,
    TransferLog,
    TransferStatus,
)
from app.libs.common.security import Principal, current_principal

from ..deps import orchestrator_dependency
from ..domain.orchestrator import Orchestrator
from ..schemas import FailureOut, FailureResolveRequest, FailureResolveResponse, TransferOut

router = APIRouter()


@router.get("", response_model=list[FailureOut], summary="List failure queue entries")
async def list_failures(
    resolution_status: str | None = Query(default="OPEN", alias="status", max_length=20),
    reason: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    _: Principal = Depends(current_principal),
) -> list[FailureOut]:
    stmt = select(FailureQueue).order_by(FailureQueue.failure_id.desc()).limit(limit).offset(offset)
    if resolution_status and resolution_status.upper() != "ALL":
        stmt = stmt.where(FailureQueue.resolution_status == resolution_status.upper())
    if reason:
        stmt = stmt.where(FailureQueue.failure_reason == reason.upper())
    rows = (await orchestrator.session.execute(stmt)).scalars().all()
    return [FailureOut.model_validate(r) for r in rows]


@router.put(
    "/{failure_id}/resolve",
    response_model=FailureResolveResponse,
    summary="Resolve a failure by supplying a mapping override and re-running the transfer",
)
async def resolve_failure(
    failure_id: int,
    payload: FailureResolveRequest,
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    principal: Principal = Depends(current_principal),
) -> FailureResolveResponse:
    session = orchestrator.session
    failure = await session.get(FailureQueue, failure_id)
    if failure is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Failure not found")
    if failure.resolution_status == ResolutionStatus.RESOLVED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Failure is already resolved")

    transfer = await session.get(TransferLog, failure.transfer_id)
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked transfer not found")

    override = payload.mapping_override.model_dump(exclude_none=True)
    if not override:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mapping_override must not be empty")

    if payload.persist_as_rule:
        suggested = failure.suggested_mapping or {}
        session.add(
            MappingRule(
                source_pattern=str(suggested.get("source_pattern") or transfer.source_path),
                match_type=str(suggested.get("match_type") or "EXACT"),
                target_study=override.get("target_study"),
                target_country=override.get("target_country"),
                target_site=override.get("target_site"),
                document_type=override.get("document_type"),
                document_subtype=override.get("document_subtype"),
                classification=override.get("classification"),
                default_metadata=override.get("default_metadata") or {},
                created_by=principal.subject,
                updated_by=principal.subject,
            )
        )
        await session.flush()

    result = await orchestrator.retry_transfer(transfer, metadata_override=override)

    if result.status == TransferStatus.SUCCESS.value:
        failure.resolution_status = ResolutionStatus.RESOLVED.value
        failure.resolved_at = datetime.now(UTC)
    failure.assigned_to = principal.subject

    await record_audit(
        session,
        action=AuditAction.CONFIG_CHANGE if payload.persist_as_rule else AuditAction.MAP,
        performed_by=principal.subject,
        source_system="ui",
        correlation_id=transfer.correlation_id,
        details={
            "failure_id": failure_id,
            "transfer_id": str(transfer.transfer_id),
            "override": override,
            "persisted_as_rule": payload.persist_as_rule,
            "reason": payload.reason,
            "resulting_status": result.status,
        },
    )
    await session.flush()

    return FailureResolveResponse(
        failure_id=failure_id,
        transfer=TransferOut.model_validate(result),
        resolution_status=failure.resolution_status,
    )


@router.put("/{failure_id}/escalate", response_model=FailureOut, summary="Escalate a failure")
async def escalate_failure(
    failure_id: int,
    assignee: str | None = Query(default=None, max_length=100),
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    principal: Principal = Depends(current_principal),
) -> FailureOut:
    session = orchestrator.session
    failure = await session.get(FailureQueue, failure_id)
    if failure is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Failure not found")
    failure.resolution_status = ResolutionStatus.ESCALATED.value
    failure.assigned_to = assignee or principal.subject
    await record_audit(
        session,
        action=AuditAction.CLASSIFY,
        performed_by=principal.subject,
        source_system="ui",
        details={"failure_id": failure_id, "escalated_to": failure.assigned_to},
    )
    await session.flush()
    return FailureOut.model_validate(failure)
