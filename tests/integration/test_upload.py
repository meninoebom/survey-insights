"""Guardrails for uploading a CSV by contents (drag-and-drop) and reset-to-sample.

These protect the load-bearing properties of the upload path:
- An upload of known bytes fully replaces the data; the new distributions reflect
  it and the summary is correct (newest wins).
- It is atomic: a bad upload (missing a required column) raises and leaves the
  prior data fully intact, and never writes a file into the upload area.
- On success the uploaded bytes are promoted into the upload area as the new newest
  file, so a subsequent refresh re-ingests the just-uploaded data (the directory and
  the DB agree).
- The live source is structural: the newest upload if any, otherwise the bundled
  sample fallback. Reset-to-sample discards the uploads so the fallback takes over.

The upload writes file *contents* the server chooses where to place; it never reads
a caller-named path (constraint 7). That guard is exercised at the API layer in
test_ingest_api.py; here we pin the service-level transactional behavior.
"""

import csv
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from survey.db.models import Response
from survey.db.session import init_db
from survey.ingest.source import LocalDirectorySource
from survey.ingest.validation import MissingColumnsError
from survey.service.refresh import refresh
from survey.service.upload import current_origin, ingest_upload, reset_to_sample, resolve_source

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
SAMPLE_CSV = Path(__file__).resolve().parents[2] / "us_ai_survey_unique_50.csv"


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


def _csv_bytes(rows: list[dict[str, str]], columns: list[str] = COLUMNS) -> bytes:
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue().encode("utf-8")


def _count_responses(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Response)).scalar_one()


def _q1_values(session: Session) -> list[int]:
    return sorted(session.execute(select(Response.q1_rating)).scalars().all())


def test_upload_replaces_data_and_returns_summary(clean_engine: Engine, tmp_path: Path) -> None:
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    summary = ingest_upload(_csv_bytes([_row(1, 5), _row(2, 4)]), source_dir, factory)

    assert summary.rows_ingested == 2
    assert summary.rows_dropped == 0
    assert summary.tables_rebuilt == ["responses", "distributions"]
    assert summary.duration_ms >= 0
    with factory() as session:
        assert _count_responses(session) == 2
        assert _q1_values(session) == [4, 5]


def test_upload_promotes_contents_so_refresh_sees_them(
    clean_engine: Engine, tmp_path: Path
) -> None:
    # After a successful upload the bytes become the newest file in the source
    # directory, so a plain refresh of that directory re-ingests them (the
    # directory and the DB stay consistent). This is the newest-wins contract.
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    ingest_upload(_csv_bytes([_row(1, 2), _row(2, 2), _row(3, 2)]), source_dir, factory)
    written = list(source_dir.glob("*.csv"))
    assert len(written) == 1  # exactly one promoted file

    refresh(LocalDirectorySource(source_dir), factory)
    with factory() as session:
        assert _count_responses(session) == 3
        assert _q1_values(session) == [2, 2, 2]


def test_bad_upload_is_atomic_and_leaves_prior_data_intact(
    clean_engine: Engine, tmp_path: Path
) -> None:
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Establish known-good data.
    ingest_upload(_csv_bytes([_row(1, 5), _row(2, 5)]), source_dir, factory)
    good_files = {p.name for p in source_dir.glob("*.csv")}

    # A CSV missing a required column (no sentiment_label) must be rejected.
    bad_columns = [c for c in COLUMNS if c != "sentiment_label"]
    bad_bytes = _csv_bytes([_row(9, 1)], columns=bad_columns)
    with pytest.raises(MissingColumnsError):
        ingest_upload(bad_bytes, source_dir, factory)

    # The prior data is fully intact: counts and values unchanged.
    with factory() as session:
        assert _count_responses(session) == 2
        assert _q1_values(session) == [5, 5]
    # And no new file was promoted into the source directory.
    assert {p.name for p in source_dir.glob("*.csv")} == good_files


def test_reset_discards_uploads_and_falls_back_to_sample(
    clean_engine: Engine, tmp_path: Path
) -> None:
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Start from a tiny uploaded dataset, then reset.
    ingest_upload(_csv_bytes([_row(1, 5)]), source_dir, factory)
    assert list(source_dir.glob("*.csv"))  # an upload is present
    with factory() as session:
        assert _count_responses(session) == 1

    summary = reset_to_sample(SAMPLE_CSV, source_dir, factory)

    assert summary.rows_ingested == 50
    with factory() as session:
        assert _count_responses(session) == 50
    # The upload is discarded, so the directory is empty and the live source resolves
    # to the bundled sample: a refresh of the resolved source keeps 50 rows.
    assert list(source_dir.glob("*.csv")) == []
    resolved = resolve_source(source_dir, SAMPLE_CSV)
    assert resolved is not None
    refresh(resolved, factory)
    with factory() as session:
        assert _count_responses(session) == 50


def test_origin_is_structural(clean_engine: Engine, tmp_path: Path) -> None:
    # "sample" vs "upload" is derived from whether an upload exists, not a stored flag.
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    assert current_origin(source_dir) == "sample"  # empty upload area
    ingest_upload(_csv_bytes([_row(1, 5)]), source_dir, factory)
    assert current_origin(source_dir) == "upload"  # an upload is present
    reset_to_sample(SAMPLE_CSV, source_dir, factory)
    assert current_origin(source_dir) == "sample"  # uploads discarded


def test_resolve_source_prefers_upload_then_sample_then_none(
    clean_engine: Engine, tmp_path: Path
) -> None:
    init_db(clean_engine)
    factory = sessionmaker(bind=clean_engine, expire_on_commit=False)
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # No upload: resolves to the bundled sample (50 rows).
    resolved = resolve_source(source_dir, SAMPLE_CSV)
    assert resolved is not None
    refresh(resolved, factory)
    with factory() as session:
        assert _count_responses(session) == 50

    # With an upload present, resolves to the upload (newest wins).
    ingest_upload(_csv_bytes([_row(1, 2), _row(2, 2)]), source_dir, factory)
    resolved = resolve_source(source_dir, SAMPLE_CSV)
    assert resolved is not None
    refresh(resolved, factory)
    with factory() as session:
        assert _count_responses(session) == 2

    # Neither an upload nor a sample: nothing to resolve.
    for csv_file in source_dir.glob("*.csv"):
        csv_file.unlink()
    assert resolve_source(source_dir, None) is None
