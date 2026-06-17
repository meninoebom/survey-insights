import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from survey.api.app import app
from survey.config import get_settings
from survey.db.models import Response
from survey.db.session import init_db
from survey.ingest.source import LocalDirectorySource
from survey.service.refresh import refresh

COLUMNS = [
    "id",
    "age",
    "gender",
    "zip_code",
    "city",
    "state",
    "income",
    "education_level",
    "q1_rating",
    "q2_rating",
    "q3_open",
    "q4_rating",
    "q5_open",
    "sentiment_label",
]


def _row(id: int, q1: int) -> dict[str, str]:
    return {
        "id": str(id),
        "age": "40",
        "gender": "Female",
        "zip_code": "04225",
        "city": "LA",
        "state": "California",
        "income": "High",
        "education_level": "Bachelor's Degree",
        "q1_rating": str(q1),
        "q2_rating": "3",
        "q3_open": "",
        "q4_rating": "3",
        "q5_open": "",
        "sentiment_label": "Positive",
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _count_responses(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Response)).scalar_one()


def _snapshot_distributions(session: Session) -> list[tuple[object, ...]]:
    rows = session.execute(
        text(
            "SELECT measure, dimension, group_value, response_value, count FROM distributions "
            "ORDER BY measure, dimension, group_value, response_value"
        )
    ).all()
    return [tuple(row) for row in rows]


def test_refresh_reflects_swapped_source_and_is_idempotent(
    clean_engine: Engine, tmp_path: Path
) -> None:
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)

    v1 = tmp_path / "v1.csv"
    _write_csv(v1, [_row(1, 5), _row(2, 5)])  # 2 rows, q1 all 5
    v2 = tmp_path / "v2.csv"
    _write_csv(v2, [_row(1, 1), _row(2, 1), _row(3, 1)])  # 3 rows, q1 all 1

    summary_v1 = refresh(LocalDirectorySource(v1), factory)
    assert summary_v1.rows_ingested == 2
    assert summary_v1.tables_rebuilt == ["responses", "distributions"]
    assert summary_v1.duration_ms >= 0
    with factory() as session:
        assert _count_responses(session) == 2

    # Swap the source: aggregates reflect the new data.
    summary_v2 = refresh(LocalDirectorySource(v2), factory)
    assert summary_v2.rows_ingested == 3
    with factory() as session:
        assert _count_responses(session) == 3
        snapshot_after_v2 = _snapshot_distributions(session)

    # Idempotent: refreshing the same data again yields identical tables.
    refresh(LocalDirectorySource(v2), factory)
    with factory() as session:
        assert _count_responses(session) == 3
        assert _snapshot_distributions(session) == snapshot_after_v2


def test_refresh_endpoint_returns_summary(
    clean_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_db(clean_engine)
    csv_path = tmp_path / "s.csv"
    _write_csv(csv_path, [_row(1, 5), _row(2, 4)])
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
    monkeypatch.delenv("BUNDLED_SAMPLE", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.post("/refresh")
        assert response.status_code == 200
        body = response.json()
        assert body["rows_ingested"] == 2
        assert body["rows_dropped"] == 0
        assert body["tables_rebuilt"] == ["responses", "distributions"]
        assert body["duration_ms"] >= 0
        assert "drop_reasons" in body
    finally:
        get_settings.cache_clear()
