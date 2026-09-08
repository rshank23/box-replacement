#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Applying database migrations"
alembic -c db/alembic.ini upgrade head

echo "==> Generating demo ZIP fixtures"
python scripts/make_demo_data.py

echo "==> Seeding mapping rules and picklists"
python scripts/seed.py
