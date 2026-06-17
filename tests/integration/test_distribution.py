from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.api.app import app
from survey.config import get_settings
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.distributions import read_overall_distribution, rebuild_distributions

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "us_ai_survey_unique_50.csv"


def _response(id: int, q1: int) -> Response:
    return Response(
        id=id,
        age=40,
        age_bucket="30-44",
        gender="Female",
        state="California",
        city="LA",
        zip_code="04225",
        income="High",
        education_level="Bachelor's Degree",
        q1_rating=q1,
        q2_rating=3,
        q4_rating=3,
        sentiment_label="Positive",
    )


def test_rebuild_and_read_overall_distribution(clean_engine: Engine) -> None:
    init_db(clean_engine)
    # q1 values 5,5,5,4,1 -> {1:1, 4:1, 5:3}, n=5
    with Session(clean_engine) as session:
        session.add_all(
            [_response(1, 5), _response(2, 5), _response(3, 5), _response(4, 4), _response(5, 1)]
        )
        rebuild_distributions(session)
        session.commit()
        result = read_overall_distribution(session, "q1_rating")
    assert result.n == 5
    assert {b.response_value: b.count for b in result.bins} == {"1": 1, "4": 1, "5": 3}


def test_distribution_endpoint_returns_overall(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        session.add_all([_response(1, 5), _response(2, 4), _response(3, 4)])
        rebuild_distributions(session)
        session.commit()
    with TestClient(app) as client:
        response = client.get("/distribution", params={"measure": "q1_rating"})
    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 3
    assert {b["response_value"]: b["count"] for b in body["distribution"]} == {"4": 2, "5": 1}


def test_distribution_endpoint_rejects_unknown_measure(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with TestClient(app) as client:
        response = client.get("/distribution", params={"measure": "haxx"})
    assert response.status_code == 400
    assert "valid_measures" in response.json()


def test_boot_falls_back_to_bundled_sample(
    clean_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty upload directory plus a BUNDLED_SAMPLE: boot ingests the sample
    # fallback, exactly like a cold `docker compose up`. The sample is the fallback,
    # never copied into the upload area, so the directory stays empty.
    init_db(clean_engine)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    monkeypatch.setenv("SOURCE_DIR", str(source_dir))
    monkeypatch.setenv("BUNDLED_SAMPLE", str(SAMPLE_CSV))
    monkeypatch.delenv("INITIAL_CSV", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.get("/distribution", params={"measure": "q1_rating"})
        assert response.status_code == 200
        assert response.json()["n"] == 50
        assert list(source_dir.glob("*.csv")) == []  # sample is the fallback, not seeded in
    finally:
        get_settings.cache_clear()
