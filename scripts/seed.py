"""Seed mapping rules and the VTMF picklist cache.

Idempotent: re-running only inserts rules that are not already present.

    python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from app.libs.common.audit import record_audit  # noqa: E402
from app.libs.common.config import get_settings  # noqa: E402
from app.libs.common.db import dispose_engine, session_scope  # noqa: E402
from app.libs.common.models import AuditAction, MappingRule  # noqa: E402
from app.libs.common.vault_factory import build_vault_client  # noqa: E402
from app.services.integration_api.domain.validation import refresh_reference_data  # noqa: E402

SEED_RULES: list[dict] = [
    {
        "source_pattern": "STUDY-001/US/SITE-101/Trial Management",
        "match_type": "EXACT",
        "priority": 10,
        "target_study": "STUDY-001",
        "target_country": "US",
        "target_site": "SITE-101",
        "document_type": "Trial Management",
        "document_subtype": "Trial Master File Plan",
        "classification": "Essential Document",
        "default_metadata": {"lifecycle": "TMF Document Lifecycle"},
    },
    {
        "source_pattern": r".*/Monitoring/.*",
        "match_type": "REGEX",
        "priority": 50,
        "document_type": "Trial Management",
        "document_subtype": "Monitoring Plan",
        "classification": "Essential Document",
        "default_metadata": {},
    },
    {
        "source_pattern": "STUDY-001",
        "match_type": "STUDY_DEFAULT",
        "priority": 100,
        "document_type": "Central Trial Documents",
        "document_subtype": "Investigator Brochure",
        "classification": "Supporting Document",
        "default_metadata": {},
    },
    {
        "source_pattern": "STUDY-002",
        "match_type": "STUDY_DEFAULT",
        "priority": 100,
        "document_type": "Safety Reporting",
        "document_subtype": "SAE Report",
        # Deliberately not a VTMF picklist value: exercises the SAM action queue.
        "classification": "Regulated Correspondence",
        "default_metadata": {},
    },
    {
        "source_pattern": "STUDY-003",
        "match_type": "STUDY_DEFAULT",
        "priority": 100,
        "document_type": "Site Management",
        "document_subtype": "Site Signature Sheet",
        "classification": "Essential Document",
        "default_metadata": {},
    },
]


async def seed() -> None:
    settings = get_settings()
    vault = build_vault_client(settings)
    inserted = 0

    async with session_scope() as session:
        for rule in SEED_RULES:
            exists = await session.execute(
                select(MappingRule.mapping_id).where(
                    MappingRule.source_pattern == rule["source_pattern"],
                    MappingRule.match_type == rule["match_type"],
                )
            )
            if exists.first():
                continue
            session.add(MappingRule(**rule, created_by="seed", updated_by="seed"))
            inserted += 1
        await session.flush()

        await vault.authenticate()
        counts = await refresh_reference_data(session, vault)

        await record_audit(
            session,
            action=AuditAction.CONFIG_CHANGE,
            performed_by="seed",
            source_system="scripts",
            details={"operation": "SEED", "mapping_rules_inserted": inserted, "picklists": counts},
        )

    await vault.aclose()
    await dispose_engine()
    print(f"Seeded {inserted} mapping rule(s); picklist cache: {counts}")


if __name__ == "__main__":
    asyncio.run(seed())
