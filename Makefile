.PHONY: install lint test migrate seed demo api watcher up down logs openapi

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check app tests scripts db

test:
	pytest

migrate:
	alembic -c db/alembic.ini upgrade head

seed:
	python scripts/seed.py

demo:
	python scripts/make_demo_data.py

api:
	uvicorn app.services.integration_api.main:app --host 0.0.0.0 --port 8080 --reload

watcher:
	python -m app.workers.watcher.main

up:
	docker compose -f deploy/docker-compose.yml up -d --build

down:
	docker compose -f deploy/docker-compose.yml down -v

logs:
	docker compose -f deploy/docker-compose.yml logs -f

openapi:
	python -c "import json;from app.services.integration_api.main import app;print(json.dumps(app.openapi(), indent=2))" > openapi.json
