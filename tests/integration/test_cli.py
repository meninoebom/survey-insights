import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.cli.main import main
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.distributions import rebuild_distributions


def test_cli_distribution_prints_counts(
    clean_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        session.add(
            Response(
                id=1,
                age=40,
                age_bucket="30-44",
                gender="Female",
                state="California",
                city="LA",
                zip_code="04225",
                income="High",
                education_level="Bachelor's Degree",
                q1_rating=5,
                q2_rating=3,
                q4_rating=3,
                sentiment_label="Positive",
            )
        )
        rebuild_distributions(session)
        session.commit()

    main(["distribution", "--measure", "q1_rating"])
    out = capsys.readouterr().out
    assert "Distribution of q1_rating (overall, n=1)" in out
    # The polished output is an aligned table: a row with response_value 5, count 1.
    assert any(line.split() == ["5", "1"] for line in out.splitlines())
