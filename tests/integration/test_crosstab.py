import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.api.app import app
from survey.config import get_settings
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.breakdown import UnsupportedAggregationError
from survey.service.crosstab import crosstab


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


def _load(session: Session) -> None:
    # Female/High: q1 {4,2} mean 3.0 n=2 ; Male/Low: q1 {5} mean 5.0 n=1
    # Female/Low and Male/High have no respondents -> empty corners.
    session.add_all(
        [_r(1, "Female", "High", 4), _r(2, "Female", "High", 2), _r(3, "Male", "Low", 5)]
    )
    session.commit()


def test_crosstab_sparse_corners(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = crosstab(session, "q1_rating", "gender", "income", min_reliable_n=2)
    assert result.agg == "average"
    assert result.threshold is None
    cells = {(c.row_value, c.col_value): c for c in result.cells}

    assert cells[("Female", "High")].status == "populated"
    assert cells[("Female", "High")].value == pytest.approx(3.0)
    assert cells[("Female", "High")].n == 2

    assert cells[("Male", "Low")].status == "low_n"
    assert cells[("Male", "Low")].value == pytest.approx(5.0)
    assert cells[("Male", "Low")].n == 1
    assert cells[("Male", "Low")].reliability == "low"

    for empty in (("Female", "Low"), ("Male", "High")):
        assert cells[empty].status == "empty"
        assert cells[empty].value is None  # never rendered as 0
        assert cells[empty].n == 0


def test_crosstab_rejects_sentiment_measure(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        with pytest.raises(UnsupportedAggregationError):
            crosstab(session, "sentiment_label", "gender", "income", min_reliable_n=2)


def test_crosstab_endpoint(clean_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    init_db(clean_engine)
    monkeypatch.setenv("MIN_RELIABLE_N", "2")
    get_settings.cache_clear()
    try:
        with Session(clean_engine) as session:
            _load(session)
        with TestClient(app) as client:
            response = client.get(
                "/crosstab", params={"measure": "q1_rating", "row": "gender", "col": "income"}
            )
            assert response.status_code == 200
            cells = {(c["row_value"], c["col_value"]): c for c in response.json()["cells"]}
            assert cells[("Female", "High")]["status"] == "populated"
            assert cells[("Female", "Low")]["status"] == "empty"
            assert cells[("Female", "Low")]["value"] is None

            rejected = client.get(
                "/crosstab",
                params={"measure": "sentiment_label", "row": "gender", "col": "income"},
            )
            assert rejected.status_code == 400
    finally:
        get_settings.cache_clear()


def test_crosstab_endpoint_rejects_unknown_dimension(
    clean_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The headline endpoint resolves row/col through the allowlist before any SQL is
    # built, so an excluded dimension ("city") or an injection string is a clean 400
    # listing the valid dimensions, never a 500 or a query (Invariant 3).
    init_db(clean_engine)
    monkeypatch.setenv("MIN_RELIABLE_N", "2")
    get_settings.cache_clear()
    try:
        with Session(clean_engine) as session:
            _load(session)
        with TestClient(app) as client:
            excluded = client.get(
                "/crosstab",
                params={"measure": "q1_rating", "row": "city", "col": "gender"},
            )
            assert excluded.status_code == 400
            assert "valid_dimensions" in excluded.json()

            injection = client.get(
                "/crosstab",
                params={
                    "measure": "q1_rating",
                    "row": "gender; DROP TABLE responses",
                    "col": "gender",
                },
            )
            assert injection.status_code == 400
    finally:
        get_settings.cache_clear()


def test_crosstab_proportion(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load(session)
        result = crosstab(
            session,
            "q1_rating",
            "gender",
            "income",
            min_reliable_n=2,
            agg="proportion",
            threshold=4,
        )
    assert result.agg == "proportion"
    assert result.threshold == 4
    cells = {(c.row_value, c.col_value): c for c in result.cells}
    assert cells[("Female", "High")].value == pytest.approx(0.5)  # 1 of {4, 2} is >= 4
    assert cells[("Male", "Low")].value == pytest.approx(1.0)  # 1 of {5} is >= 4
    assert cells[("Female", "Low")].status == "empty"
    assert cells[("Female", "Low")].value is None


def test_crosstab_endpoint_proportion(
    clean_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_db(clean_engine)
    monkeypatch.setenv("MIN_RELIABLE_N", "2")
    get_settings.cache_clear()
    try:
        with Session(clean_engine) as session:
            _load(session)
        with TestClient(app) as client:
            response = client.get(
                "/crosstab",
                params={
                    "measure": "q1_rating",
                    "row": "gender",
                    "col": "income",
                    "agg": "proportion",
                    "threshold": 4,
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["agg"] == "proportion"
        assert body["threshold"] == 4
        cells = {(c["row_value"], c["col_value"]): c for c in body["cells"]}
        assert cells[("Female", "High")]["value"] == pytest.approx(0.5)
    finally:
        get_settings.cache_clear()
