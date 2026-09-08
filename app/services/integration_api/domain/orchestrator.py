from __future__ import annotations

import time
import uuid
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common import mbox
from app.libs.common.archive import CorruptArchiveError, read_archive_error
from app.libs.common.audit import record_audit
from app.libs.common.config import Settings, get_settings
from app.libs.common.hashing import external_id, md5_file, sha256_file
from app.libs.common.logging_config import bind_correlation_id, get_logger
from app.libs.common.mailer import BatchSummary, send_batch_summary
from app.libs.common.metrics import (
    PIPELINE_LATENCY,
    SAM_REQUESTS,
    TRANSFERS_DUPLICATE,
    UNCLASSIFIED_UPLOADS,
    record_failure,
    record_success,
)
from app.libs.common.models import (
    AuditAction,
    FailureQueue,
    FailureReason,
    InitiatedBy,
    ResolutionStatus,
    SamActionQueue,
    SamStatus,
    TransferLog,
    TransferStatus,
)
from app.libs.common.path_parser import PathInfo
from app.libs.common.vault_client import (
    FIELD_CLASSIFICATION,
    FIELD_EXTERNAL_ID,
    FIELD_LIFECYCLE,
    FIELD_NAME,
    FIELD_SITE,
    FIELD_STUDY,
    FIELD_STUDY_COUNTRY,
    FIELD_SUBTYPE,
    FIELD_TYPE,
    UNCLASSIFIED_ALLOWED_FIELDS,
    VaultApiError,
    VaultCircuitOpenError,
    VaultClient,
    VaultError,
    VaultRateLimitError,
)

from .mapping import MappingEngine
from .validation import ValidationService, ensure_picklists_fresh

log = get_logger(__name__)


class PreCheckError(Exception):
    """A file failed structural validation before any mapping was attempted."""


@dataclass
class SubmissionItem:
    source_path: str
    initiated_by: str = InitiatedBy.USER.value
    staged_path: str | None = None
    checksum: str | None = None
    override_duplicate: bool = False
    metadata_override: dict[str, Any] | None = None


class Orchestrator:
    """Normalize -> parse -> map -> validate -> upload, persisting state at every step."""

    def __init__(
        self,
        session: AsyncSession,
        vault: VaultClient,
        *,
        settings: Settings | None = None,
        performed_by: str | None = None,
        source_system: str = "integration-api",
    ) -> None:
        self.session = session
        self.vault = vault
        self.settings = settings or get_settings()
        self.source_system = source_system
        self.performed_by = performed_by or self.settings.service_account_id
        self.last_summary: BatchSummary | None = None

    # ------------------------------------------------------------------ API
    async def process_batch(
        self,
        items: list[SubmissionItem],
        correlation_id: uuid.UUID | None = None,
        *,
        send_summary: bool = True,
    ) -> list[TransferLog]:
        correlation_id = correlation_id or uuid.uuid4()
        bind_correlation_id(correlation_id)
        started_at = datetime.now(UTC)

        await self.prepare_batch(correlation_id)
        mapping_engine = await MappingEngine.load(self.session)
        validator = await ValidationService.load(self.session)

        results: list[TransferLog] = []
        for item in items:
            results.append(await self._process_one(item, correlation_id, mapping_engine, validator))

        self.last_summary = self._build_summary(correlation_id, started_at, results)
        if send_summary:
            await self._deliver_summary(self.last_summary, correlation_id)
        return results

    async def prepare_batch(self, correlation_id: uuid.UUID | None = None) -> None:
        """Authenticate once and prefetch picklists, instead of doing both per file.

        Neither step aborts the batch: the per-file paths already downgrade Vault
        outages to VAULT_API_ERROR failures, and a stale picklist cache is still
        safer than skipping validation.
        """
        try:
            await self.vault.authenticate()
        except VaultError as exc:
            log.error("batch_authentication_failed", error=str(exc))
            return

        try:
            counts = await ensure_picklists_fresh(
                self.session, self.vault, self.settings.picklist_cache_ttl_s
            )
        except VaultError as exc:
            log.warning("picklist_prefetch_failed", error=str(exc))
            return

        if counts is not None:
            log.info("picklists_prefetched", counts=counts)
            await record_audit(
                self.session,
                action=AuditAction.CONFIG_CHANGE,
                performed_by=self.performed_by,
                source_system=self.source_system,
                correlation_id=correlation_id,
                details={"operation": "PICKLIST_TTL_REFRESH", "counts": counts},
            )

    def _build_summary(
        self, correlation_id: uuid.UUID, started_at: datetime, results: list[TransferLog]
    ) -> BatchSummary:
        reasons = Counter(
            (t.message or "Unspecified").strip()[:120]
            for t in results
            if t.status in (TransferStatus.EXCEPTION.value, TransferStatus.FAILED.value)
        )
        studies = sorted(
            {(t.resolved_metadata or {}).get("study") or t.source_path.split("/")[0] for t in results}
        )
        return BatchSummary(
            correlation_id=str(correlation_id),
            started_at=started_at,
            total=len(results),
            success=sum(1 for t in results if t.status == TransferStatus.SUCCESS.value),
            duplicates=sum(1 for t in results if t.status == TransferStatus.DUPLICATE_SKIPPED.value),
            sam_pending=sum(1 for t in results if t.status == TransferStatus.SAM_PENDING.value),
            unclassified=sum(
                1
                for t in results
                if t.status == TransferStatus.EXCEPTION.value and t.vault_document_id is not None
            ),
            failed=sum(
                1
                for t in results
                if t.status in (TransferStatus.EXCEPTION.value, TransferStatus.FAILED.value)
            ),
            studies=[s for s in studies if s],
            top_reasons=reasons.most_common(5),
        )

    async def _deliver_summary(self, summary: BatchSummary, correlation_id: uuid.UUID) -> None:
        delivered = await send_batch_summary(summary, self.settings)
        await record_audit(
            self.session,
            action=AuditAction.NOTIFY,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=correlation_id,
            details={
                "operation": "BATCH_SUMMARY",
                "delivered": delivered,
                "total": summary.total,
                "success": summary.success,
                "duplicates": summary.duplicates,
                "unclassified": summary.unclassified,
                "sam_pending": summary.sam_pending,
                "failed": summary.failed,
                "top_reasons": summary.top_reasons,
            },
        )

    async def retry_transfer(
        self, transfer: TransferLog, metadata_override: dict[str, Any] | None = None
    ) -> TransferLog:
        """Re-run the pipeline for an existing transfer, typically after a human fix."""
        await record_audit(
            self.session,
            action=AuditAction.RETRY,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=transfer.correlation_id,
            details={"transfer_id": str(transfer.transfer_id), "override": metadata_override or {}},
        )
        item = SubmissionItem(
            source_path=transfer.source_path,
            initiated_by=InitiatedBy.USER.value,
            checksum=transfer.file_checksum,
            override_duplicate=True,
            metadata_override=metadata_override,
        )
        await self.prepare_batch(transfer.correlation_id)
        mapping_engine = await MappingEngine.load(self.session)
        validator = await ValidationService.load(self.session)
        return await self._process_one(
            item, transfer.correlation_id, mapping_engine, validator, existing=transfer
        )

    # -------------------------------------------------------------- internals
    async def _process_one(
        self,
        item: SubmissionItem,
        correlation_id: uuid.UUID,
        mapping_engine: MappingEngine,
        validator: ValidationService,
        existing: TransferLog | None = None,
    ) -> TransferLog:
        started = time.perf_counter()
        bind_correlation_id(correlation_id)
        info = mbox.describe(item.source_path, self.settings)
        normalized_source = mbox.relative_to_root_virtual(item.source_path, self.settings)

        transfer = existing or TransferLog(
            transfer_id=uuid.uuid4(),
            source_path=normalized_source,
            file_name=info.file_name,
            status=TransferStatus.PENDING.value,
            initiated_by=item.initiated_by,
            correlation_id=correlation_id,
        )
        if existing is None:
            self.session.add(transfer)
        transfer.status = TransferStatus.PROCESSING.value
        transfer.attempt_count = (transfer.attempt_count or 0) + 1
        transfer.updated_at = datetime.now(UTC)
        await self.session.flush()

        try:
            with ExitStack() as stack:
                real_path = self._locate(item, stack)
                self._pre_check(info, real_path)

                if info.extension == ".zip" and not info.is_inside_archive:
                    return await self._reject_archive(transfer, info, real_path)

                checksum = item.checksum or sha256_file(real_path)
                size = real_path.stat().st_size
                transfer.file_checksum = checksum
                transfer.file_size_bytes = size
                await self.session.flush()

                if not item.override_duplicate and await self._is_duplicate(transfer, checksum):
                    return await self._finish_duplicate(transfer, info)

                mapping = mapping_engine.resolve(info, item.metadata_override)
                await record_audit(
                    self.session,
                    action=AuditAction.MAP,
                    performed_by=self.performed_by,
                    source_system=self.source_system,
                    correlation_id=correlation_id,
                    details={
                        "transfer_id": str(transfer.transfer_id),
                        "source_path": transfer.source_path,
                        "mapping_id": mapping.rule_id,
                        "match_type": mapping.match_type,
                    },
                )
                if not mapping.matched:
                    return await self._handle_unmapped(
                        transfer, info, real_path, checksum, size, mapping_engine
                    )

                transfer.mapping_id_used = mapping.rule_id
                transfer.resolved_metadata = mapping.metadata

                validation = validator.validate(mapping.metadata)
                await record_audit(
                    self.session,
                    action=AuditAction.VALIDATE,
                    performed_by=self.performed_by,
                    source_system=self.source_system,
                    correlation_id=correlation_id,
                    details={"transfer_id": str(transfer.transfer_id), "result": validation.summary()},
                )
                if validation.missing_required:
                    return await self._fail(
                        transfer,
                        info,
                        FailureReason.VALIDATION_ERROR,
                        validation.summary(),
                        suggested=mapping_engine.suggest(info),
                    )
                if validation.needs_sam:
                    return await self._route_to_sam(transfer, info, validation.invalid_picklist)

                return await self._upload(transfer, info, real_path, checksum, size, mapping.metadata)

        except CorruptArchiveError as exc:
            return await self._fail(transfer, info, FailureReason.CORRUPT_ARCHIVE, str(exc))
        except (PreCheckError, FileNotFoundError) as exc:
            return await self._fail(transfer, info, FailureReason.VALIDATION_ERROR, str(exc))
        except VaultRateLimitError as exc:
            return await self._fail(transfer, info, FailureReason.RATE_LIMIT, str(exc))
        except (VaultCircuitOpenError, VaultApiError, VaultError) as exc:
            return await self._fail(transfer, info, FailureReason.VAULT_API_ERROR, str(exc))
        finally:
            PIPELINE_LATENCY.observe(time.perf_counter() - started)

    async def _reject_archive(self, transfer: TransferLog, info: PathInfo, real_path: Path) -> TransferLog:
        """An archive is a container, never a document; it must be expanded before submission."""
        error = read_archive_error(real_path)
        if error is not None:
            return await self._fail(transfer, info, FailureReason.CORRUPT_ARCHIVE, error)
        return await self._fail(
            transfer,
            info,
            FailureReason.VALIDATION_ERROR,
            "Archives are containers, not documents: submit the extracted members instead.",
        )

    def _locate(self, item: SubmissionItem, stack: ExitStack) -> Path:
        """Prefer a path already staged by the watcher; otherwise materialize from MBox."""
        if item.staged_path:
            staged = Path(item.staged_path)
            if staged.is_file():
                return staged
        return stack.enter_context(mbox.materialize(item.source_path, self.settings))

    def _pre_check(self, info: PathInfo, real_path: Path) -> None:
        if info.extension not in self.settings.allowed_extension_set:
            raise PreCheckError(f"Extension {info.extension or '<none>'} is not permitted for ingestion")
        if info.depth < self.settings.min_folder_depth and not info.is_inside_archive:
            raise PreCheckError(
                f"Folder depth {info.depth} is below the required minimum of {self.settings.min_folder_depth}"
            )
        size = real_path.stat().st_size
        if size == 0:
            raise PreCheckError("File is empty")
        if size > self.settings.max_file_bytes:
            raise PreCheckError(f"File size {size} bytes exceeds MAX_FILE_MB={self.settings.max_file_mb}")

    async def _is_duplicate(self, transfer: TransferLog, checksum: str) -> bool:
        stmt = select(TransferLog.transfer_id).where(
            TransferLog.source_path == transfer.source_path,
            TransferLog.file_checksum == checksum,
            TransferLog.status == TransferStatus.SUCCESS.value,
            TransferLog.transfer_id != transfer.transfer_id,
        )
        return (await self.session.execute(stmt)).first() is not None

    async def _finish_duplicate(self, transfer: TransferLog, info: PathInfo) -> TransferLog:
        transfer.status = TransferStatus.DUPLICATE_SKIPPED.value
        transfer.message = "An identical file (same path and checksum) was already archived in VTMF."
        transfer.updated_at = datetime.now(UTC)
        TRANSFERS_DUPLICATE.inc()
        await record_audit(
            self.session,
            action=AuditAction.CLASSIFY,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=transfer.correlation_id,
            details={
                "transfer_id": str(transfer.transfer_id),
                "status": transfer.status,
                "study": info.study,
                "file": info.file_name,
            },
        )
        await self.session.flush()
        return transfer

    async def _fail(
        self,
        transfer: TransferLog,
        info: PathInfo,
        reason: FailureReason,
        detail: str,
        suggested: dict[str, Any] | None = None,
    ) -> TransferLog:
        transfer.status = TransferStatus.EXCEPTION.value
        transfer.message = detail
        transfer.updated_at = datetime.now(UTC)

        existing = await self.session.execute(
            select(FailureQueue).where(
                FailureQueue.transfer_id == transfer.transfer_id,
                FailureQueue.resolution_status == ResolutionStatus.OPEN.value,
            )
        )
        entry = existing.scalars().first()
        if entry is None:
            entry = FailureQueue(transfer_id=transfer.transfer_id)
            self.session.add(entry)
        entry.failure_reason = reason.value
        entry.failure_detail = detail[:4000]
        entry.suggested_mapping = suggested
        entry.resolution_status = ResolutionStatus.OPEN.value

        record_failure(reason.value, info.study)
        log.warning(
            "transfer_failed",
            transfer_id=str(transfer.transfer_id),
            reason=reason.value,
            study=info.study,
            country=info.country,
            site=info.site,
            file=info.file_name,
        )
        await record_audit(
            self.session,
            action=AuditAction.CLASSIFY,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=transfer.correlation_id,
            details={
                "transfer_id": str(transfer.transfer_id),
                "status": transfer.status,
                "reason": reason.value,
                "detail": detail[:1000],
            },
        )
        await self.session.flush()
        return transfer

    async def _route_to_sam(
        self, transfer: TransferLog, info: PathInfo, missing: dict[str, str]
    ) -> TransferLog:
        transfer.status = TransferStatus.SAM_PENDING.value
        transfer.message = "Metadata values are not present in VTMF picklists; SAM request required before upload."
        transfer.updated_at = datetime.now(UTC)

        existing = await self.session.execute(
            select(SamActionQueue).where(
                SamActionQueue.transfer_id == transfer.transfer_id,
                SamActionQueue.status != SamStatus.COMPLETED.value,
            )
        )
        sam = existing.scalars().first()
        if sam is None:
            sam = SamActionQueue(transfer_id=transfer.transfer_id, missing_fields=missing)
            self.session.add(sam)
        sam.missing_fields = missing
        sam.status = SamStatus.OPEN.value
        sam.requested_at = datetime.now(UTC)

        SAM_REQUESTS.inc()
        await record_audit(
            self.session,
            action=AuditAction.SAM_REQUEST,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=transfer.correlation_id,
            details={"transfer_id": str(transfer.transfer_id), "missing_fields": missing, "study": info.study},
        )
        log.info("sam_required", transfer_id=str(transfer.transfer_id), missing_fields=missing)
        await self.session.flush()
        return transfer

    async def _upload(
        self,
        transfer: TransferLog,
        info: PathInfo,
        real_path: Path,
        checksum: str,
        size: int,
        metadata: dict[str, Any],
    ) -> TransferLog:
        document_id, idempotent = await self._create_document(
            transfer, info, real_path, checksum, size, metadata
        )
        if idempotent:
            return await self._finish_success(transfer, info, document_id, idempotent=True, verified_size=size)

        mismatch = await self._verify_upload(document_id, real_path, size)
        if mismatch:
            return await self._fail(transfer, info, FailureReason.VAULT_API_ERROR, mismatch)

        return await self._finish_success(transfer, info, document_id, idempotent=False, verified_size=size)

    async def _create_document(
        self,
        transfer: TransferLog,
        info: PathInfo,
        real_path: Path,
        checksum: str,
        size: int,
        metadata: dict[str, Any],
    ) -> tuple[str, bool]:
        """Create the Vault document, or return the existing one for a replayed file."""
        ext_id = external_id(transfer.source_path, checksum)
        existing_doc = await self.vault.find_document_by_external_id(ext_id)
        if existing_doc:
            return existing_doc, True

        payload: Any = real_path
        if size > self.settings.large_file_bytes:
            payload = await self.vault.stage_file(real_path)

        vault_metadata = self._to_vault_metadata(metadata, ext_id, checksum, info)
        return await self.vault.create_document(vault_metadata, payload), False

    async def _verify_upload(self, document_id: str, real_path: Path, size: int) -> str | None:
        """Post-upload check against Vault's reported size and MD5."""
        try:
            doc_info = await self.vault.get_document_info(document_id)
        except VaultApiError as exc:
            log.warning("post_upload_verification_unavailable", error=str(exc), document_id=document_id)
            return None

        reported_size = doc_info.get("size")
        if reported_size is not None and int(reported_size) not in (0, size):
            return f"Post-upload verification failed: Vault size {reported_size} != source size {size}"

        reported_checksum = doc_info.get("checksum")
        if reported_checksum and str(reported_checksum).lower() != md5_file(real_path).lower():
            return "Post-upload verification failed: Vault MD5 does not match the source file"
        return None

    async def _handle_unmapped(
        self,
        transfer: TransferLog,
        info: PathInfo,
        real_path: Path,
        checksum: str,
        size: int,
        mapping_engine: MappingEngine,
    ) -> TransferLog:
        """Unmapped files are always queued; policy decides whether they also reach VTMF."""
        detail = "No active mapping rule matched this source path."

        if self.settings.unmapped_policy == "unclassified":
            metadata = {
                "study": info.study,
                "country": info.country,
                "site": info.site,
                "document_type": self.settings.unclassified_document_type,
                "document_subtype": self.settings.unclassified_document_subtype,
                "classification": self.settings.unclassified_classification,
            }
            try:
                document_id, _ = await self._create_document(
                    transfer, info, real_path, checksum, size, metadata
                )
                transfer.vault_document_id = document_id
                transfer.resolved_metadata = metadata
                UNCLASSIFIED_UPLOADS.inc()
                detail = (
                    f"No mapping rule matched; filed in VTMF as an Unclassified document "
                    f"({document_id}) pending reclassification."
                )
            except VaultError as exc:
                detail = f"No mapping rule matched and the Unclassified fallback upload failed: {exc}"

        return await self._fail(
            transfer, info, FailureReason.NO_MAPPING, detail, suggested=mapping_engine.suggest(info)
        )

    def _to_vault_metadata(
        self, metadata: dict[str, Any], ext_id: str, checksum: str, info: PathInfo
    ) -> dict[str, Any]:
        """Map resolved metadata onto Vault v26.2 document fields."""
        document_type = metadata.get("document_type")
        unclassified = document_type == self.settings.unclassified_document_type

        fields: dict[str, Any] = {
            FIELD_NAME: info.file_name,
            FIELD_TYPE: document_type,
            FIELD_SUBTYPE: metadata.get("document_subtype"),
            FIELD_CLASSIFICATION: metadata.get("classification"),
            FIELD_STUDY: metadata.get("study"),
            FIELD_STUDY_COUNTRY: metadata.get("country"),
            FIELD_SITE: metadata.get("site"),
            FIELD_EXTERNAL_ID: ext_id,
            FIELD_LIFECYCLE: metadata.get("lifecycle") or self.settings.document_lifecycle,
        }

        if unclassified:
            # Vault ignores every other field on an Unclassified document, and pairs
            # that type with the Inbox lifecycle.
            fields[FIELD_LIFECYCLE] = self.settings.unclassified_lifecycle
            fields = {k: v for k, v in fields.items() if k in UNCLASSIFIED_ALLOWED_FIELDS}

        return {k: v for k, v in fields.items() if v is not None and v != ""}

    async def _finish_success(
        self,
        transfer: TransferLog,
        info: PathInfo,
        document_id: str,
        *,
        idempotent: bool,
        verified_size: int | None,
    ) -> TransferLog:
        transfer.vault_document_id = document_id
        transfer.status = TransferStatus.SUCCESS.value
        transfer.message = (
            "Existing Vault document matched by external id (idempotent no-op)."
            if idempotent
            else "Document created in Veeva Vault TMF."
        )
        transfer.updated_at = datetime.now(UTC)
        record_success(info.study)
        await record_audit(
            self.session,
            action=AuditAction.UPLOAD,
            performed_by=self.performed_by,
            source_system=self.source_system,
            correlation_id=transfer.correlation_id,
            details={
                "transfer_id": str(transfer.transfer_id),
                "vault_document_id": document_id,
                "study": info.study,
                "country": info.country,
                "site": info.site,
                "file": info.file_name,
                "checksum": transfer.file_checksum,
                "size_bytes": verified_size,
                "idempotent": idempotent,
            },
        )
        log.info(
            "transfer_success",
            transfer_id=str(transfer.transfer_id),
            vault_document_id=document_id,
            study=info.study,
            file=info.file_name,
        )
        await self.session.flush()
        return transfer
