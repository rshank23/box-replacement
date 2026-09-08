"""Initial schema for MBox to VTMF automation.

Revision ID: V001_init
Revises:
Create Date: 2026-01-01

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "V001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    op.create_table(
        "mapping_rules",
        sa.Column("mapping_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_pattern", sa.String(length=500), nullable=False),
        sa.Column("target_study", sa.String(length=100)),
        sa.Column("target_country", sa.String(length=100)),
        sa.Column("target_site", sa.String(length=100)),
        sa.Column("document_type", sa.String(length=200)),
        sa.Column("document_subtype", sa.String(length=200)),
        sa.Column("classification", sa.String(length=200)),
        sa.Column("default_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.Column("match_type", sa.String(length=20), nullable=False, server_default="EXACT"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=100)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "match_type in ('EXACT','REGEX','STUDY_DEFAULT','GLOBAL_DEFAULT')",
            name="ck_mapping_rules_match_type",
        ),
    )
    op.create_index("ix_mapping_rules_is_active", "mapping_rules", ["is_active"])
    op.create_index("ix_mapping_rules_source_pattern", "mapping_rules", ["source_pattern"])

    op.create_table(
        "transfer_log",
        sa.Column("transfer_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_checksum", sa.String(length=100)),
        sa.Column("file_size_bytes", sa.BigInteger()),
        sa.Column("vault_document_id", sa.String(length=100)),
        sa.Column("mapping_id_used", sa.Integer(), sa.ForeignKey("mapping_rules.mapping_id")),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("initiated_by", sa.String(length=10), nullable=False, server_default="SYSTEM"),
        sa.Column("message", sa.Text()),
        sa.Column("resolved_metadata", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "status in ('PENDING','PROCESSING','SUCCESS','FAILED','EXCEPTION','DUPLICATE_SKIPPED','SAM_PENDING')",
            name="ck_transfer_log_status",
        ),
        sa.CheckConstraint("initiated_by in ('SYSTEM','USER')", name="ck_transfer_log_initiated_by"),
    )
    op.create_index("ix_transfer_log_status", "transfer_log", ["status"])
    op.create_index("ix_transfer_log_created_at", "transfer_log", ["created_at"])
    op.create_index("ix_transfer_log_checksum", "transfer_log", ["file_checksum"])

    op.create_table(
        "failure_queue",
        sa.Column("failure_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfer_log.transfer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=40), nullable=False),
        sa.Column("failure_detail", sa.Text()),
        sa.Column("suggested_mapping", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("assigned_to", sa.String(length=100)),
        sa.Column("resolution_status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "failure_reason in ('NO_MAPPING','VALIDATION_ERROR','VAULT_API_ERROR','RATE_LIMIT',"
            "'CORRUPT_ARCHIVE','MISSING_PICKLIST')",
            name="ck_failure_queue_reason",
        ),
        sa.CheckConstraint(
            "resolution_status in ('OPEN','RESOLVED','ESCALATED')", name="ck_failure_queue_resolution_status"
        ),
    )
    op.create_index("ix_failure_queue_resolution_status", "failure_queue", ["resolution_status"])

    op.create_table(
        "audit_trail",
        sa.Column("audit_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("performed_by", sa.String(length=100), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("source_system", sa.String(length=50)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_audit_trail_timestamp", "audit_trail", ["timestamp"])

    op.create_table(
        "sam_action_queue",
        sa.Column("sam_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfer_log.transfer_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("missing_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('OPEN','REQUESTED','COMPLETED')", name="ck_sam_action_queue_status"),
    )
    op.create_index("ix_sam_action_queue_status", "sam_action_queue", ["status"])

    op.create_table(
        "picklist_cache",
        sa.Column("picklist_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("picklist_name", sa.String(length=100), nullable=False),
        sa.Column("value_name", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=300)),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_picklist_cache_name_value", "picklist_cache", ["picklist_name", "value_name"], unique=True
    )

    op.create_table(
        "folder_watch",
        sa.Column("folder_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("folder_path", sa.Text(), nullable=False, unique=True),
        sa.Column("study", sa.String(length=100)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_alert_level", sa.String(length=20)),
    )
    op.create_index("ix_folder_watch_study", "folder_watch", ["study"])

    # --- 21 CFR Part 11: the audit trail is append-only at the database level ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_trail_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_trail is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_trail_no_update
        BEFORE UPDATE OR DELETE ON audit_trail
        FOR EACH ROW EXECUTE FUNCTION audit_trail_immutable();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_trail_no_truncate() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_trail is append-only: TRUNCATE is not permitted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_trail_no_truncate
        BEFORE TRUNCATE ON audit_trail
        FOR EACH STATEMENT EXECUTE FUNCTION audit_trail_no_truncate();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_mapping_rules_updated_at
        BEFORE UPDATE ON mapping_rules
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_transfer_log_updated_at
        BEFORE UPDATE ON transfer_log
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_transfer_log_updated_at ON transfer_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_mapping_rules_updated_at ON mapping_rules;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_trail_no_truncate ON audit_trail;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_trail_no_update ON audit_trail;")
    op.execute("DROP FUNCTION IF EXISTS audit_trail_no_truncate();")
    op.execute("DROP FUNCTION IF EXISTS audit_trail_immutable();")

    op.drop_table("folder_watch")
    op.drop_table("picklist_cache")
    op.drop_table("sam_action_queue")
    op.drop_table("audit_trail")
    op.drop_table("failure_queue")
    op.drop_table("transfer_log")
    op.drop_table("mapping_rules")
