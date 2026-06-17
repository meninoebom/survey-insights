"""Distribution aggregation: the canonical per-response-value counts.

`rebuild_distributions` recomputes the precomputed distributions from `responses`
in SQL (the same GROUP BY the live cross-tab uses), so every number has exactly
one definition. It covers every measure (numeric ratings and the categorical
sentiment) by every dimension, plus the overall (ungrouped) cut.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from survey.allowlist import (
    DIMENSION_COLUMNS,
    MEASURE_COLUMNS,
    OVERALL_DIMENSION,
    OVERALL_GROUP_VALUE,
    resolve_dimension,
    resolve_measure,
)
from survey.db.models import Distribution


@dataclass(frozen=True)
class Bin:
    """One response_value and its count within a distribution."""

    response_value: str
    count: int


@dataclass(frozen=True)
class DistributionResult:
    """A measure's distribution. `dimension`/`group_value` are None when overall."""

    measure: str
    dimension: str | None
    group_value: str | None
    bins: list[Bin]
    n: int


@dataclass(frozen=True)
class GroupedDistribution:
    """A measure's distribution split into one result per group within a dimension."""

    measure: str
    dimension: str
    groups: list[DistributionResult]


def _insert_overall_distribution(session: Session, measure: str) -> None:
    # Column comes from the allowlist (a fixed identifier), so interpolating it is
    # safe; names are bound parameters (Invariant 3).
    column = resolve_measure(measure)
    session.execute(
        text(
            f"""
            INSERT INTO distributions (measure, dimension, group_value, response_value, count)
            SELECT :measure, :dimension, :group_value, CAST({column} AS TEXT), COUNT(*)
            FROM responses
            GROUP BY {column}
            """
        ),
        {"measure": measure, "dimension": OVERALL_DIMENSION, "group_value": OVERALL_GROUP_VALUE},
    )


def _insert_grouped_distribution(session: Session, measure: str, dimension: str) -> None:
    measure_column = resolve_measure(measure)
    dimension_column = resolve_dimension(dimension)
    session.execute(
        text(
            f"""
            INSERT INTO distributions (measure, dimension, group_value, response_value, count)
            SELECT :measure, :dimension, CAST({dimension_column} AS TEXT),
                   CAST({measure_column} AS TEXT), COUNT(*)
            FROM responses
            GROUP BY {dimension_column}, {measure_column}
            """
        ),
        {"measure": measure, "dimension": dimension},
    )


def rebuild_distributions(session: Session) -> None:
    """Recompute every precomputed distribution from `responses`. Caller commits."""
    session.execute(delete(Distribution))
    for measure in sorted(MEASURE_COLUMNS):
        _insert_overall_distribution(session, measure)
        for dimension in sorted(DIMENSION_COLUMNS):
            _insert_grouped_distribution(session, measure, dimension)


def read_overall_distribution(session: Session, measure: str) -> DistributionResult:
    """Read the stored overall distribution for a measure (validates the measure)."""
    resolve_measure(measure)  # raises UnknownMeasureError if not allowlisted
    rows = session.execute(
        select(Distribution.response_value, Distribution.count)
        .where(Distribution.measure == measure)
        .where(Distribution.dimension == OVERALL_DIMENSION)
        .where(Distribution.group_value == OVERALL_GROUP_VALUE)
        .order_by(Distribution.response_value)
    ).all()
    bins = [Bin(response_value=response_value, count=count) for response_value, count in rows]
    return DistributionResult(
        measure=measure, dimension=None, group_value=None, bins=bins, n=sum(b.count for b in bins)
    )


def read_grouped_distribution(
    session: Session, measure: str, dimension: str
) -> GroupedDistribution:
    """Read the stored distribution grouped by a dimension (validates both names)."""
    resolve_measure(measure)
    resolve_dimension(dimension)
    rows = session.execute(
        select(Distribution.group_value, Distribution.response_value, Distribution.count)
        .where(Distribution.measure == measure)
        .where(Distribution.dimension == dimension)
        .order_by(Distribution.group_value, Distribution.response_value)
    ).all()
    bins_by_group: dict[str, list[Bin]] = {}
    for group_value, response_value, count in rows:
        bins_by_group.setdefault(group_value, []).append(
            Bin(response_value=response_value, count=count)
        )
    groups = [
        DistributionResult(
            measure=measure,
            dimension=dimension,
            group_value=group_value,
            bins=bins,
            n=sum(b.count for b in bins),
        )
        for group_value, bins in bins_by_group.items()
    ]
    return GroupedDistribution(measure=measure, dimension=dimension, groups=groups)
