"""End-to-end proof of concept: source folder -> destination / unclassified folders."""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.libs.common.config import Settings, get_settings
from app.libs.common.models import TransferStatus
from app.libs.common.vault_client_fs import FilesystemVaultClient
from app.services.integration_api.domain.orchestrator import Orchestrator, SubmissionItem


@pytest.fixture
def poc_roots(tmp_path: Path) -> tuple[Path, Path]:
    destination = tmp_path / "destination"
    unclassified = tmp_path / "unclassified"
    destination.mkdir()
    unclassified.mkdir()
    return destination, unclassified


def _settings(destination: Path, unclassified: Path, **overrides) -> Settings:
    base = get_settings().model_dump()
    base.update(
        vault_client="filesystem",
        destination_root=str(destination),
        unclassified_root=str(unclassified),
    )
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def fs_vault(poc_roots):
    destination, unclassified = poc_roots
    client = FilesystemVaultClient(_settings(destination, unclassified))
    await client.authenticate()
    return client


def _files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not p.name.startswith("_") and "_metadata" not in p.parts
    )


async def _run(session, vault, settings, *paths: str):
    orchestrator = Orchestrator(session, vault, settings=settings, performed_by="svc-poc")
    return await orchestrator.process_batch(
        [SubmissionItem(source_path=p, override_duplicate=True) for p in paths], uuid.uuid4()
    )


# ------------------------------------------------------------------ filing
async def test_mapped_file_lands_in_the_destination_tree(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)

    results = await _run(
        session, fs_vault, settings, "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf"
    )

    assert results[0].status == TransferStatus.SUCCESS.value
    assert _files(destination) == [
        "STUDY-001/US/SITE-101/Trial Management/Trial Master File Plan/tmf-plan.pdf"
    ]
    assert _files(unclassified) == []


async def test_unmapped_file_lands_in_unclassified(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified, unmapped_policy="unclassified")

    results = await _run(
        session, fs_vault, settings, "STUDY-004/US/SITE-101/Trial Management/orphan.pdf"
    )

    assert results[0].status == TransferStatus.EXCEPTION.value
    assert results[0].vault_document_id is not None
    assert _files(unclassified) == ["STUDY-004/US/SITE-101/orphan.pdf"]
    assert _files(destination) == []


async def test_nested_zip_member_is_extracted_and_filed(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)

    results = await _run(
        session,
        fs_vault,
        settings,
        "STUDY-003/JP/SITE-301/Site Management/site-docs.zip!/level-2/inner.zip!/signature-sheet.pdf",
    )

    assert results[0].status == TransferStatus.SUCCESS.value
    assert _files(destination) == [
        "STUDY-003/JP/SITE-301/Site Management/Site Signature Sheet/signature-sheet.pdf"
    ]
    # The extracted member must be the real content, not the archive.
    filed = destination / _files(destination)[0]
    assert not zipfile.is_zipfile(filed)
    assert filed.read_text(encoding="utf-8") == "signature sheet"


async def test_sam_pending_file_is_not_written_anywhere(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)

    results = await _run(session, fs_vault, settings, "STUDY-002/GB/SITE-201/Safety/sae-2024-001.pdf")

    assert results[0].status == TransferStatus.SAM_PENDING.value
    assert _files(destination) == []
    assert _files(unclassified) == []


async def test_corrupt_archive_is_not_written_anywhere(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified, unmapped_policy="unclassified")

    results = await _run(
        session, fs_vault, settings, "STUDY-003/JP/SITE-301/Site Management/corrupt.zip!/anything.pdf"
    )

    assert results[0].status == TransferStatus.EXCEPTION.value
    assert _files(destination) == []
    assert _files(unclassified) == []


async def test_a_corrupt_archive_submitted_directly_is_never_filed(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified, unmapped_policy="unclassified")

    results = await _run(session, fs_vault, settings, "STUDY-003/JP/SITE-301/Site Management/corrupt.zip")

    assert results[0].status == TransferStatus.EXCEPTION.value
    assert results[0].vault_document_id is None
    assert "not a readable ZIP archive" in (results[0].message or "")
    assert _files(destination) == []
    assert _files(unclassified) == []


async def test_a_healthy_archive_is_rejected_as_a_document(session, fs_vault, seeded, poc_roots):
    """Archives are containers; filing one as a document would hide its contents."""
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)

    results = await _run(session, fs_vault, settings, "STUDY-003/JP/SITE-301/Site Management/site-docs.zip")

    assert results[0].status == TransferStatus.EXCEPTION.value
    assert results[0].vault_document_id is None
    assert "containers, not documents" in (results[0].message or "")
    assert _files(destination) == []


# ------------------------------------------------------------- idempotency
async def test_rerunning_does_not_duplicate_the_filed_document(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)
    path = "STUDY-001/US/SITE-101/Monitoring/monitoring-plan.pdf"

    first = await _run(session, fs_vault, settings, path)
    second = await _run(session, fs_vault, settings, path)

    assert first[0].vault_document_id == second[0].vault_document_id
    assert len(_files(destination)) == 1


async def test_identical_content_is_overwritten_even_if_the_index_is_lost(
    session, fs_vault, seeded, poc_roots
):
    """A re-run after the sink index is lost must not accumulate "(2)" copies."""
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)
    path = "STUDY-001/US/SITE-101/Monitoring/monitoring-plan.pdf"

    await _run(session, fs_vault, settings, path)
    fs_vault._index = {"sequence": 0, "documents": {}, "by_external_id": {}}
    await _run(session, fs_vault, settings, path)

    filed = _files(destination)
    assert filed == ["STUDY-001/US/SITE-101/Trial Management/Monitoring Plan/monitoring-plan.pdf"]


async def test_different_content_with_the_same_name_is_kept_separately(fs_vault, poc_roots, tmp_path):
    destination, _ = poc_roots
    metadata = {
        "name__v": "report.pdf",
        "type__v": "Trial Management",
        "subtype__v": "Monitoring Plan",
        "study__v": "STUDY-001",
    }
    for index, body in enumerate(("first version", "second version"), start=1):
        source = tmp_path / f"src{index}.pdf"
        source.write_text(body, encoding="utf-8")
        await fs_vault.create_document({**metadata, "external_id__v": f"ext-{index}"}, source)

    assert len(_files(destination)) == 2


async def test_reset_empties_both_sinks(session, fs_vault, seeded, poc_roots):
    destination, unclassified = poc_roots
    settings = _settings(destination, unclassified)

    await _run(session, fs_vault, settings, "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf")
    assert _files(destination)

    fs_vault.reset()
    assert _files(destination) == []
    assert await fs_vault.find_document_by_external_id("anything") is None


# ------------------------------------------------------------------ safety
async def test_metadata_values_cannot_escape_the_destination_root(fs_vault, poc_roots, tmp_path):
    destination, _ = poc_roots
    source = tmp_path / "payload.pdf"
    source.write_text("payload", encoding="utf-8")

    await fs_vault.create_document(
        {
            "name__v": "../../payload.pdf",
            "type__v": "Trial Management",
            "subtype__v": "Trial Master File Plan",
            "study__v": "../../escaped",
            "study_country__v": "..",
            "site__v": "/etc",
            "external_id__v": "esc-1",
        },
        source,
    )

    filed = _files(destination)
    assert len(filed) == 1
    # The security property is containment, not the cosmetics of the sanitised name.
    assert destination.resolve() in (destination / filed[0]).resolve().parents
    assert ".." not in filed[0]
    assert not (tmp_path / "escaped").exists()
