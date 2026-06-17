"""The ingest validation contract and the pure row-cleaning transform.

Two failure modes, handled differently:
- A missing required *column* fails the whole ingest loudly (MissingColumnsError).
- A bad *row* is dropped and its reason counted (RowValidationError), never
  silently mangled. A row whose `id` repeats one already seen in the batch is a
  counted drop too (the first occurrence wins), so a duplicate primary key never
  reaches the database as an IntegrityError.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from survey.ingest.cleaning import derive_age_bucket, normalize_text

REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "age",
        "gender",
        "state",
        "city",
        "zip_code",
        "income",
        "education_level",
        "q1_rating",
        "q2_rating",
        "q4_rating",
        "sentiment_label",
    }
)

_RATING_MIN, _RATING_MAX = 1, 5
_AGE_MIN, _AGE_MAX = 18, 120


@dataclass(frozen=True)
class CleanedResponse:
    """A validated, normalized respondent row, ready to write to `responses`."""

    id: int
    age: int
    age_bucket: str
    gender: str
    state: str
    city: str
    zip_code: str
    income: str
    education_level: str
    q1_rating: int
    q2_rating: int
    q4_rating: int
    sentiment_label: str


@dataclass(frozen=True)
class IngestResult:
    """Outcome of cleaning a batch of raw rows."""

    cleaned: list[CleanedResponse]
    rows_ingested: int
    rows_dropped: int
    drop_reasons: dict[str, int]


class MissingColumnsError(ValueError):
    """Raised when the source is missing one or more required columns."""

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing = sorted(missing)
        super().__init__(f"Missing required columns: {self.missing}")


class RowValidationError(ValueError):
    """Raised for a single bad row; the reason is counted and the row dropped."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def validate_columns(columns: Iterable[str]) -> None:
    """Raise MissingColumnsError if any required column is absent."""
    missing = REQUIRED_COLUMNS - set(columns)
    if missing:
        raise MissingColumnsError(missing)


def _parse_int(raw: Mapping[str, str], field: str) -> int:
    value = (raw.get(field) or "").strip()
    try:
        return int(value)
    except ValueError:
        raise RowValidationError(f"{field}: not an integer ({value!r})") from None


def _parse_rating(raw: Mapping[str, str], field: str) -> int:
    rating = _parse_int(raw, field)
    if not (_RATING_MIN <= rating <= _RATING_MAX):
        raise RowValidationError(f"{field}: out of range (must be {_RATING_MIN}-{_RATING_MAX})")
    return rating


def _require_nonempty(raw: Mapping[str, str], field: str) -> str:
    value = normalize_text(raw.get(field) or "")
    if not value:
        raise RowValidationError(f"{field}: empty")
    return value


def clean_row(raw: Mapping[str, str]) -> CleanedResponse:
    """Validate and normalize one raw row, or raise RowValidationError."""
    id_ = _parse_int(raw, "id")
    age = _parse_int(raw, "age")
    if not (_AGE_MIN <= age <= _AGE_MAX):
        raise RowValidationError(f"age: out of range (must be {_AGE_MIN}-{_AGE_MAX})")
    q1 = _parse_rating(raw, "q1_rating")
    q2 = _parse_rating(raw, "q2_rating")
    q4 = _parse_rating(raw, "q4_rating")
    return CleanedResponse(
        id=id_,
        age=age,
        age_bucket=derive_age_bucket(age),
        gender=_require_nonempty(raw, "gender"),
        state=_require_nonempty(raw, "state"),
        city=normalize_text(raw.get("city") or ""),
        zip_code=normalize_text(raw.get("zip_code") or ""),
        income=_require_nonempty(raw, "income"),
        education_level=_require_nonempty(raw, "education_level"),
        q1_rating=q1,
        q2_rating=q2,
        q4_rating=q4,
        sentiment_label=_require_nonempty(raw, "sentiment_label"),
    )


_DUPLICATE_ID_REASON = "id: duplicate (kept first occurrence)"


def ingest_rows(raw_rows: Iterable[Mapping[str, str]]) -> IngestResult:
    """Clean each row; drop and count failures. Pure (no IO).

    A repeated `id` within the batch is a counted drop like any other bad row, so
    a duplicate respondent primary key never reaches the database.
    """
    cleaned: list[CleanedResponse] = []
    seen_ids: set[int] = set()
    drop_reasons: Counter[str] = Counter()
    for raw in raw_rows:
        try:
            row = clean_row(raw)
        except RowValidationError as exc:
            drop_reasons[exc.reason] += 1
            continue
        if row.id in seen_ids:
            drop_reasons[_DUPLICATE_ID_REASON] += 1
            continue
        seen_ids.add(row.id)
        cleaned.append(row)
    return IngestResult(
        cleaned=cleaned,
        rows_ingested=len(cleaned),
        rows_dropped=sum(drop_reasons.values()),
        drop_reasons=dict(drop_reasons),
    )
