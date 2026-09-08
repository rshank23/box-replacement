from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.libs.common import mbox
from app.libs.common.archive import CorruptArchiveError, extract_archive
from app.libs.common.audit import record_audit
from app.libs.common.config import Settings, get_settings
from app.libs.common.logging_config import get_logger
from app.libs.common.models import AuditAction
from app.libs.common.path_parser import normalize_path, parse_path
from app.libs.common.security import Principal, current_principal, require_admin
from app.libs.common.vault_client import VaultClient

from ..deps import orchestrator_dependency, vault_dependency
from ..domain.orchestrator import Orchestrator, SubmissionItem
from ..schemas import TransferOut

router = APIRouter()
log = get_logger("poc")


class PocRunRequest(BaseModel):
    #: The POC is meant to be re-run, so previously archived files are re-processed.
    override_duplicate: bool = True


class PocRunResponse(BaseModel):
    correlation_id: uuid.UUID
    submitted: int
    skipped_extension: int
    skipped_depth: int
    results: list[TransferOut]


class PocCounts(BaseModel):
    source: int
    destination: int
    unclassified: int


class PocStatus(BaseModel):
    vault_client: str
    source_root: str
    destination_root: str
    unclassified_root: str
    rate_limit_per_sec: float
    unmapped_policy: str
    zip_max_depth: int
    counts: PocCounts
    generated_at: datetime


def _discover(settings: Settings) -> tuple[list[str], int, int]:
    """Enumerate every ingestible source file, expanding archives to virtual paths."""
    allowed = settings.allowed_extension_set
    paths: list[str] = []
    skipped_extension = 0
    skipped_depth = 0

    for path in mbox.iter_source_files(settings):
        relative = mbox.relative_of(path, settings)
        info = parse_path(relative)

        if info.extension not in allowed:
            skipped_extension += 1
            continue
        if info.depth < settings.min_folder_depth:
            skipped_depth += 1
            continue

        if info.extension != ".zip":
            paths.append(relative)
            continue

        # Archive members are submitted individually so each gets its own audit trail.
        try:
            with tempfile.TemporaryDirectory(prefix="poc-scan-") as staging:
                members = extract_archive(
                    path,
                    Path(staging),
                    virtual_prefix=relative,
                    max_depth=settings.zip_max_depth,
                    max_file_bytes=settings.max_file_bytes,
                )
            for member in members:
                if Path(member.virtual_path).suffix.lower() in allowed:
                    paths.append(normalize_path(member.virtual_path))
                else:
                    skipped_extension += 1
        except CorruptArchiveError:
            # Submit the archive itself; the orchestrator records CORRUPT_ARCHIVE and files nothing.
            paths.append(relative)

    return paths, skipped_extension, skipped_depth


@router.get("/status", response_model=PocStatus, summary="POC folder configuration and file counts")
async def poc_status(
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(current_principal),
) -> PocStatus:
    return PocStatus(
        vault_client=settings.vault_client,
        source_root=settings.mbox_root,
        destination_root=settings.destination_root,
        unclassified_root=settings.unclassified_root,
        rate_limit_per_sec=settings.rate_limit_per_sec,
        unmapped_policy=settings.unmapped_policy,
        zip_max_depth=settings.zip_max_depth,
        counts=PocCounts(
            source=mbox.count_files(settings, "source"),
            destination=mbox.count_files(settings, "destination"),
            unclassified=mbox.count_files(settings, "unclassified"),
        ),
        generated_at=datetime.now(UTC),
    )


@router.post("/run", response_model=PocRunResponse, summary="Migrate every file in the source folder")
async def poc_run(
    payload: PocRunRequest,
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    settings: Settings = Depends(get_settings),
) -> PocRunResponse:
    paths, skipped_extension, skipped_depth = _discover(settings)
    if not paths:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No ingestible files found in the source folder"
        )

    correlation_id = uuid.uuid4()
    items = [
        SubmissionItem(
            source_path=path,
            initiated_by="USER",
            override_duplicate=payload.override_duplicate,
        )
        for path in paths
    ]
    results = await orchestrator.process_batch(items, correlation_id)

    log.info(
        "poc_run_complete",
        correlation_id=str(correlation_id),
        submitted=len(results),
        skipped_extension=skipped_extension,
        skipped_depth=skipped_depth,
    )
    return PocRunResponse(
        correlation_id=correlation_id,
        submitted=len(results),
        skipped_extension=skipped_extension,
        skipped_depth=skipped_depth,
        results=[TransferOut.model_validate(r) for r in results],
    )


@router.post("/reset", response_model=PocCounts, summary="Empty the destination and unclassified folders (Admin)")
async def poc_reset(
    orchestrator: Orchestrator = Depends(orchestrator_dependency),
    vault: VaultClient = Depends(vault_dependency),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(require_admin),
) -> PocCounts:
    if settings.vault_client != "filesystem":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reset is only available with VAULT_CLIENT=filesystem",
        )
    reset = getattr(vault, "reset", None)
    if reset is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Active Vault client cannot be reset")

    result = reset()
    # The audit trail is append-only, so a reset is recorded rather than erasing history.
    await record_audit(
        orchestrator.session,
        action=AuditAction.DELETE,
        performed_by=principal.subject,
        source_system="poc",
        details={"operation": "POC_RESET", **result},
    )
    return PocCounts(
        source=mbox.count_files(settings, "source"),
        destination=mbox.count_files(settings, "destination"),
        unclassified=mbox.count_files(settings, "unclassified"),
    )
