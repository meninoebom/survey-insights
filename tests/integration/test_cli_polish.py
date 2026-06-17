import csv
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.cli.main import main
from survey.config import get_settings
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.distributions import rebuild_distributions

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


def _r(id: int, gender: str, q1: int) -> Response:
    return Response(
        id=id,
        age=40,
        age_bucket="30-44",
        gender=gender,
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


def test_cli_breakdown_average_shows_value_and_n(
    clean_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        session.add_all([_r(1, "Female", 5), _r(2, "Female", 3), _r(3, "Male", 4)])
        rebuild_distributions(session)
        session.commit()
    main(["breakdown", "--measure", "q1_rating", "--by", "gender", "--agg", "average"])
    out = capsys.readouterr().out
    assert "Breakdown of q1_rating by gender (average)" in out
    assert "value" in out and "n" in out
    assert "Female" in out and "4.00" in out  # (5 + 3) / 2


def test_cli_refresh_prints_summary(
    clean_engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db(clean_engine)
    csv_path = tmp_path / "s.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "id": "1",
                "age": "40",
                "gender": "Female",
                "zip_code": "04225",
                "city": "LA",
                "state": "California",
                "income": "High",
                "education_level": "Bachelor's Degree",
                "q1_rating": "5",
                "q2_rating": "3",
                "q3_open": "",
                "q4_rating": "3",
                "q5_open": "",
                "sentiment_label": "Positive",
            }
        )
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        main(["refresh"])
        out = capsys.readouterr().out
        assert "Refresh complete" in out
        assert "rows_ingested:   1" in out
        assert "responses, distributions" in out
    finally:
        get_settings.cache_clear()
