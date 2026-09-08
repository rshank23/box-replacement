from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.db import get_db
from app.libs.common.models import TransferLog
from app.libs.common.security import Principal, current_principal

from ..deps import correlation_id, orchestrator_dependency
from ..domain.orchestrator import Orchestrator, SubmissionItem
from ..schemas import TransferOut, TransferSubmitRequest, TransferSubmitResponse

router = APIRouter()


@router.post(
    "/submit",
    response_model=TransferSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit MBox files for archival into VTMF",
)
async def submit(
    payload: TransferSubmitRequest,
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    cid: uuid.UUID = Depends(correlation_id),
) -> TransferSubmitResponse:
    batch_correlation_id = payload.correlation_id or cid
    items = [
        SubmissionItem(
            source_path=f.source_path,
            initiated_by=f.initiated_by,
            staged_path=f.staged_path,
            checksum=f.checksum,
            override_duplicate=f.override_duplicate,
            metadata_override=f.metadata_override,
        )
        for f in payload.files
    ]
    results = await orchestrator.process_batch(items, batch_correlation_id)
    return TransferSubmitResponse(
        correlation_id=batch_correlation_id,
        submitted=len(results),
        results=[TransferOut.model_validate(r) for r in results],
    )


@router.get("", response_model=list[TransferOut], summary="List transfers")
async def list_transfers(
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    study: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(current_principal),
) -> list[TransferOut]:
    stmt = select(TransferLog).order_by(TransferLog.created_at.desc()).limit(limit).offset(offset)
    if status_filter:
        stmt = stmt.where(TransferLog.status == status_filter.upper())
    if study:
        stmt = stmt.where(TransferLog.source_path.like(f"{study}/%"))
    rows = (await session.execute(stmt)).scalars().all()
    return [TransferOut.model_validate(r) for r in rows]


@router.get("/{transfer_id}", response_model=TransferOut, summary="Get one transfer")
async def get_transfer(
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(current_principal),
) -> TransferOut:
    row = await session.get(TransferLog, transfer_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    return TransferOut.model_validate(row)


@router.post("/{transfer_id}/retry", response_model=TransferOut, summary="Retry a transfer")
async def retry_transfer(
    transfer_id: uuid.UUID,
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
) -> TransferOut:
    row = await orchestrator.session.get(TransferLog, transfer_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    result = await orchestrator.retry_transfer(row)
    return TransferOut.model_validate(result)
