"""One-dimensional breakdowns (average, proportion) derived from `distributions`.

Both the value and its respondent count `n` come from the same SUM over the
stored distribution, so `n` always travels with the statistic (Invariant 2) and
there is one definition of each number (Invariant 1). Numeric measures only;
sentiment is distribution-only.

Names are bound parameters, never interpolated, so nothing caller-supplied
reaches SQL as an identifier (Invariant 3).
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from survey.allowlist import (
    OVERALL_DIMENSION,
    is_numeric_measure,
    resolve_dimension,
    resolve_measure,
)


class UnsupportedAggregationError(ValueError):
    """Raised when average/proportion is requested for a non-numeric measure."""

    def __init__(self, measure: str, agg: str) -> None:
        self.measure = measure
        self.agg = agg
        super().__init__(
            f"Aggregation {agg!r} is not supported for measure {measure!r} "
            "(numeric measures only; sentiment is distribution-only)."
        )


@dataclass(frozen=True)
class BreakdownCell:
    """One group's derived value and its respondent count."""

    group_value: str
    value: float
    n: int


@dataclass(frozen=True)
class OverallValue:
    """The ungrouped aggregate of a measure across all respondents, with its n.

    The anchor for a breakdown (the "Overall" row and reference line) and the
    crosstab grand-total corner. Derived from the same `__overall__` distribution
    the grouped cuts read, by the identical SQL expression, so it is one
    definition (Invariant 1) carrying its own n (Invariant 2), never a
    mean-of-means recomputed from the group results.

    `value` is None only when there are no respondents at all (n == 0); an empty
    dataset is reported as a null value, never as an average of 0 (Invariant 6).
    """

    value: float | None
    n: int


@dataclass(frozen=True)
class BreakdownResult:
    """A one-dimensional breakdown of a measure by a dimension."""

    measure: str
    dimension: str
    agg: str
    threshold: int | None
    cells: list[BreakdownCell]
    overall: OverallValue


def _require_numeric(measure: str) -> None:
    resolve_measure(measure)  # unknown measure -> UnknownMeasureError
    if not is_numeric_measure(measure):
        raise UnsupportedAggregationError(measure, "average/proportion")


def overall_average(session: Session, measure: str) -> OverallValue:
    """Mean of a numeric measure across all respondents (the ungrouped anchor).

    Reads the `__overall__` sentinel rows with the same expression as a grouped
    average, so the grand total has one definition and carries its n.
    """
    _require_numeric(measure)
    value, n = session.execute(
        text(
            """
            SELECT SUM(CAST(response_value AS INTEGER) * count)::float / SUM(count) AS value,
                   COALESCE(SUM(count), 0) AS n
            FROM distributions
            WHERE measure = :measure AND dimension = :dimension
            """
        ),
        {"measure": measure, "dimension": OVERALL_DIMENSION},
    ).one()
    return OverallValue(value=value, n=n)


def overall_proportion(session: Session, measure: str, threshold: int = 4) -> OverallValue:
    """Share of all respondents with a numeric measure at or above `threshold`, with n."""
    _require_numeric(measure)
    value, n = session.execute(
        text(
            """
            SELECT COALESCE(
                       SUM(count) FILTER (WHERE CAST(response_value AS INTEGER) >= :threshold), 0
                   )::float / SUM(count) AS value,
                   COALESCE(SUM(count), 0) AS n
            FROM distributions
            WHERE measure = :measure AND dimension = :dimension
            """
        ),
        {"measure": measure, "dimension": OVERALL_DIMENSION, "threshold": threshold},
    ).one()
    return OverallValue(value=value, n=n)


def breakdown_average(session: Session, measure: str, dimension: str) -> BreakdownResult:
    """Per-group mean of a numeric measure, with each group's n."""
    _require_numeric(measure)
    resolve_dimension(dimension)
    rows = session.execute(
        text(
            """
            SELECT group_value,
                   SUM(CAST(response_value AS INTEGER) * count)::float / SUM(count) AS value,
                   SUM(count) AS n
            FROM distributions
            WHERE measure = :measure AND dimension = :dimension
            GROUP BY group_value
            ORDER BY group_value
            """
        ),
        {"measure": measure, "dimension": dimension},
    ).all()
    cells = [BreakdownCell(group_value=gv, value=value, n=n) for gv, value, n in rows]
    return BreakdownResult(
        measure=measure,
        dimension=dimension,
        agg="average",
        threshold=None,
        cells=cells,
        overall=overall_average(session, measure),
    )


def breakdown_proportion(
    session: Session, measure: str, dimension: str, threshold: int = 4
) -> BreakdownResult:
    """Per-group share of a numeric measure at or above `threshold`, with each group's n."""
    _require_numeric(measure)
    resolve_dimension(dimension)
    rows = session.execute(
        text(
            """
            SELECT group_value,
                   COALESCE(
                       SUM(count) FILTER (WHERE CAST(response_value AS INTEGER) >= :threshold), 0
                   )::float / SUM(count) AS value,
                   SUM(count) AS n
            FROM distributions
            WHERE measure = :measure AND dimension = :dimension
            GROUP BY group_value
            ORDER BY group_value
            """
        ),
        {"measure": measure, "dimension": dimension, "threshold": threshold},
    ).all()
    cells = [BreakdownCell(group_value=gv, value=value, n=n) for gv, value, n in rows]
    return BreakdownResult(
        measure=measure,
        dimension=dimension,
        agg="proportion",
        threshold=threshold,
        cells=cells,
        overall=overall_proportion(session, measure, threshold),
    )
