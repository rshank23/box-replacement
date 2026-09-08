from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.models import MappingRule
from app.libs.common.path_parser import PathInfo, normalize_path

#: Resolution order. The first tier that produces a match wins.
PRECEDENCE = ("EXACT", "REGEX", "STUDY_DEFAULT", "GLOBAL_DEFAULT")

#: Metadata fields that a mapping rule can supply.
MAPPED_FIELDS = (
    "target_study",
    "target_country",
    "target_site",
    "document_type",
    "document_subtype",
    "classification",
)


@dataclass(frozen=True)
class MappingResult:
    rule_id: int | None
    match_type: str | None
    metadata: dict[str, Any]

    @property
    def matched(self) -> bool:
        return self.rule_id is not None


def _rule_matches(rule: MappingRule, info: PathInfo) -> bool:
    pattern = normalize_path(rule.source_pattern or "")
    match_type = (rule.match_type or "EXACT").upper()

    if match_type == "GLOBAL_DEFAULT":
        return True
    if match_type == "STUDY_DEFAULT":
        return bool(info.study) and pattern.lower() == (info.study or "").lower()
    if match_type == "REGEX":
        try:
            return re.search(rule.source_pattern, info.relative_path, re.IGNORECASE) is not None
        except re.error:
            return False
    # EXACT: full relative path or the containing folder path.
    candidates = {info.relative_path.lower(), info.folder_path.lower()}
    return pattern.lower() in candidates


def compose_metadata(
    info: PathInfo,
    rule: MappingRule | None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine folder-derived values, rule targets and caller overrides.

    Precedence (highest first): explicit override, mapping rule value, folder hierarchy.
    """
    metadata: dict[str, Any] = {
        "study": info.study,
        "country": info.country,
        "site": info.site,
        "document_type": None,
        "document_subtype": None,
        "classification": None,
        "category": info.category,
        "file_name": info.file_name,
    }
    if rule is not None:
        if rule.default_metadata:
            metadata.update({k: v for k, v in rule.default_metadata.items() if v is not None})
        rule_values = {
            "study": rule.target_study,
            "country": rule.target_country,
            "site": rule.target_site,
            "document_type": rule.document_type,
            "document_subtype": rule.document_subtype,
            "classification": rule.classification,
        }
        metadata.update({k: v for k, v in rule_values.items() if v is not None})

    if overrides:
        alias = {
            "target_study": "study",
            "target_country": "country",
            "target_site": "site",
        }
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "default_metadata" and isinstance(value, dict):
                metadata.update({k: v for k, v in value.items() if v is not None})
            else:
                metadata[alias.get(key, key)] = value
    return metadata


class MappingEngine:
    """Resolves target VTMF metadata for a source path using versioned rules."""

    def __init__(self, rules: list[MappingRule]) -> None:
        self._rules = sorted(
            rules,
            key=lambda r: (
                PRECEDENCE.index((r.match_type or "EXACT").upper())
                if (r.match_type or "EXACT").upper() in PRECEDENCE
                else len(PRECEDENCE),
                r.priority,
                r.mapping_id or 0,
            ),
        )

    @classmethod
    async def load(cls, session: AsyncSession) -> MappingEngine:
        result = await session.execute(select(MappingRule).where(MappingRule.is_active.is_(True)))
        return cls(list(result.scalars().all()))

    def resolve(self, info: PathInfo, overrides: dict[str, Any] | None = None) -> MappingResult:
        for rule in self._rules:
            if _rule_matches(rule, info):
                return MappingResult(
                    rule_id=rule.mapping_id,
                    match_type=(rule.match_type or "EXACT").upper(),
                    metadata=compose_metadata(info, rule, overrides),
                )
        return MappingResult(rule_id=None, match_type=None, metadata=compose_metadata(info, None, overrides))

    def suggest(self, info: PathInfo) -> dict[str, Any]:
        """Best-effort mapping suggestion shown to a human resolving a failure."""
        return {
            "source_pattern": info.folder_path or info.relative_path,
            "match_type": "EXACT",
            "target_study": info.study,
            "target_country": info.country,
            "target_site": info.site,
            "document_type": info.category,
        }
