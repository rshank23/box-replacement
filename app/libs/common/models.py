from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: JSONB on PostgreSQL, plain JSON elsewhere (SQLite is used for fast unit tests).
JSONType = JSONB().with_variant(JSON(), "sqlite")
UUIDType = Uuid(as_uuid=True)


class Base(DeclarativeBase):
    pass


class TransferStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    EXCEPTION = "EXCEPTION"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
    SAM_PENDING = "SAM_PENDING"


class InitiatedBy(enum.StrEnum):
    SYSTEM = "SYSTEM"
    USER = "USER"


class FailureReason(enum.StrEnum):
    NO_MAPPING = "NO_MAPPING"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VAULT_API_ERROR = "VAULT_API_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CORRUPT_ARCHIVE = "CORRUPT_ARCHIVE"
    MISSING_PICKLIST = "MISSING_PICKLIST"


class ResolutionStatus(enum.StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class SamStatus(enum.StrEnum):
    OPEN = "OPEN"
    REQUESTED = "REQUESTED"
    COMPLETED = "COMPLETED"


class AuditAction(enum.StrEnum):
    UPLOAD = "UPLOAD"
    MAP = "MAP"
    CLASSIFY = "CLASSIFY"
    RETRY = "RETRY"
    DELETE = "DELETE"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    SCAN = "SCAN"
    VALIDATE = "VALIDATE"
    SAM_REQUEST = "SAM_REQUEST"
    NOTIFY = "NOTIFY"


class MappingRule(Base):
    __tablename__ = "mapping_rules"

    mapping_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    target_study: Mapped[str | None] = mapped_column(String(100))
    target_country: Mapped[str | None] = mapped_column(String(100))
    target_site: Mapped[str | None] = mapped_column(String(100))
    document_type: Mapped[str | None] = mapped_column(String(200))
    document_subtype: Mapped[str | None] = mapped_column(String(200))
    classification: Mapped[str | None] = mapped_column(String(200))
    default_metadata: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False, default="EXACT")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "match_type in ('EXACT','REGEX','STUDY_DEFAULT','GLOBAL_DEFAULT')",
            name="ck_mapping_rules_match_type",
        ),
        Index("ix_mapping_rules_is_active", "is_active"),
        Index("ix_mapping_rules_source_pattern", "source_pattern"),
    )


class TransferLog(Base):
    __tablename__ = "transfer_log"

    transfer_id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_checksum: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    vault_document_id: Mapped[str | None] = mapped_column(String(100))
    mapping_id_used: Mapped[int | None] = mapped_column(Integer, ForeignKey("mapping_rules.mapping_id"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=TransferStatus.PENDING.value)
    initiated_by: Mapped[str] = mapped_column(String(10), nullable=False, default=InitiatedBy.SYSTEM.value)
    message: Mapped[str | None] = mapped_column(Text)
    resolved_metadata: Mapped[dict | None] = mapped_column(JSONType)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False, default=uuid.uuid4)

    __table_args__ = (
        CheckConstraint(
            "status in ('PENDING','PROCESSING','SUCCESS','FAILED','EXCEPTION','DUPLICATE_SKIPPED','SAM_PENDING')",
            name="ck_transfer_log_status",
        ),
        CheckConstraint("initiated_by in ('SYSTEM','USER')", name="ck_transfer_log_initiated_by"),
        Index("ix_transfer_log_status", "status"),
        Index("ix_transfer_log_created_at", "created_at"),
        Index("ix_transfer_log_checksum", "file_checksum"),
    )


class FailureQueue(Base):
    __tablename__ = "failure_queue"

    failure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("transfer_log.transfer_id", ondelete="CASCADE"), nullable=False
    )
    failure_reason: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    suggested_mapping: Mapped[dict | None] = mapped_column(JSONType)
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False, default=ResolutionStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "failure_reason in ('NO_MAPPING','VALIDATION_ERROR','VAULT_API_ERROR','RATE_LIMIT',"
            "'CORRUPT_ARCHIVE','MISSING_PICKLIST')",
            name="ck_failure_queue_reason",
        ),
        CheckConstraint(
            "resolution_status in ('OPEN','RESOLVED','ESCALATED')", name="ck_failure_queue_resolution_status"
        ),
        Index("ix_failure_queue_resolution_status", "resolution_status"),
    )


class AuditTrail(Base):
    """Append-only. UPDATE/DELETE are rejected by a database trigger."""

    __tablename__ = "audit_trail"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    performed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    source_system: Mapped[str | None] = mapped_column(String(50))
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType)

    __table_args__ = (Index("ix_audit_trail_timestamp", "timestamp"),)


class SamActionQueue(Base):
    __tablename__ = "sam_action_queue"

    sam_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("transfer_log.transfer_id", ondelete="CASCADE"), nullable=False
    )
    missing_fields: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SamStatus.OPEN.value)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status in ('OPEN','REQUESTED','COMPLETED')", name="ck_sam_action_queue_status"),
        Index("ix_sam_action_queue_status", "status"),
    )


class PicklistValue(Base):
    """Local cache of VTMF picklist values used by the validation service."""

    __tablename__ = "picklist_cache"

    picklist_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    picklist_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value_name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_picklist_cache_name_value", "picklist_name", "value_name", unique=True),)


class FolderWatch(Base):
    """First-seen tracking per study folder, used for 90-day retention alerting."""

    __tablename__ = "folder_watch"

    folder_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    study: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_alert_level: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (Index("ix_folder_watch_study", "study"),)
