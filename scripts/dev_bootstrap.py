"""Create the schema directly from the ORM models, for local runs without Docker.

Postgres deployments must use Alembic (`alembic -c db/alembic.ini upgrade head`) so the
Part 11 audit-trail triggers are installed; this shortcut is for a disposable dev database.

    python scripts/dev_bootstrap.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.libs.common.config import get_settings  # noqa: E402
from app.libs.common.db import dispose_engine, get_engine  # noqa: E402
from app.libs.common.models import Base  # noqa: E402


async def main() -> None:
    settings = get_settings()
    if not settings.async_db_dsn.startswith("sqlite"):
        raise SystemExit(
            f"Refusing to create tables on {settings.async_db_dsn.split('://')[0]}; run Alembic instead."
        )
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await dispose_engine()
    print(f"schema created on {settings.db_dsn}")


if __name__ == "__main__":
    asyncio.run(main())
