import pytest

from survey.ingest.cleaning import derive_age_bucket, normalize_text
from survey.ingest.validation import (
    MissingColumnsError,
    clean_row,
    ingest_rows,
    validate_columns,
)


@pytest.mark.parametrize(
    ("age", "bucket"),
    [
        (18, "18-29"),
        (29, "18-29"),
        (30, "30-44"),
        (44, "30-44"),
        (45, "45-59"),
        (59, "45-59"),
        (60, "60+"),
        (95, "60+"),
    ],
)
def test_age_bucket_boundaries(age: int, bucket: str) -> None:
    assert derive_age_bucket(age) == bucket


def test_normalize_text_canonicalizes_curly_apostrophe_and_whitespace() -> None:
    raw = "Bachelor’s   Degree "  # curly apostrophe + extra/trailing whitespace
    assert normalize_text(raw) == "Bachelor's Degree"


def _good_row(**overrides: str) -> dict[str, str]:
    row = {
        "id": "1",
        "age": "34",
        "gender": "Female",
        "state": "California",
        "city": "Los Angeles",
        "zip_code": "04225",
        "income": "Upper-Middle",
        "education_level": "Bachelor’s Degree",
        "q1_rating": "4",
        "q2_rating": "5",
        "q4_rating": "3",
        "sentiment_label": "Positive",
    }
    row.update(overrides)
    return row


def test_clean_row_normalizes_and_derives() -> None:
    cleaned = clean_row(_good_row())
    assert cleaned.age_bucket == "30-44"
    assert cleaned.education_level == "Bachelor's Degree"  # curly -> ASCII
    assert cleaned.zip_code == "04225"  # leading zero preserved (text)
    assert cleaned.q1_rating == 4


def test_ingest_drops_and_counts_one_bad_row() -> None:
    rows = [_good_row(id="1"), _good_row(id="2", age="200"), _good_row(id="3")]
    result = ingest_rows(rows)
    assert result.rows_ingested == 2
    assert result.rows_dropped == 1
    assert sum(result.drop_reasons.values()) == 1
    assert any("age" in reason for reason in result.drop_reasons)


def test_ingest_drops_rating_out_of_range_and_empty_required() -> None:
    rows = [_good_row(q1_rating="9"), _good_row(gender="   ")]
    result = ingest_rows(rows)
    assert result.rows_ingested == 0
    assert result.rows_dropped == 2


def test_validate_columns_missing_required_fails_loudly() -> None:
    present = set(_good_row().keys())
    validate_columns(present)  # all present: no raise
    with pytest.raises(MissingColumnsError) as exc:
        validate_columns(present - {"state"})
    assert "state" in exc.value.missing
