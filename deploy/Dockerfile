# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml backend.md ./
COPY app ./app
COPY db ./db
COPY scripts ./scripts

RUN python -m pip install --upgrade pip && python -m pip install .

# Least privilege: the service account owns nothing it does not need to write.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/mbox \
    && chown -R appuser:appuser /srv /data
USER appuser

EXPOSE 8080 9101

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.services.integration_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
