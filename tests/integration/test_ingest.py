import csv
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from survey.db.models import Response
from survey.db.session import init_db
from survey.ingest.pipeline import ingest_responses
from survey.ingest.source import LocalDirectorySource

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "us_ai_survey_unique_50.csv"


def _count_responses(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Response)).scalar_one()


def test_ingest_sample_is_full_replace(clean_engine: Engine) -> None:
    init_db(clean_engine)
    with Session(clean_engine) as session:
        summary = ingest_responses(LocalDirectorySource(SAMPLE_CSV), session)
        session.commit()
        assert summary.rows_dropped == 0
        assert summary.rows_ingested == 50
        assert _count_responses(session) == summary.rows_ingested

    # Ingesting again replaces rather than appends.
    with Session(clean_engine) as session:
        ingest_responses(LocalDirectorySource(SAMPLE_CSV), session)
        session.commit()
        assert _count_responses(session) == 50


def test_ingest_drops_injected_malformed_row(clean_engine: Engine, tmp_path: Path) -> None:
    init_db(clean_engine)
    columns = [
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
    good = {
        "id": "1",
        "age": "34",
        "gender": "Female",
        "zip_code": "04225",
        "city": "LA",
        "state": "California",
        "income": "High",
        "education_level": "Bachelor’s Degree",
        "q1_rating": "4",
        "q2_rating": "5",
        "q3_open": "x",
        "q4_rating": "3",
        "q5_open": "y",
        "sentiment_label": "Positive",
    }
    bad = {**good, "id": "2", "age": "200"}  # age out of range -> dropped
    csv_path = tmp_path / "survey.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow(good)
        writer.writerow(bad)

    with Session(clean_engine) as session:
        summary = ingest_responses(LocalDirectorySource(csv_path), session)
        session.commit()
        assert summary.rows_ingested == 1
        assert summary.rows_dropped == 1
        assert _count_responses(session) == 1
