from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from survey.api.app import app


def test_health_ok_and_boot_creates_empty_tables(clean_engine: Engine) -> None:
    # clean_engine points DATABASE_URL at a disposable Postgres with an empty
    # schema; entering the TestClient runs the lifespan, which calls init_db.
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        with clean_engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM responses")).scalar_one()
        assert count == 0  # boot creates the tables empty (no ingest yet)
