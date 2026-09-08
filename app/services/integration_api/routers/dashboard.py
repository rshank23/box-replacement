from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.config import Settings, get_settings
from app.libs.common.db import get_db
from app.libs.common.metrics import FAILURE_QUEUE_DEPTH, MAPPING_HIT_RATE, SAM_QUEUE_DEPTH
from app.libs.common.models import (
    FailureQueue,
    FolderWatch,
    ResolutionStatus,
    SamActionQueue,
    SamStatus,
    TransferLog,
    TransferStatus,
)
from app.libs.common.security import Principal, current_principal

from ..schemas import DashboardStats, FolderAgeOut, SamActionOut

router = APIRouter()


def _alert_level(age_days: int, settings: Settings) -> str:
    if age_days >= settings.folder_age_critical_days:
        return "CRITICAL"
    if age_days >= settings.folder_age_warn_days:
        return "WARNING"
    return "OK"


@router.get("/stats", response_model=DashboardStats, summary="Operational dashboard counters")
async def stats(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(current_principal),
) -> DashboardStats:
    status_rows = await session.execute(
        select(TransferLog.status, func.count()).group_by(TransferLog.status)
    )
    transfers_by_status = {row[0]: row[1] for row in status_rows.all()}

    reason_rows = await session.execute(
        select(FailureQueue.failure_reason, func.count()).group_by(FailureQueue.failure_reason)
    )
    failures_by_reason = {row[0]: row[1] for row in reason_rows.all()}

    open_failures = (
        await session.execute(
            select(func.count())
            .select_from(FailureQueue)
            .where(FailureQueue.resolution_status == ResolutionStatus.OPEN.value)
        )
    ).scalar_one()
    open_sam = (
        await session.execute(
            select(func.count()).select_from(SamActionQueue).where(SamActionQueue.status != SamStatus.COMPLETED.value)
        )
    ).scalar_one()

    total = sum(transfers_by_status.values())
    mapped = (
        await session.execute(
            select(func.count()).select_from(TransferLog).where(TransferLog.mapping_id_used.is_not(None))
        )
    ).scalar_one()
    hit_rate = (mapped / total) if total else 0.0

    documents = transfers_by_status.get(TransferStatus.SUCCESS.value, 0)

    now = datetime.now(UTC)
    folder_rows = (await session.execute(select(FolderWatch))).scalars().all()
    aging: list[FolderAgeOut] = []
    for folder in folder_rows:
        first_seen = folder.first_seen_at
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=UTC)
        age = (now - first_seen).days
        level = _alert_level(age, settings)
        if level != "OK":
            aging.append(
                FolderAgeOut(folder_path=folder.folder_path, study=folder.study, age_days=age, alert_level=level)
            )

    FAILURE_QUEUE_DEPTH.set(open_failures)
    SAM_QUEUE_DEPTH.set(open_sam)
    MAPPING_HIT_RATE.set(hit_rate)

    return DashboardStats(
        transfers_by_status=transfers_by_status,
        failures_by_reason=failures_by_reason,
        open_failures=int(open_failures),
        open_sam_items=int(open_sam),
        mapping_hit_rate=round(hit_rate, 4),
        total_transfers=int(total),
        documents_in_vault=int(documents),
        aging_folders=sorted(aging, key=lambda f: f.age_days, reverse=True),
        generated_at=now,
    )


@router.get("/sam-queue", response_model=list[SamActionOut], summary="Items awaiting a SAM picklist request")
async def sam_queue(
    session: AsyncSession = Depends(get_db),
    _: Principal = Depends(current_principal),
) -> list[SamActionOut]:
    rows = (
        await session.execute(
            select(SamActionQueue)
            .where(SamActionQueue.status != SamStatus.COMPLETED.value)
            .order_by(SamActionQueue.sam_id.desc())
        )
    ).scalars().all()
    return [SamActionOut.model_validate(r) for r in rows]
