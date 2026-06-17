from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from survey.api.app import app
from survey.db.models import Response
from survey.db.session import init_db
from survey.service.distributions import (
    read_grouped_distribution,
    read_overall_distribution,
    rebuild_distributions,
)


def _r(id: int, gender: str, q1: int, sentiment: str) -> Response:
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
        sentiment_label=sentiment,
    )


def _load_fixture(session: Session) -> None:
    # Female: q1 {5,4} sentiment {Positive,Negative}; Male: q1 {5,5} sentiment {Positive,Neutral}
    session.add_all(
        [
            _r(1, "Female", 5, "Positive"),
            _r(2, "Female", 4, "Negative"),
            _r(3, "Male", 5, "Positive"),
            _r(4, "Male", 5, "Neutral"),
        ]
    )
    rebuild_distributions(session)
    session.commit()


def test_overall_rating_and_sentiment_distributions(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load_fixture(session)
        q1 = read_overall_distribution(session, "q1_rating")
        sentiment = read_overall_distribution(session, "sentiment_label")
    assert q1.n == 4
    assert {b.response_value: b.count for b in q1.bins} == {"4": 1, "5": 3}
    assert sentiment.n == 4
    assert {b.response_value: b.count for b in sentiment.bins} == {
        "Positive": 2,
        "Negative": 1,
        "Neutral": 1,
    }


def test_grouped_rating_and_sentiment_by_gender(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load_fixture(session)
        q1_by_gender = read_grouped_distribution(session, "q1_rating", "gender")
        sentiment_by_gender = read_grouped_distribution(session, "sentiment_label", "gender")

    q1_groups = {
        g.group_value: ({b.response_value: b.count for b in g.bins}, g.n)
        for g in q1_by_gender.groups
    }
    assert q1_groups["Female"] == ({"4": 1, "5": 1}, 2)
    assert q1_groups["Male"] == ({"5": 2}, 2)

    sentiment_groups = {
        g.group_value: {b.response_value: b.count for b in g.bins}
        for g in sentiment_by_gender.groups
    }
    assert sentiment_groups["Female"] == {"Positive": 1, "Negative": 1}
    assert sentiment_groups["Male"] == {"Positive": 1, "Neutral": 1}


def test_distribution_endpoint_grouped_by_dimension(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        _load_fixture(session)
    with TestClient(app) as client:
        response = client.get("/distribution", params={"measure": "q1_rating", "by": "gender"})
    assert response.status_code == 200
    body = response.json()
    assert body["dimension"] == "gender"
    groups = {g["group_value"]: g for g in body["groups"]}
    assert {b["response_value"]: b["count"] for b in groups["Female"]["distribution"]} == {
        "4": 1,
        "5": 1,
    }
