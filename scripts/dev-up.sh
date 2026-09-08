#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Building and starting the local stack"
docker compose -f deploy/docker-compose.yml up -d --build

echo "==> Waiting for the API to become ready"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8080/health/ready >/dev/null 2>&1; then
    echo "API is ready."
    exit 0
  fi
  sleep 2
done

echo "API did not become ready in time; check 'docker compose logs api'." >&2
exit 1
