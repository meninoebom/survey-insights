import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.api.app import app
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.breakdown import (
    UnsupportedAggregationError,
    breakdown_average,
    breakdown_proportion,
    overall_average,
    overall_proportion,
)
from survey.service.distributions import rebuild_distributions


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


def _load(session: Session) -> None:
    # Female q1 {5,3}: avg 4.0, prop>=4 0.5, n=2
    # Male   q1 {5,4,2}: avg 11/3, prop>=4 2/3, n=3
    session.add_all(
        [
            _r(1, "Female", 5),
            _r(2, "Female", 3),
            _r(3, "Male", 5),
            _r(4, "Male", 4),
            _r(5, "Male", 2),
        ]
    )
    rebuild_distributions(session)
    session.commit()


def test_breakdown_average_matches_handcomputed_with_n(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = breakdown_average(session, "q1_rating", "gender")
    cells = {c.group_value: c for c in result.cells}
    assert cells["Female"].value == pytest.approx(4.0)
    assert cells["Female"].n == 2
    assert cells["Male"].value == pytest.approx(11 / 3)
    assert cells["Male"].n == 3


def test_breakdown_proportion_at_threshold(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = breakdown_proportion(session, "q1_rating", "gender", threshold=4)
    cells = {c.group_value: c for c in result.cells}
    assert cells["Female"].value == pytest.approx(0.5)
    assert cells["Female"].n == 2
    assert cells["Male"].value == pytest.approx(2 / 3)
    assert cells["Male"].n == 3


def test_overall_average_matches_handcomputed_with_n(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = overall_average(session, "q1_rating")
    # q1 across all five respondents {5,3,5,4,2}: mean 19/5, carrying n = 5.
    assert result.value == pytest.approx(19 / 5)
    assert result.n == 5


def test_overall_proportion_at_threshold(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = overall_proportion(session, "q1_rating", threshold=4)
    # Three of five respondents rate >= 4 ({5,5,4}).
    assert result.value == pytest.approx(3 / 5)
    assert result.n == 5


def test_overall_on_sentiment_is_rejected(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        with pytest.raises(UnsupportedAggregationError):
            overall_average(session, "sentiment_label")


def test_average_on_sentiment_is_rejected(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        with pytest.raises(UnsupportedAggregationError):
            breakdown_average(session, "sentiment_label", "gender")


def test_breakdown_endpoint_and_error_contract(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
    with TestClient(app) as client:
        ok = client.get(
            "/breakdown", params={"measure": "q1_rating", "by": "gender", "agg": "average"}
        )
        assert ok.status_code == 200
        body = ok.json()
        assert all("n" in cell for cell in body["breakdown"])  # n travels
        # The overall anchor (grand-total mean) travels with the breakdown, with its n.
        assert body["overall"]["n"] == 5
        assert body["overall"]["value"] == pytest.approx(19 / 5)

        sentiment = client.get(
            "/breakdown", params={"measure": "sentiment_label", "by": "gender", "agg": "average"}
        )
        assert sentiment.status_code == 400

        unknown_measure = client.get(
            "/breakdown", params={"measure": "nope", "by": "gender", "agg": "average"}
        )
        assert unknown_measure.status_code == 400
        assert "valid_measures" in unknown_measure.json()

        unknown_dimension = client.get(
            "/breakdown", params={"measure": "q1_rating", "by": "nope", "agg": "average"}
        )
        assert unknown_dimension.status_code == 400
        assert "valid_dimensions" in unknown_dimension.json()
