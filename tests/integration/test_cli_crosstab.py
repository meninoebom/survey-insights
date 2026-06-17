import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.cli.main import main
from survey.config import get_settings
from survey.db.models import Response
from survey.db.session import init_db


def _r(id: int, gender: str, income: str, q1: int) -> Response:
    return Response(
        id=id,
        age=40,
        age_bucket="30-44",
        gender=gender,
        state="California",
        city="LA",
        zip_code="04225",
        income=income,
        education_level="Bachelor's Degree",
        q1_rating=q1,
        q2_rating=3,
        q4_rating=3,
        sentiment_label="Positive",
    )


def test_cli_crosstab_renders_grid(
    clean_engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MIN_RELIABLE_N", "2")
    get_settings.cache_clear()
    try:
        init_db(clean_engine)
        with Session(clean_engine) as session:
            # Female/High q1 {4,2} n=2 populated; Male/Low q1 {5} n=1 low_n;
            # Female/Low and Male/High empty.
            session.add_all(
                [_r(1, "Female", "High", 4), _r(2, "Female", "High", 2), _r(3, "Male", "Low", 5)]
            )
            session.commit()
        main(["crosstab", "--measure", "q1_rating", "--row", "gender", "--col", "income"])
        out = capsys.readouterr().out
        assert "q1_rating" in out
        assert "n/a" in out  # empty corners rendered distinctly, never as 0
        assert "(low)" in out  # the n=1 cell is flagged
        assert "[n=2]" in out  # the populated cell shows its n
    finally:
        get_settings.cache_clear()


def test_cli_crosstab_proportion(
    clean_engine: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MIN_RELIABLE_N", "2")
    get_settings.cache_clear()
    try:
        init_db(clean_engine)
        with Session(clean_engine) as session:
            session.add_all(
                [_r(1, "Female", "High", 4), _r(2, "Female", "High", 2), _r(3, "Male", "Low", 5)]
            )
            session.commit()
        main(
            [
                "crosstab",
                "--measure",
                "q1_rating",
                "--row",
                "gender",
                "--col",
                "income",
                "--agg",
                "proportion",
                "--threshold",
                "4",
            ]
        )
        out = capsys.readouterr().out
        assert "proportion >= 4" in out  # header reflects the aggregation
        assert "0.50" in out  # Female/High: 1 of 2 >= 4
    finally:
        get_settings.cache_clear()
