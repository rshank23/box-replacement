from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.metrics import PICKLIST_REFRESHES
from app.libs.common.models import PicklistValue
from app.libs.common.vault_client import (
    FIELD_CLASSIFICATION,
    FIELD_SITE,
    FIELD_STUDY,
    FIELD_SUBTYPE,
    FIELD_TYPE,
    VaultClient,
)

#: Metadata fields that must be present before any upload is attempted.
REQUIRED_FIELDS = ("study", "country", "site", "document_type", "document_subtype", "classification")

COUNTRY_PICKLIST = "country__v"

#: Vault reference data backing each metadata field. `type__v`, `subtype__v` and
#: `classification__v` come from the document type hierarchy, not from picklists.
FIELD_PICKLISTS: dict[str, str] = {
    "study": FIELD_STUDY,
    "country": COUNTRY_PICKLIST,
    "site": FIELD_SITE,
    "document_type": FIELD_TYPE,
    "document_subtype": FIELD_SUBTYPE,
    "classification": FIELD_CLASSIFICATION,
}

#: Fields sourced from `GET /objects/picklists/{name}`.
PICKLIST_BACKED = (FIELD_STUDY, COUNTRY_PICKLIST, FIELD_SITE)
#: Fields sourced from `GET /metadata/objects/documents/types/...`.
HIERARCHY_BACKED = {FIELD_TYPE: "types", FIELD_SUBTYPE: "subtypes", FIELD_CLASSIFICATION: "classifications"}


@dataclass
class ValidationResult:
    missing_required: list[str] = field(default_factory=list)
    invalid_picklist: dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.missing_required and not self.invalid_picklist

    @property
    def needs_sam(self) -> bool:
        """A well-formed value that VTMF does not yet know requires a SAM request."""
        return not self.missing_required and bool(self.invalid_picklist)

    def summary(self) -> str:
        parts = []
        if self.missing_required:
            parts.append("missing required fields: " + ", ".join(sorted(self.missing_required)))
        if self.invalid_picklist:
            parts.append(
                "values not configured in VTMF: "
                + ", ".join(f"{k}={v}" for k, v in sorted(self.invalid_picklist.items()))
            )
        return "; ".join(parts) or "valid"


class ValidationService:
    """Validates resolved metadata against required fields and cached VTMF reference data."""

    def __init__(self, reference_data: dict[str, set[str]]) -> None:
        self._reference = reference_data

    @classmethod
    async def load(cls, session: AsyncSession) -> ValidationService:
        rows = await session.execute(select(PicklistValue.picklist_name, PicklistValue.value_name))
        cache: dict[str, set[str]] = {}
        for name, value in rows.all():
            cache.setdefault(name, set()).add(value)
        return cls(cache)

    def validate(self, metadata: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()
        for field_name in REQUIRED_FIELDS:
            value = metadata.get(field_name)
            if value is None or str(value).strip() == "":
                result.missing_required.append(field_name)

        for field_name, reference_name in FIELD_PICKLISTS.items():
            value = metadata.get(field_name)
            if value is None or str(value).strip() == "":
                continue
            known = self._reference.get(reference_name)
            # An empty/absent cache means the reference data was never synced; do not block on it.
            if known and str(value) not in known:
                result.invalid_picklist[field_name] = str(value)
        return result


async def refresh_reference_data(
    session: AsyncSession, vault: VaultClient, trigger: str = "explicit"
) -> dict[str, int]:
    """Reload picklists and the document type hierarchy from Vault. Returns counts per field."""
    counts: dict[str, int] = {}
    now = datetime.now(UTC)

    async def _replace(name: str, values: list[dict]) -> None:
        await session.execute(delete(PicklistValue).where(PicklistValue.picklist_name == name))
        seen: set[str] = set()
        for item in values:
            value_name = str(item.get("name") or "").strip()
            if not value_name or value_name in seen:
                continue
            seen.add(value_name)
            session.add(
                PicklistValue(
                    picklist_name=name,
                    value_name=value_name,
                    label=item.get("label"),
                    refreshed_at=now,
                )
            )
        counts[name] = len(seen)

    for picklist in PICKLIST_BACKED:
        await _replace(picklist, await vault.get_picklist_values(picklist))

    hierarchy = await vault.get_document_type_hierarchy()
    for reference_name, key in HIERARCHY_BACKED.items():
        await _replace(reference_name, hierarchy.get(key) or [])

    await session.flush()
    PICKLIST_REFRESHES.labels(trigger=trigger).inc()
    return counts


async def reference_cache_age_seconds(session: AsyncSession) -> float | None:
    """Age of the freshest cached reference value, or None when the cache is empty."""
    newest = (await session.execute(select(func.max(PicklistValue.refreshed_at)))).scalar_one_or_none()
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return (datetime.now(UTC) - newest).total_seconds()


async def ensure_picklists_fresh(
    session: AsyncSession, vault: VaultClient, ttl_seconds: int
) -> dict[str, int] | None:
    """Prefetch reference data once per batch when the cache is empty or past its TTL."""
    age = await reference_cache_age_seconds(session)
    if age is not None and age < ttl_seconds:
        return None
    return await refresh_reference_data(session, vault, trigger="ttl")


#: Retained for callers that predate the type-hierarchy split.
refresh_picklists = refresh_reference_data
