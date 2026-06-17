"""The fixed allowlist mapping request-supplied measure/dimension names to real
column identifiers.

Resolving names here, before any SQL is built, is the SQL-injection guard
(Invariant 3): a name that is not in these maps never reaches a query. The
indirection is the guard even though the names equal the columns today.
"""

from typing import Final

# Sentinel rows in `distributions` that hold the ungrouped ("overall") aggregate.
OVERALL_DIMENSION: Final[str] = "__overall__"
OVERALL_GROUP_VALUE: Final[str] = "all"

# measure name -> column identifier in `responses`.
MEASURE_COLUMNS: Final[dict[str, str]] = {
    "q1_rating": "q1_rating",
    "q2_rating": "q2_rating",
    "q4_rating": "q4_rating",
    "sentiment_label": "sentiment_label",
}

# Numeric measures additionally support average and proportion; sentiment is
# distribution-only.
NUMERIC_MEASURES: Final[frozenset[str]] = frozenset({"q1_rating", "q2_rating", "q4_rating"})

# dimension name -> column identifier in `responses`.
# `city` and `zip_code` are intentionally absent: too high-cardinality to form a
# meaningful breakdown unit.
DIMENSION_COLUMNS: Final[dict[str, str]] = {
    "state": "state",
    "gender": "gender",
    "education_level": "education_level",
    "income": "income",
    "age_bucket": "age_bucket",
}


class UnknownMeasureError(ValueError):
    """Raised when a requested measure is not on the allowlist."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.valid_measures = sorted(MEASURE_COLUMNS)
        super().__init__(f"Unknown measure {name!r}. Valid measures: {self.valid_measures}.")


class UnknownDimensionError(ValueError):
    """Raised when a requested dimension is not on the allowlist."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.valid_dimensions = sorted(DIMENSION_COLUMNS)
        super().__init__(f"Unknown dimension {name!r}. Valid dimensions: {self.valid_dimensions}.")


def resolve_measure(name: str) -> str:
    """Return the column identifier for an allowlisted measure, else raise."""
    try:
        return MEASURE_COLUMNS[name]
    except KeyError:
        raise UnknownMeasureError(name) from None


def resolve_dimension(name: str) -> str:
    """Return the column identifier for an allowlisted dimension, else raise."""
    try:
        return DIMENSION_COLUMNS[name]
    except KeyError:
        raise UnknownDimensionError(name) from None


def is_numeric_measure(name: str) -> bool:
    """True for measures that support average/proportion (not sentiment)."""
    return name in NUMERIC_MEASURES
