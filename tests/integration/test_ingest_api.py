"""Guardrails for the upload endpoints (`POST /ingest`, the reset, `POST /refresh`).

The browser uploads a CSV as the raw request body (no multipart dependency). The
server writes those contents into the configured source directory; it never reads
a caller-named path. These tests pin:
- a known small CSV body replaces the data and returns the correct summary;
- an empty body is 400 and an oversize body is 413;
- a bad CSV (missing a required column) is a clean 4xx, not a 500, with the old
  data intact (atomicity surfaced through the API);
- contents-not-paths: posting a filesystem path string as the body is treated as
  CSV content and fails column validation; the path is never read (constraint 7);
- reset-to-sample restores the 50-row sample.

The app ingests the bundled sample fallback on boot (the upload directory is empty),
so the TestClient comes up populated with the sample, exactly like `docker compose up`.
"""

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from survey.api.app import app
from survey.config import get_settings
from survey.db.session import init_db

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "us_ai_survey_unique_50.csv"
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
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue().encode("utf-8")


@pytest.fixture
def client(clean_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    init_db(clean_engine)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    monkeypatch.setenv("SOURCE_DIR", str(source_dir))
    monkeypatch.setenv("BUNDLED_SAMPLE", str(SAMPLE_CSV))
    monkeypatch.delenv("INITIAL_CSV", raising=False)
    get_settings.cache_clear()
    return TestClient(app)


def _overall_n(client: TestClient, measure: str = "q1_rating") -> int:
    body = client.get(f"/distribution?measure={measure}").json()
    return int(body["n"])


def test_ingest_replaces_data_and_returns_summary(client: TestClient) -> None:
    try:
        with client:
            assert _overall_n(client) == 50  # seeded from the bundled sample on boot

            body = _csv_bytes([_row(1, 5), _row(2, 4), _row(3, 4)])
            resp = client.post("/ingest", content=body)
            assert resp.status_code == 200
            summary = resp.json()
            assert summary["rows_ingested"] == 3
            assert summary["rows_dropped"] == 0
            assert summary["tables_rebuilt"] == ["responses", "distributions"]
            assert "duration_ms" in summary

            assert _overall_n(client) == 3  # newest wins: the upload replaced the sample
    finally:
        get_settings.cache_clear()


def test_empty_body_is_rejected(client: TestClient) -> None:
    try:
        with client:
            resp = client.post("/ingest", content=b"")
            assert resp.status_code == 400
            assert _overall_n(client) == 50  # unchanged
    finally:
        get_settings.cache_clear()


def test_oversize_body_is_rejected(client: TestClient) -> None:
    try:
        with client:
            # Declare an oversize Content-Length; the cap rejects before reading a body.
            resp = client.post(
                "/ingest",
                content=b"x",
                headers={"Content-Length": str(50 * 1024 * 1024)},
            )
            assert resp.status_code == 413
            assert _overall_n(client) == 50  # unchanged
    finally:
        get_settings.cache_clear()


def test_bad_csv_is_4xx_and_leaves_data_intact(client: TestClient) -> None:
    try:
        with client:
            assert _overall_n(client) == 50
            bad_columns = [c for c in COLUMNS if c != "sentiment_label"]
            resp = client.post("/ingest", content=_csv_bytes([_row(1, 1)], columns=bad_columns))
            assert 400 <= resp.status_code < 500  # a clean client error, never a 500
            assert _overall_n(client) == 50  # the seeded data is untouched
    finally:
        get_settings.cache_clear()


def test_posting_a_path_string_is_treated_as_contents_not_a_path(client: TestClient) -> None:
    # Constraint 7: a request carries contents, never a server path. Posting a
    # filesystem path as the body is just (invalid) CSV text; the server never
    # opens that path. It fails column validation and the data stays intact.
    try:
        with client:
            resp = client.post("/ingest", content=b"/etc/passwd")
            assert 400 <= resp.status_code < 500
            assert _overall_n(client) == 50
    finally:
        get_settings.cache_clear()


def test_reset_to_sample_restores_the_sample(client: TestClient) -> None:
    try:
        with client:
            client.post("/ingest", content=_csv_bytes([_row(1, 5)]))
            assert _overall_n(client) == 1

            resp = client.post("/ingest/sample")
            assert resp.status_code == 200
            assert resp.json()["rows_ingested"] == 50
            assert _overall_n(client) == 50
    finally:
        get_settings.cache_clear()


def test_meta_reports_the_live_source(client: TestClient) -> None:
    # The UI reads which dataset is live from /meta, so a page reload shows the truth
    # (a persisted upload stays "upload"; the boot fallback is "sample").
    try:
        with client:
            assert client.get("/meta").json()["source"] == "sample"  # boot fallback
            client.post("/ingest", content=_csv_bytes([_row(1, 5)]))
            assert client.get("/meta").json()["source"] == "upload"
            client.post("/ingest/sample")
            assert client.get("/meta").json()["source"] == "sample"
    finally:
        get_settings.cache_clear()


def test_duplicate_id_in_upload_is_dropped_and_counted(client: TestClient) -> None:
    # A repeated id within one CSV is a counted drop (first occurrence wins), not a
    # 500 from a primary-key collision. The prior data is still cleanly replaced.
    try:
        with client:
            body = _csv_bytes([_row(1, 5), _row(1, 3), _row(2, 4)])
            resp = client.post("/ingest", content=body)
            assert resp.status_code == 200
            summary = resp.json()
            assert summary["rows_ingested"] == 2  # ids 1 and 2; the second id=1 is dropped
            assert summary["rows_dropped"] == 1
            assert any("duplicate" in reason for reason in summary["drop_reasons"])
            assert _overall_n(client) == 2

            # First occurrence won: id=1 kept q1=5, so the q1 distribution has a 5, not a 3.
            dist = client.get("/distribution?measure=q1_rating").json()
            counts = {b["response_value"]: b["count"] for b in dist["distribution"]}
            assert counts.get("5") == 1
            assert "3" not in counts
    finally:
        get_settings.cache_clear()
