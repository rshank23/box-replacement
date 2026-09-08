# MBox → Veeva Vault TMF Automation

Automates post-study archival of clinical trial documents from MBox into Veeva Vault TMF (VTMF),
with mapping, validation, exception handling and full auditability. See [backend.md](backend.md)
for architecture, endpoints and operations details, and [frontend/README.md](frontend/README.md)
for the operations UI.

## 1. Prerequisites

- Git
- Python 3.11+
- Node.js 18+ (for the frontend)
- Docker and Docker Compose (recommended, avoids installing PostgreSQL locally)
- PostgreSQL 14+ (only needed if you run without Docker)

## 2. Clone the repo

```bash
git clone https://github.com/rshank23/box-replacement.git
cd box-replacement
```

## 3. Configure environment variables

Copy the example file and adjust values as needed (defaults work for local/demo use):

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

`.env` is for local development only and must never be committed.

## 4. Run with Docker Compose (recommended)

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

This builds the images, starts PostgreSQL, applies migrations, generates demo data and seeds
mapping rules.

```bash
curl http://localhost:8080/health
curl http://localhost:8080/api/dashboard/stats
```

- API: `http://localhost:8080` (docs at `http://localhost:8080/docs`)
- UI: `http://localhost:3000`

Optional Prometheus monitoring stack:

```bash
docker compose -f deploy/docker-compose.yml --profile observability up -d
```

Stop everything:

```bash
docker compose -f deploy/docker-compose.yml down
```

## 5. Run without Docker

### Backend

```bash
python -m venv .venv
. .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

alembic -c db/alembic.ini upgrade head
python scripts/make_demo_data.py   # nested + corrupt ZIP fixtures
python scripts/seed.py             # mapping rules + picklist cache

uvicorn app.services.integration_api.main:app --port 8080 --reload
```

In a second terminal, start the watcher:

```bash
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m app.workers.watcher.main
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to `http://localhost:8080` (see `frontend/vite.config.ts`).

### Convenience scripts

`scripts/dev-up.sh`, `scripts/dev-down.sh` and `scripts/load-seed.sh` wrap the steps above, and the
`Makefile` exposes `install`, `lint`, `test`, `migrate`, `seed`, `api`, `watcher`, `up`, `down`.

```bash
make install
make migrate
make seed
make api        # in one shell
make watcher     # in another shell
```

## 6. Run the tests

```bash
pytest                      # unit + integration
pytest -m integration       # Testcontainers PostgreSQL (requires Docker)
ruff check app tests scripts db
```

## 7. Further reading

- [backend.md](backend.md) — architecture, configuration reference, endpoints, data model,
  compliance/security, observability, operations runbook
- [frontend/README.md](frontend/README.md) — frontend-specific setup and structure
- `http://localhost:8080/docs` — interactive API docs once the backend is running
