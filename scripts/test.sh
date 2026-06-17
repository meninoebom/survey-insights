#!/usr/bin/env bash
# Run the full test suite against a disposable Postgres, then tear it down.
# Requires Docker and uv. Usage: ./scripts/test.sh [extra pytest args]
set -euo pipefail

PORT="${TEST_DB_PORT:-55433}"
NAME="survey-test-db"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker run -d --rm --name "$NAME" \
  -e POSTGRES_USER=survey -e POSTGRES_PASSWORD=survey -e POSTGRES_DB=survey \
  -p "${PORT}:5432" postgres:16 >/dev/null

export DATABASE_URL="postgresql+psycopg://survey:survey@localhost:${PORT}/survey"

# Wait for the database to accept connections (no shell sleep dependency).
uv run python - <<'PY'
import os
import sys
import time

import psycopg

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
for _ in range(60):
    try:
        psycopg.connect(url).close()
        sys.exit(0)
    except Exception:
        time.sleep(0.5)
sys.exit("test database never became ready")
PY

uv run pytest "$@"
