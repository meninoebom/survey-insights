#!/usr/bin/env bash
# One command to explore the survey data in the TUI.
#
# Starts a throwaway Postgres in Docker, loads the bundled sample, and runs the
# Textual TUI on your machine so drag-and-drop re-ingest works. The only thing you
# need installed is Docker; uv is installed for you if it is missing. The TUI takes
# over the screen; press 'q' or Ctrl+C to quit, and the database is torn down on exit.
#
#   ./run-tui.sh
#
set -euo pipefail

# Preflight: the only hard prerequisite is Docker. uv (the Python runner that
# supplies the TUI's libraries on your machine) is installed on demand if missing.
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker isn't installed. Get Docker Desktop from" >&2
  echo "https://www.docker.com/products/docker-desktop/ then re-run this." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Open Docker Desktop, wait for it to" >&2
  echo "finish starting, then re-run this." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (one-time, from the official astral.sh installer)..." >&2
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # The installer edits your shell profile, not this running shell, so make uv
  # reachable for the rest of this run.
  if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv was installed but isn't on PATH yet. Open a new terminal and re-run." >&2
  exit 1
fi

PORT="${TUI_DB_PORT:-55440}"
NAME="survey-tui-pg"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker run -d --rm --name "$NAME" \
  -e POSTGRES_USER=survey -e POSTGRES_PASSWORD=survey -e POSTGRES_DB=survey \
  -p "${PORT}:5432" postgres:16 >/dev/null

export DATABASE_URL="postgresql+psycopg://survey:survey@localhost:${PORT}/survey"

# Wait for the database, then ingest the sample and build the distributions.
uv run python - <<'PY'
import os
import time

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from survey.db.session import init_db
from survey.ingest.pipeline import ingest_responses
from survey.ingest.source import LocalDirectorySource
from survey.service.distributions import rebuild_distributions

dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
for _ in range(60):
    try:
        psycopg.connect(dsn).close()
        break
    except Exception:
        time.sleep(0.5)

engine = create_engine(os.environ["DATABASE_URL"])
init_db(engine)
with Session(engine) as session, session.begin():
    ingest_responses(LocalDirectorySource("us_ai_survey_unique_50.csv"), session)
    rebuild_distributions(session)
print("sample ingested; launching TUI...")
PY

uv run python -m survey.tui
