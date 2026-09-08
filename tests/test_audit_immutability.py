"""Database-level verification of the append-only audit trail (21 CFR Part 11).

Requires Docker; skipped automatically when Testcontainers cannot start.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url():
    testcontainers = pytest.importorskip("testcontainers.postgres")
    try:
        with testcontainers.PostgresContainer("postgres:14-alpine") as container:
            yield container.get_connection_url().replace("postgresql+psycopg2", "postgresql+psycopg2")
    except Exception as exc:  # docker not available in this environment
        pytest.skip(f"Testcontainers unavailable: {exc}")


@pytest.fixture(scope="module")
def migrated(pg_url):
    import os

    from alembic import command
    from alembic.config import Config

    from app.libs.common.config import get_settings

    previous = os.environ.get("DB_DSN")
    os.environ["DB_DSN"] = pg_url
    get_settings.cache_clear()
    try:
        cfg = Config("db/alembic.ini")
        command.upgrade(cfg, "head")
        yield pg_url
    finally:
        if previous is not None:
            os.environ["DB_DSN"] = previous
        get_settings.cache_clear()


def test_audit_trail_rejects_update_and_delete(migrated):
    engine = create_engine(migrated)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_trail (action, performed_by, source_system) "
                "VALUES ('UPLOAD', 'svc-test', 'pytest')"
            )
        )

    with engine.connect() as conn, pytest.raises(Exception, match="append-only"):
        conn.execute(text("UPDATE audit_trail SET performed_by = 'attacker'"))

    with engine.connect() as conn, pytest.raises(Exception, match="append-only"):
        conn.execute(text("DELETE FROM audit_trail"))

    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT count(*) FROM audit_trail")).scalar_one()
    assert remaining == 1
    engine.dispose()


def test_transfer_log_status_check_constraint(migrated):
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated)
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO transfer_log (transfer_id, source_path, file_name, status, initiated_by, "
                "correlation_id) VALUES (gen_random_uuid(), 'a/b/c/d.pdf', 'd.pdf', 'NOT_A_STATUS', "
                "'SYSTEM', gen_random_uuid())"
            )
        )
    engine.dispose()
