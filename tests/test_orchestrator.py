from __future__ import annotations

import uuid

from sqlalchemy import select

from app.libs.common.models import AuditTrail, FailureQueue, SamActionQueue, TransferStatus
from app.services.integration_api.domain.orchestrator import Orchestrator, SubmissionItem


async def _run(session, vault, source_path: str, **kwargs):
    orchestrator = Orchestrator(session, vault, performed_by="svc-test")
    results = await orchestrator.process_batch([SubmissionItem(source_path=source_path, **kwargs)], uuid.uuid4())
    return results[0]


async def test_happy_path_creates_vault_document(session, vault, seeded):
    transfer = await _run(session, vault, "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf")

    assert transfer.status == TransferStatus.SUCCESS.value
    assert transfer.vault_document_id
    assert transfer.file_checksum
    assert transfer.resolved_metadata["document_subtype"] == "Trial Master File Plan"

    doc = await vault.get_document_info(transfer.vault_document_id)
    assert doc["metadata"]["study__v"] == "STUDY-001"

    actions = (await session.execute(select(AuditTrail.action))).scalars().all()
    assert {"MAP", "VALIDATE", "UPLOAD"} <= set(actions)


async def test_unmapped_file_goes_to_failure_queue(session, vault, seeded):
    transfer = await _run(session, vault, "STUDY-004/US/SITE-101/Trial Management/orphan.pdf")

    assert transfer.status == TransferStatus.EXCEPTION.value
    failure = (
        await session.execute(select(FailureQueue).where(FailureQueue.transfer_id == transfer.transfer_id))
    ).scalar_one()
    assert failure.failure_reason == "NO_MAPPING"
    assert failure.resolution_status == "OPEN"
    assert failure.suggested_mapping["target_study"] == "STUDY-004"


async def test_unknown_picklist_value_routes_to_sam_without_upload(session, vault, seeded):
    transfer = await _run(session, vault, "STUDY-002/GB/SITE-201/Safety/sae-2024-001.pdf")

    assert transfer.status == TransferStatus.SAM_PENDING.value
    assert transfer.vault_document_id is None
    sam = (
        await session.execute(select(SamActionQueue).where(SamActionQueue.transfer_id == transfer.transfer_id))
    ).scalar_one()
    assert sam.missing_fields == {"classification": "Regulated Correspondence"}


async def test_duplicate_is_skipped_unless_overridden(session, vault, seeded):
    path = "STUDY-001/US/SITE-101/Monitoring/monitoring-plan.pdf"
    first = await _run(session, vault, path)
    assert first.status == TransferStatus.SUCCESS.value

    second = await _run(session, vault, path)
    assert second.status == TransferStatus.DUPLICATE_SKIPPED.value

    third = await _run(session, vault, path, override_duplicate=True)
    assert third.status == TransferStatus.SUCCESS.value
    # Idempotency: the same external id resolves to the original Vault document.
    assert third.vault_document_id == first.vault_document_id


async def test_nested_archive_member_is_archived(session, vault, seeded):
    transfer = await _run(
        session,
        vault,
        "STUDY-003/JP/SITE-301/Site Management/site-docs.zip!/level-2/inner.zip!/signature-sheet.pdf",
    )
    assert transfer.status == TransferStatus.SUCCESS.value
    assert transfer.file_name == "signature-sheet.pdf"
    assert transfer.resolved_metadata["study"] == "STUDY-003"


async def test_corrupt_archive_is_queued_not_lost(session, vault, seeded):
    transfer = await _run(
        session, vault, "STUDY-003/JP/SITE-301/Site Management/corrupt.zip!/anything.pdf"
    )
    assert transfer.status == TransferStatus.EXCEPTION.value
    failure = (
        await session.execute(select(FailureQueue).where(FailureQueue.transfer_id == transfer.transfer_id))
    ).scalar_one()
    assert failure.failure_reason == "CORRUPT_ARCHIVE"


async def test_disallowed_extension_is_rejected(session, vault, seeded):
    transfer = await _run(session, vault, "STUDY-001/US/SITE-101/Trial Management/notes.txt")
    assert transfer.status == TransferStatus.EXCEPTION.value
    assert "not permitted" in transfer.message


async def test_folder_depth_below_minimum_is_rejected(session, vault, seeded):
    transfer = await _run(session, vault, "shallow.pdf")
    assert transfer.status == TransferStatus.EXCEPTION.value
    assert "Folder depth" in transfer.message
