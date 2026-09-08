from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.libs.common.config import Settings, get_settings
from app.libs.common.mailer import BatchSummary
from app.libs.common.models import FailureQueue, PicklistValue, TransferStatus
from app.services.integration_api.domain.orchestrator import Orchestrator, SubmissionItem
from app.services.integration_api.domain.validation import ensure_picklists_fresh

MAPPED = "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf"
UNMAPPED = "STUDY-004/US/SITE-101/Trial Management/orphan.pdf"


def _settings(**overrides) -> Settings:
    base = get_settings().model_dump()
    base.update(overrides)
    return Settings(**base)


async def _run(session, vault, path: str, **kwargs):
    orchestrator = Orchestrator(session, vault, performed_by="svc-test", **kwargs)
    results = await orchestrator.process_batch([SubmissionItem(source_path=path)], uuid.uuid4())
    return orchestrator, results[0]


# ----------------------------------------------------------- batch preparation
async def test_batch_authenticates_and_prefetches_once_not_per_file(session, vault, seeded, monkeypatch):
    calls = {"auth": 0, "picklists": 0}
    original_auth = vault.authenticate
    original_picklists = vault.get_picklist_values

    async def counting_auth():
        calls["auth"] += 1
        await original_auth()

    async def counting_picklists(name):
        calls["picklists"] += 1
        return await original_picklists(name)

    monkeypatch.setattr(vault, "authenticate", counting_auth)
    monkeypatch.setattr(vault, "get_picklist_values", counting_picklists)

    orchestrator = Orchestrator(session, vault, performed_by="svc-test")
    results = await orchestrator.process_batch(
        [SubmissionItem(source_path=MAPPED), SubmissionItem(source_path=UNMAPPED)], uuid.uuid4()
    )

    assert len(results) == 2
    assert calls["auth"] == 1
    # The seeded cache is inside its TTL, so no picklist call should be made at all.
    assert calls["picklists"] == 0


async def test_picklists_refresh_once_the_ttl_expires(session, vault, seeded):
    stale = datetime.now(UTC) - timedelta(hours=4)
    await session.execute(update(PicklistValue).values(refreshed_at=stale))
    await session.flush()

    counts = await ensure_picklists_fresh(session, vault, ttl_seconds=3600)
    assert counts
    assert counts["study__v"] == 3
    # Types, subtypes and classifications come from the document type hierarchy.
    assert counts["type__v"] == len(vault.DEFAULT_TYPE_HIERARCHY)
    assert counts["classification__v"] > 0

    # Immediately afterwards the cache is fresh again.
    assert await ensure_picklists_fresh(session, vault, ttl_seconds=3600) is None


async def test_empty_cache_is_always_prefetched(session, vault, engine):
    assert await ensure_picklists_fresh(session, vault, ttl_seconds=86400) is not None
    rows = (await session.execute(select(PicklistValue))).scalars().all()
    assert len(rows) > 0


async def test_vault_outage_during_prepare_does_not_abort_the_batch(session, vault, seeded, monkeypatch):
    from app.libs.common.vault_client import VaultAuthError

    async def failing_auth():
        raise VaultAuthError("vault unreachable")

    monkeypatch.setattr(vault, "authenticate", failing_auth)
    vault._authenticated = False

    orchestrator = Orchestrator(session, vault, performed_by="svc-test")
    results = await orchestrator.process_batch([SubmissionItem(source_path=MAPPED)], uuid.uuid4())

    assert results[0].status == TransferStatus.EXCEPTION.value
    failure = (
        await session.execute(select(FailureQueue).where(FailureQueue.transfer_id == results[0].transfer_id))
    ).scalar_one()
    assert failure.failure_reason == "VAULT_API_ERROR"


# ------------------------------------------------------------ unmapped policy
async def test_default_policy_queues_unmapped_without_uploading(session, vault, seeded):
    _, transfer = await _run(session, vault, UNMAPPED)
    assert transfer.status == TransferStatus.EXCEPTION.value
    assert transfer.vault_document_id is None


async def test_unclassified_policy_files_the_document_and_still_queues_it(session, vault, seeded):
    settings = _settings(unmapped_policy="unclassified")
    _, transfer = await _run(session, vault, UNMAPPED, settings=settings)

    assert transfer.status == TransferStatus.EXCEPTION.value
    assert transfer.vault_document_id is not None
    assert "Unclassified" in (transfer.message or "")

    doc = await vault.get_document_info(transfer.vault_document_id)
    assert doc["metadata"]["type__v"] == "Unclassified"
    assert doc["metadata"]["study__v"] == "STUDY-004"

    failure = (
        await session.execute(select(FailureQueue).where(FailureQueue.transfer_id == transfer.transfer_id))
    ).scalar_one()
    assert failure.failure_reason == "NO_MAPPING"
    assert failure.resolution_status == "OPEN"


async def test_unclassified_fallback_failure_is_still_queued(session, vault, seeded, monkeypatch):
    from app.libs.common.vault_client import VaultApiError

    async def failing_create(metadata, payload):
        raise VaultApiError("vault rejected the document", status_code=500)

    monkeypatch.setattr(vault, "create_document", failing_create)

    settings = _settings(unmapped_policy="unclassified")
    _, transfer = await _run(session, vault, UNMAPPED, settings=settings)

    assert transfer.status == TransferStatus.EXCEPTION.value
    assert transfer.vault_document_id is None
    assert "fallback upload failed" in (transfer.message or "")


# ------------------------------------------------------- post-upload verifying
async def test_checksum_mismatch_fails_the_transfer(session, vault, seeded, monkeypatch):
    async def wrong_checksum(document_id):
        return {"id": document_id, "size": None, "checksum": "f" * 32}

    monkeypatch.setattr(vault, "get_document_info", wrong_checksum)

    _, transfer = await _run(session, vault, MAPPED)
    assert transfer.status == TransferStatus.EXCEPTION.value
    assert "MD5 does not match" in (transfer.message or "")


async def test_matching_md5_passes_verification(session, vault, seeded, monkeypatch):
    """Vault reports md5checksum__v, so verification must compare MD5 and not SHA-256."""
    from app.libs.common.hashing import md5_file

    expected = md5_file(get_settings().mbox_root + "/" + MAPPED)

    async def matching(document_id):
        return {"id": document_id, "size": None, "checksum": expected.upper()}

    monkeypatch.setattr(vault, "get_document_info", matching)

    _, transfer = await _run(session, vault, MAPPED)
    assert transfer.status == TransferStatus.SUCCESS.value


# -------------------------------------------------------------- batch summary
async def test_batch_summary_counts_every_outcome(session, vault, seeded):
    orchestrator = Orchestrator(session, vault, performed_by="svc-test")
    await orchestrator.process_batch(
        [
            SubmissionItem(source_path=MAPPED),
            SubmissionItem(source_path=UNMAPPED),
            SubmissionItem(source_path="STUDY-002/GB/SITE-201/Safety/sae-2024-001.pdf"),
        ],
        uuid.uuid4(),
    )

    summary = orchestrator.last_summary
    assert summary is not None
    assert summary.total == 3
    assert summary.success == 1
    assert summary.sam_pending == 1
    assert summary.failed == 1
    assert summary.needs_attention == 2
    assert "STUDY-001" in summary.studies
    assert summary.top_reasons[0][1] == 1


async def test_summary_is_audited_even_when_mail_is_disabled(session, vault, seeded):
    from app.libs.common.models import AuditTrail

    correlation_id = uuid.uuid4()
    orchestrator = Orchestrator(session, vault, performed_by="svc-test")
    await orchestrator.process_batch([SubmissionItem(source_path=MAPPED)], correlation_id)

    entry = (
        await session.execute(select(AuditTrail).where(AuditTrail.action == "NOTIFY"))
    ).scalars().one()
    assert entry.details["operation"] == "BATCH_SUMMARY"
    assert entry.details["delivered"] is False
    assert entry.details["success"] == 1


@pytest.mark.parametrize("failed,expected", [(0, "complete"), (2, "action required")])
def test_summary_subject_reflects_outcome(failed: int, expected: str):
    summary = BatchSummary(
        correlation_id=str(uuid.uuid4()),
        started_at=datetime.now(UTC),
        total=5,
        success=3,
        failed=failed,
        studies=["STUDY-001"],
    )
    assert expected in summary.subject("[MBox->VTMF]")
    assert "STUDY-001" in summary.subject("[MBox->VTMF]")


def test_summary_body_never_leaks_document_content():
    summary = BatchSummary(
        correlation_id=str(uuid.uuid4()),
        started_at=datetime.now(UTC),
        total=1,
        success=1,
        studies=["STUDY-001"],
        top_reasons=[("No active mapping rule matched this source path.", 1)],
    )
    body = summary.text_body()
    assert "no document content or regulated data" in body
    assert "STUDY-001" in body


async def test_mail_send_refuses_credentials_without_tls():
    from app.libs.common.mailer import send_batch_summary

    settings = _settings(
        mail_enabled=True,
        mail_to="ops@example.com",
        smtp_username="svc",
        smtp_password="not-a-real-password",
        smtp_use_tls=False,
    )
    summary = BatchSummary(correlation_id=str(uuid.uuid4()), started_at=datetime.now(UTC))
    assert await send_batch_summary(summary, settings) is False
