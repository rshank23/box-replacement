from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common import mbox
from app.libs.common.archive import CorruptArchiveError, extract_archive
from app.libs.common.audit import record_audit
from app.libs.common.config import Settings, get_settings
from app.libs.common.db import session_scope
from app.libs.common.hashing import sha256_file
from app.libs.common.logging_config import bind_correlation_id, get_logger
from app.libs.common.metrics import FOLDER_AGE_DAYS, TRANSFERS_DUPLICATE, WATCHER_LAST_RUN, record_failure
from app.libs.common.models import (
    AuditAction,
    FailureQueue,
    FailureReason,
    FolderWatch,
    InitiatedBy,
    ResolutionStatus,
    TransferLog,
    TransferStatus,
)
from app.libs.common.path_parser import normalize_path, parse_path
from app.libs.common.security import issue_dev_token

log = get_logger("watcher")

#: Files are submitted to the Integration API in chunks to bound request size.
BATCH_SIZE = 50


@dataclass
class Candidate:
    source_path: str  # relative to MBOX_ROOT, may contain the "!/" archive separator
    real_path: Path
    checksum: str
    size_bytes: int


@dataclass
class ScanResult:
    scanned: int = 0
    submitted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    corrupt_archives: int = 0


def _service_token(settings: Settings) -> str:
    return issue_dev_token(settings.service_account_id, [settings.admin_role], ttl_seconds=900)


async def _is_known_success(session: AsyncSession, source_path: str, checksum: str) -> bool:
    stmt = select(TransferLog.transfer_id).where(
        TransferLog.source_path == source_path,
        TransferLog.file_checksum == checksum,
        TransferLog.status == TransferStatus.SUCCESS.value,
    )
    return (await session.execute(stmt)).first() is not None


async def _record_duplicate(session: AsyncSession, candidate: Candidate, correlation_id: uuid.UUID) -> None:
    transfer = TransferLog(
        transfer_id=uuid.uuid4(),
        source_path=candidate.source_path,
        file_name=Path(candidate.source_path).name,
        file_checksum=candidate.checksum,
        file_size_bytes=candidate.size_bytes,
        status=TransferStatus.DUPLICATE_SKIPPED.value,
        initiated_by=InitiatedBy.SYSTEM.value,
        message="Identical path and checksum already archived; skipped by the watcher.",
        correlation_id=correlation_id,
    )
    session.add(transfer)
    await session.flush()
    TRANSFERS_DUPLICATE.inc()
    await record_audit(
        session,
        action=AuditAction.SCAN,
        performed_by=get_settings().service_account_id,
        source_system="watcher",
        correlation_id=correlation_id,
        details={"transfer_id": str(transfer.transfer_id), "status": transfer.status},
    )


async def _record_corrupt_archive(
    session: AsyncSession, source_path: str, detail: str, correlation_id: uuid.UUID
) -> None:
    transfer = TransferLog(
        transfer_id=uuid.uuid4(),
        source_path=source_path,
        file_name=Path(source_path).name,
        status=TransferStatus.EXCEPTION.value,
        initiated_by=InitiatedBy.SYSTEM.value,
        message=detail[:4000],
        correlation_id=correlation_id,
    )
    session.add(transfer)
    await session.flush()
    session.add(
        FailureQueue(
            transfer_id=transfer.transfer_id,
            failure_reason=FailureReason.CORRUPT_ARCHIVE.value,
            failure_detail=detail[:4000],
            resolution_status=ResolutionStatus.OPEN.value,
        )
    )
    record_failure(FailureReason.CORRUPT_ARCHIVE.value, parse_path(source_path).study)
    await record_audit(
        session,
        action=AuditAction.SCAN,
        performed_by=get_settings().service_account_id,
        source_system="watcher",
        correlation_id=correlation_id,
        details={"transfer_id": str(transfer.transfer_id), "reason": FailureReason.CORRUPT_ARCHIVE.value},
    )


async def _track_folder_ages(session: AsyncSession, settings: Settings) -> None:
    """Upsert first-seen timestamps per study folder and emit 60/75-day alerts."""
    root = Path(settings.mbox_root).expanduser().resolve()
    if not root.exists():
        return
    now = datetime.now(UTC)
    existing = {f.folder_path: f for f in (await session.execute(select(FolderWatch))).scalars().all()}

    for study_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        rel = study_dir.relative_to(root).as_posix()
        watch = existing.get(rel)
        if watch is None:
            watch = FolderWatch(folder_path=rel, study=study_dir.name, first_seen_at=now, last_seen_at=now)
            session.add(watch)
        else:
            watch.last_seen_at = now

        first_seen = watch.first_seen_at or now
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=UTC)
        age_days = (now - first_seen).days
        FOLDER_AGE_DAYS.labels(study=study_dir.name).set(age_days)

        level = None
        if age_days >= settings.folder_age_critical_days:
            level = "CRITICAL"
        elif age_days >= settings.folder_age_warn_days:
            level = "WARNING"
        if level and watch.last_alert_level != level:
            watch.last_alert_level = level
            log.warning(
                "mbox_folder_aging",
                study=study_dir.name,
                folder=rel,
                age_days=age_days,
                retention_days=settings.folder_retention_days,
                alert_level=level,
            )
    await session.flush()


def _collect_candidates(
    settings: Settings, staging_root: Path, result: ScanResult
) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Walk MBox, expanding archives into ``staging_root``.

    Returns the ingestible candidates plus ``(source_path, detail)`` pairs for archives
    that could not be read.
    """
    candidates: list[Candidate] = []
    corrupt: list[tuple[str, str]] = []
    allowed = settings.allowed_extension_set

    for path in mbox.iter_source_files(settings):
        rel = mbox.relative_of(path, settings)
        result.scanned += 1
        info = parse_path(rel)

        if info.extension not in allowed:
            result.skipped_invalid += 1
            log.info("file_skipped", reason="extension_not_allowed", file=rel)
            continue
        if info.depth < settings.min_folder_depth:
            result.skipped_invalid += 1
            log.info("file_skipped", reason="folder_depth_below_minimum", file=rel, depth=info.depth)
            continue

        size = path.stat().st_size
        if size == 0 or size > settings.max_file_bytes:
            result.skipped_invalid += 1
            log.info("file_skipped", reason="size_out_of_bounds", file=rel, size_bytes=size)
            continue

        if info.extension == ".zip":
            dest = staging_root / normalize_path(rel).replace("/", "__")
            try:
                members = extract_archive(
                    path,
                    dest,
                    virtual_prefix=rel,
                    max_depth=settings.zip_max_depth,
                    max_file_bytes=settings.max_file_bytes,
                )
            except CorruptArchiveError as exc:
                result.corrupt_archives += 1
                corrupt.append((rel, str(exc)))
                log.error("archive_unreadable", file=rel, error=str(exc))
                continue
            for member in members:
                member_ext = member.real_path.suffix.lower()
                if member_ext not in allowed or member_ext == ".zip":
                    result.skipped_invalid += 1
                    continue
                candidates.append(
                    Candidate(
                        source_path=normalize_path(member.virtual_path),
                        real_path=member.real_path,
                        checksum=sha256_file(member.real_path),
                        size_bytes=member.size_bytes,
                    )
                )
            continue

        candidates.append(
            Candidate(source_path=rel, real_path=path, checksum=sha256_file(path), size_bytes=size)
        )
    return candidates, corrupt


async def _submit(
    client: httpx.AsyncClient, batch: list[Candidate], correlation_id: uuid.UUID, settings: Settings
) -> int:
    payload = {
        "correlation_id": str(correlation_id),
        "files": [
            {
                "source_path": c.source_path,
                "initiated_by": InitiatedBy.SYSTEM.value,
                "staged_path": str(c.real_path),
                "checksum": c.checksum,
            }
            for c in batch
        ],
    }
    response = await client.post(
        f"{settings.integration_api_base_url.rstrip('/')}/api/transfers/submit",
        json=payload,
        headers={
            "Authorization": f"Bearer {_service_token(settings)}",
            "X-Correlation-Id": str(correlation_id),
        },
    )
    if response.status_code >= 400:
        log.error("submit_failed", status_code=response.status_code, batch_size=len(batch))
        return 0
    return len(batch)


async def scan_and_enqueue() -> ScanResult:
    """One full MBox scan: discover, de-duplicate and submit files for archival."""
    settings = get_settings()
    correlation_id = uuid.uuid4()
    bind_correlation_id(correlation_id)
    started = time.perf_counter()
    result = ScanResult()

    staging_root = Path(tempfile.mkdtemp(prefix="mbox-watch-"))
    try:
        candidates, corrupt = _collect_candidates(settings, staging_root, result)

        async with session_scope() as session:
            await _track_folder_ages(session, settings)
            for source_path, detail in corrupt:
                await _record_corrupt_archive(session, source_path, detail, correlation_id)

            pending: list[Candidate] = []
            for candidate in candidates:
                if await _is_known_success(session, candidate.source_path, candidate.checksum):
                    await _record_duplicate(session, candidate, correlation_id)
                    result.skipped_duplicate += 1
                else:
                    pending.append(candidate)

        if pending:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
                for start in range(0, len(pending), BATCH_SIZE):
                    batch = pending[start : start + BATCH_SIZE]
                    result.submitted += await _submit(client, batch, correlation_id, settings)

        WATCHER_LAST_RUN.set(time.time())
        log.info(
            "watcher_scan_complete",
            duration_s=round(time.perf_counter() - started, 3),
            scanned=result.scanned,
            submitted=result.submitted,
            duplicates=result.skipped_duplicate,
            invalid=result.skipped_invalid,
            corrupt_archives=result.corrupt_archives,
        )
        return result
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
