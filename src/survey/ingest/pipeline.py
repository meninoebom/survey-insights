"""Ingest orchestration: read via a DataSource, validate columns, clean rows, and
full-replace `responses` (the rebuildable source of truth).

Distributions are rebuilt from `responses` by the aggregation layer; this module
owns only the detail-row write. The caller controls the transaction (the
transactional refresh wraps responses + distributions together).
"""

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from survey.db.models import Response
from survey.ingest.source import DataSource
from survey.ingest.validation import CleanedResponse, ingest_rows, validate_columns


@dataclass(frozen=True)
class IngestSummary:
    """Outcome of an ingest into `responses`."""

    files_processed: int
    rows_ingested: int
    rows_dropped: int
    drop_reasons: dict[str, int]


def replace_responses(session: Session, cleaned: list[CleanedResponse]) -> None:
    """Full-replace `responses`: delete all rows, then insert the cleaned set."""
    session.execute(delete(Response))
    session.add_all(
        Response(
            id=row.id,
            age=row.age,
            age_bucket=row.age_bucket,
            gender=row.gender,
            state=row.state,
            city=row.city,
            zip_code=row.zip_code,
            income=row.income,
            education_level=row.education_level,
            q1_rating=row.q1_rating,
            q2_rating=row.q2_rating,
            q4_rating=row.q4_rating,
            sentiment_label=row.sentiment_label,
        )
        for row in cleaned
    )


def ingest_responses(source: DataSource, session: Session) -> IngestSummary:
    """Read, validate columns, clean, and full-replace `responses`. Caller commits."""
    raw = source.read()
    validate_columns(raw.columns)
    result = ingest_rows(raw.rows)
    replace_responses(session, result.cleaned)
    return IngestSummary(
        files_processed=raw.files_processed,
        rows_ingested=result.rows_ingested,
        rows_dropped=result.rows_dropped,
        drop_reasons=result.drop_reasons,
    )
