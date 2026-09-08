from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="mbox-tests-"))
_MBOX = _TMP / "mbox"

# Environment must be set before any application module reads the settings cache.
os.environ.update(
    {
        "DB_DSN": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
        "MBOX_ROOT": str(_MBOX),
        "VAULT_CLIENT": "fake",
        "AUTH_ENABLED": "false",
        "DEV_TOKEN_ENABLED": "false",
        "PROMETHEUS_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
        "SERVICE_ACCOUNT_ID": "svc-test",
        "JWT_SECRET": "test-secret-not-a-real-credential",
        "MIN_FOLDER_DEPTH": "3",
        "MAX_FILE_MB": "50",
        "LARGE_FILE_MB": "1",
    }
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.libs.common import db as db_module  # noqa: E402
from app.libs.common.config import get_settings  # noqa: E402
from app.libs.common.models import Base, MappingRule  # noqa: E402
from app.libs.common.vault_client import FakeVaultClient  # noqa: E402
from app.libs.common.vault_factory import set_vault_client  # noqa: E402
from app.services.integration_api.domain.validation import refresh_reference_data  # noqa: E402

SEED_RULES = [
    dict(
        source_pattern="STUDY-001/US/SITE-101/Trial Management",
        match_type="EXACT",
        priority=10,
        target_study="STUDY-001",
        target_country="US",
        target_site="SITE-101",
        document_type="Trial Management",
        document_subtype="Trial Master File Plan",
        classification="Essential Document",
    ),
    dict(
        source_pattern=r".*/Monitoring/.*",
        match_type="REGEX",
        priority=50,
        document_type="Trial Management",
        document_subtype="Monitoring Plan",
        classification="Essential Document",
    ),
    dict(
        source_pattern="STUDY-002",
        match_type="STUDY_DEFAULT",
        priority=100,
        document_type="Safety Reporting",
        document_subtype="SAE Report",
        classification="Regulated Correspondence",  # not a picklist value -> SAM
    ),
    dict(
        source_pattern="STUDY-003",
        match_type="STUDY_DEFAULT",
        priority=100,
        document_type="Site Management",
        document_subtype="Site Signature Sheet",
        classification="Essential Document",
    ),
]


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def mbox_root() -> Path:
    _write(_MBOX / "STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf", "tmf plan")
    _write(_MBOX / "STUDY-001/US/SITE-101/Monitoring/monitoring-plan.pdf", "monitoring plan")
    _write(_MBOX / "STUDY-002/GB/SITE-201/Safety/sae-2024-001.pdf", "sae report")
    _write(_MBOX / "STUDY-004/US/SITE-101/Trial Management/orphan.pdf", "orphan")
    _write(_MBOX / "STUDY-001/US/SITE-101/Trial Management/notes.txt", "disallowed extension")
    _write(_MBOX / "shallow.pdf", "too shallow")

    nested_dir = _MBOX / "STUDY-003/JP/SITE-301/Site Management"
    nested_dir.mkdir(parents=True, exist_ok=True)
    inner = nested_dir / "_inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("signature-sheet.pdf", "signature sheet")
    with zipfile.ZipFile(nested_dir / "site-docs.zip", "w") as zf:
        zf.writestr("delegation-log.pdf", "delegation log")
        zf.writestr("level-2/inner.zip", inner.read_bytes())
    inner.unlink()

    (nested_dir / "corrupt.zip").write_bytes(b"PK\x03\x04not-a-zip")
    return _MBOX


@pytest_asyncio.fixture
async def engine():
    get_settings.cache_clear()
    settings = get_settings()
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(settings.async_db_dsn)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    db_module.configure_engine(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def vault() -> FakeVaultClient:
    client = FakeVaultClient()
    set_vault_client(client)
    yield client
    set_vault_client(None)


@pytest_asyncio.fixture
async def session(engine):
    async with db_module.session_scope() as s:
        yield s


@pytest_asyncio.fixture
async def seeded(engine, vault, mbox_root):
    """Database with mapping rules and a populated VTMF reference-data cache."""
    async with db_module.session_scope() as s:
        for rule in SEED_RULES:
            s.add(MappingRule(**rule, created_by="test", updated_by="test"))
        await vault.authenticate()
        await refresh_reference_data(s, vault)
    return True


@pytest_asyncio.fixture
async def api_client(seeded):
    from app.services.integration_api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
