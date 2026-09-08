from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.libs.common.config import get_settings
from app.libs.common.models import FailureQueue, FolderWatch, TransferLog, TransferStatus
from app.workers.watcher import jobs


def test_collect_candidates_expands_archives_and_skips_invalid(mbox_root, tmp_path: Path):
    settings = get_settings()
    result = jobs.ScanResult()
    candidates, corrupt = jobs._collect_candidates(settings, tmp_path / "stage", result)

    paths = {c.source_path for c in candidates}
    assert "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf" in paths
    assert "STUDY-003/JP/SITE-301/Site Management/site-docs.zip!/delegation-log.pdf" in paths
    assert (
        "STUDY-003/JP/SITE-301/Site Management/site-docs.zip!/level-2/inner.zip!/signature-sheet.pdf" in paths
    )
    assert not any(p.endswith("notes.txt") for p in paths)
    assert not any(p == "shallow.pdf" for p in paths)
    assert all(c.checksum and c.size_bytes > 0 for c in candidates)

    assert [c[0] for c in corrupt] == ["STUDY-003/JP/SITE-301/Site Management/corrupt.zip"]
    assert result.skipped_invalid >= 2


async def test_scan_records_corrupt_archives_and_folder_ages(engine, mbox_root, monkeypatch):
    submitted: list[int] = []

    async def fake_submit(client, batch, correlation_id, settings):
        submitted.append(len(batch))
        return len(batch)

    monkeypatch.setattr(jobs, "_submit", fake_submit)
    result = await jobs.scan_and_enqueue()

    assert result.submitted > 0
    assert result.corrupt_archives == 1
    assert sum(submitted) == result.submitted

    from app.libs.common.db import session_scope

    async with session_scope() as session:
        failure = (
            await session.execute(
                select(FailureQueue).where(FailureQueue.failure_reason == "CORRUPT_ARCHIVE")
            )
        ).scalar_one()
        transfer = await session.get(TransferLog, failure.transfer_id)
        assert transfer.status == TransferStatus.EXCEPTION.value

        studies = {f.study for f in (await session.execute(select(FolderWatch))).scalars().all()}
        assert {"STUDY-001", "STUDY-002", "STUDY-003", "STUDY-004"} <= studies


async def test_second_scan_skips_already_archived_files(engine, mbox_root, monkeypatch, vault, seeded):
    import uuid

    from app.services.integration_api.domain.orchestrator import Orchestrator, SubmissionItem

    path = "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf"
    from app.libs.common.db import session_scope

    async with session_scope() as session:
        orchestrator = Orchestrator(session, vault, performed_by="svc-test")
        results = await orchestrator.process_batch([SubmissionItem(source_path=path)], uuid.uuid4())
        assert results[0].status == TransferStatus.SUCCESS.value

    async def fake_submit(client, batch, correlation_id, settings):
        assert all(c.source_path != path for c in batch)
        return len(batch)

    monkeypatch.setattr(jobs, "_submit", fake_submit)
    result = await jobs.scan_and_enqueue()
    assert result.skipped_duplicate == 1
