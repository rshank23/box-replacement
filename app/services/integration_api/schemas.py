from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- mbox
class MBoxEntryOut(BaseModel):
    name: str
    relative_path: str
    is_dir: bool
    size_bytes: int | None = None
    modified_at: datetime | None = None


# ---------------------------------------------------------------- transfers
class TransferRequestItem(BaseModel):
    source_path: str = Field(min_length=1, max_length=4000)
    initiated_by: Literal["SYSTEM", "USER"] = "USER"
    staged_path: str | None = Field(default=None, max_length=4000)
    checksum: str | None = Field(default=None, max_length=100)
    override_duplicate: bool = False
    metadata_override: dict[str, Any] | None = None

    @field_validator("source_path")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(c) < 32 for c in value):
            raise ValueError("source_path contains control characters")
        return value.strip()


class TransferSubmitRequest(BaseModel):
    files: list[TransferRequestItem] = Field(min_length=1, max_length=1000)
    correlation_id: uuid.UUID | None = None


class TransferOut(ORMModel):
    transfer_id: uuid.UUID
    source_path: str
    file_name: str
    file_checksum: str | None = None
    file_size_bytes: int | None = None
    vault_document_id: str | None = None
    mapping_id_used: int | None = None
    status: str
    initiated_by: str
    message: str | None = None
    resolved_metadata: dict[str, Any] | None = None
    attempt_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    correlation_id: uuid.UUID


class TransferSubmitResponse(BaseModel):
    correlation_id: uuid.UUID
    submitted: int
    results: list[TransferOut]


# ----------------------------------------------------------------- failures
class FailureOut(ORMModel):
    failure_id: int
    transfer_id: uuid.UUID
    failure_reason: str
    failure_detail: str | None = None
    suggested_mapping: dict[str, Any] | None = None
    assigned_to: str | None = None
    resolution_status: str
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class MappingOverride(BaseModel):
    target_study: str | None = None
    target_country: str | None = None
    target_site: str | None = None
    document_type: str | None = None
    document_subtype: str | None = None
    classification: str | None = None
    default_metadata: dict[str, Any] | None = None


class FailureResolveRequest(BaseModel):
    mapping_override: MappingOverride
    persist_as_rule: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class FailureResolveResponse(BaseModel):
    failure_id: int
    transfer: TransferOut
    resolution_status: str


# ----------------------------------------------------------------- mappings
class MappingRuleBase(BaseModel):
    source_pattern: str = Field(min_length=1, max_length=500)
    target_study: str | None = None
    target_country: str | None = None
    target_site: str | None = None
    document_type: str | None = None
    document_subtype: str | None = None
    classification: str | None = None
    default_metadata: dict[str, Any] | None = None
    match_type: Literal["EXACT", "REGEX", "STUDY_DEFAULT", "GLOBAL_DEFAULT"] = "EXACT"
    priority: int = 100
    is_active: bool = True


class MappingRuleCreate(MappingRuleBase):
    pass


class MappingRuleUpdate(BaseModel):
    source_pattern: str | None = Field(default=None, min_length=1, max_length=500)
    target_study: str | None = None
    target_country: str | None = None
    target_site: str | None = None
    document_type: str | None = None
    document_subtype: str | None = None
    classification: str | None = None
    default_metadata: dict[str, Any] | None = None
    match_type: Literal["EXACT", "REGEX", "STUDY_DEFAULT", "GLOBAL_DEFAULT"] | None = None
    priority: int | None = None
    is_active: bool | None = None


class MappingRuleOut(ORMModel, MappingRuleBase):
    mapping_id: int
    version: int
    created_by: str | None = None
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


# -------------------------------------------------------------------- audit
class AuditOut(ORMModel):
    audit_id: int
    action: str
    performed_by: str
    timestamp: datetime
    details: dict[str, Any] | None = None
    source_system: str | None = None
    correlation_id: uuid.UUID | None = None


# ---------------------------------------------------------------------- SAM
class SamActionOut(ORMModel):
    sam_id: int
    transfer_id: uuid.UUID
    missing_fields: dict[str, Any]
    status: str
    requested_at: datetime | None = None
    completed_at: datetime | None = None


# -------------------------------------------------------------- dashboard
class FolderAgeOut(BaseModel):
    folder_path: str
    study: str | None = None
    age_days: int
    alert_level: str


class DashboardStats(BaseModel):
    transfers_by_status: dict[str, int]
    failures_by_reason: dict[str, int]
    open_failures: int
    open_sam_items: int
    mapping_hit_rate: float
    total_transfers: int
    documents_in_vault: int
    aging_folders: list[FolderAgeOut]
    generated_at: datetime
