import pytest

from survey.allowlist import (
    DIMENSION_COLUMNS,
    MEASURE_COLUMNS,
    UnknownDimensionError,
    UnknownMeasureError,
    is_numeric_measure,
    resolve_dimension,
    resolve_measure,
)


def test_resolve_known_measure_returns_column() -> None:
    assert resolve_measure("q1_rating") == "q1_rating"
    assert resolve_measure("sentiment_label") == "sentiment_label"


def test_injection_string_is_rejected_not_passed_through() -> None:
    # The guard: a hostile string raises instead of reaching a query.
    with pytest.raises(UnknownMeasureError) as exc:
        resolve_measure("q1_rating; DROP TABLE responses")
    assert exc.value.valid_measures == sorted(MEASURE_COLUMNS)


def test_resolve_known_dimension_returns_column() -> None:
    assert resolve_dimension("education_level") == "education_level"


def test_excluded_dimension_is_rejected() -> None:
    # `city` is intentionally not a dimension (too high-cardinality).
    with pytest.raises(UnknownDimensionError) as exc:
        resolve_dimension("city")
    assert exc.value.valid_dimensions == sorted(DIMENSION_COLUMNS)


def test_numeric_vs_sentiment_measure() -> None:
    assert is_numeric_measure("q1_rating") is True
    assert is_numeric_measure("sentiment_label") is False
