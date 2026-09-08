from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso_duration_seconds(value: str) -> int:
    """Parse a restricted ISO-8601 duration (e.g. ``PT30M``) into seconds."""
    match = _ISO_DURATION.match(value.strip().upper())
    if not match or not any(match.groupdict().values()):
        raise ValueError(f"Invalid ISO-8601 duration: {value!r}")
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


class Settings(BaseSettings):
    """Runtime configuration. Every value is overridable through the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---------------------------------------------------------
    db_dsn: str = "postgresql+psycopg2://app:app@localhost:5432/mbox_vtmf"

    # --- MBox source ------------------------------------------------------
    mbox_root: str = "./demo/mbox"
    #: Sinks used by the filesystem (POC) Vault client.
    destination_root: str = "./poc/destination"
    unclassified_root: str = "./poc/unclassified"
    mbox_poll_iso_duration: str = "PT30M"
    min_folder_depth: int = 3
    allowed_extensions: str = ".pdf,.docx,.xlsx,.zip"
    max_file_mb: int = 500
    zip_max_depth: int = -1
    folder_age_warn_days: int = 60
    folder_age_critical_days: int = 75
    folder_retention_days: int = 90

    # --- Vault ------------------------------------------------------------
    #: fake = in-memory stub, real = live Vault REST API, filesystem = POC folder sink
    vault_client: Literal["fake", "real", "filesystem"] = "fake"
    vault_base_url: str = "https://vault.example.com"
    vault_api_version: str = "v26.2"
    #: "token" uses an API access token (recommended); "password" opens a session.
    vault_auth_mode: Literal["token", "password"] = "password"
    vault_api_token: str = ""
    vault_username: str = ""
    vault_password: str = ""
    #: Guards against Vault authentication defaulting to the wrong Vault when one is inactive.
    vault_expected_vault_id: int | None = None
    vault_session_idle_keep_alive_s: int = 1800
    #: Post-study archival is a migration; the header bypasses entry actions and notifications.
    vault_migration_mode: bool = False
    vault_no_triggers: bool = False
    vault_file_part_mb: int = 25
    vault_job_timeout_s: int = 1800
    document_lifecycle: str = "TMF Document Lifecycle"
    large_file_mb: int = 50
    rate_limit_per_sec: float = 5
    retry_max_attempts: int = 3
    retry_backoff_s: str = "30,120,600"
    circuit_breaker_fail_max: int = 5
    circuit_breaker_reset_s: int = 60

    # --- Batch behaviour ---------------------------------------------------
    #: Picklists are prefetched once per batch and reused until this TTL expires.
    picklist_cache_ttl_s: int = 3600
    #: "queue" holds unmapped files for a human; "unclassified" also files them in VTMF.
    unmapped_policy: Literal["queue", "unclassified"] = "queue"
    unclassified_document_type: str = "Unclassified"
    unclassified_document_subtype: str = "Unclassified"
    unclassified_classification: str = "Unclassified"
    #: Vault pairs the Unclassified document type with the Inbox lifecycle.
    unclassified_lifecycle: str = "Inbox"

    # --- Batch summary digest ----------------------------------------------
    mail_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    mail_from: str = "mbox-vtmf@example.com"
    mail_to: str = ""
    mail_subject_prefix: str = "[MBox->VTMF]"

    # --- Integration API --------------------------------------------------
    integration_api_base_url: str = "http://localhost:8080"
    api_host: str = "0.0.0.0"  # noqa: S104 - bound inside the container network only
    api_port: int = 8080

    # --- Identity / security ----------------------------------------------
    service_account_id: str = "svc-mbox"
    jwt_secret: str = "change-me-in-every-environment-min-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "mbox-vtmf"
    auth_enabled: bool = True
    admin_role: str = "tmf_admin"
    cors_allow_origins: str = ""
    # Local-development only: exposes POST /api/admin/dev-token. Must stay false in validated envs.
    dev_token_enabled: bool = False

    # --- Observability ----------------------------------------------------
    log_level: str = "INFO"
    prometheus_enabled: bool = True

    # --- Derived helpers ---------------------------------------------------
    @field_validator("mbox_poll_iso_duration")
    @classmethod
    def _validate_duration(cls, value: str) -> str:
        parse_iso_duration_seconds(value)
        return value

    @property
    def poll_interval_seconds(self) -> int:
        return parse_iso_duration_seconds(self.mbox_poll_iso_duration)

    @property
    def allowed_extension_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()}

    @property
    def retry_backoff_schedule(self) -> list[int]:
        return [int(x.strip()) for x in self.retry_backoff_s.split(",") if x.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def mail_recipients(self) -> list[str]:
        return [r.strip() for r in self.mail_to.split(",") if r.strip()]

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def large_file_bytes(self) -> int:
        return self.large_file_mb * 1024 * 1024

    @property
    def vault_file_part_bytes(self) -> int:
        # Vault requires parts of at least 5 MB (except the last) and at most 52 MB.
        return min(max(self.vault_file_part_mb, 5), 52) * 1024 * 1024

    @property
    def async_db_dsn(self) -> str:
        """SQLAlchemy async DSN derived from ``db_dsn`` (which may name a sync driver)."""
        dsn = self.db_dsn
        for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://", "postgres://"):
            if dsn.startswith(prefix):
                return "postgresql+asyncpg://" + dsn[len(prefix) :]
        return dsn

    @property
    def sync_db_dsn(self) -> str:
        """Sync DSN used by Alembic."""
        if self.db_dsn.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg2://" + self.db_dsn[len("postgresql+asyncpg://") :]
        return self.db_dsn


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()

__all__ = ["Settings", "get_settings", "parse_iso_duration_seconds", "settings"]
