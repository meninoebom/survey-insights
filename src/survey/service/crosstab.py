"""Live two-dimensional cross-tab: the headline capability.

Computed live with one GROUP BY over `responses` (the joint detail the
one-dimensional distributions cannot reconstruct). Numeric measures only in v1.
Every (row_value, col_value) in the grid gets a cell, including the sparse
corners: an empty cell (no respondents) is distinct from a low-n cell and is
never shown as an average of 0 (Invariant 6).

Row/col/measure resolve through the allowlist before any SQL is built, so no
caller string reaches a query as an identifier (Invariant 3).
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from survey.allowlist import is_numeric_measure, resolve_dimension, resolve_measure
from survey.service.breakdown import UnsupportedAggregationError


@dataclass(frozen=True)
class CrosstabCell:
    """One grid cell. `value` is None and `n` is 0 only for an empty cell."""

    row_value: str
    col_value: str
    status: str  # "populated" | "low_n" | "empty"
    value: float | None
    n: int
    reliability: str | None  # "low" for low_n, else None


@dataclass(frozen=True)
class CrosstabResult:
    measure: str
    row: str
    col: str
    agg: str  # "average" | "proportion"
    threshold: int | None  # the proportion threshold, else None
    row_values: list[str]
    col_values: list[str]
    cells: list[CrosstabCell]


def format_crosstab_cell(cell: CrosstabCell) -> str:
    """Render one cell as a compact human string for the terminal adapters.

    The single home for the display side of Invariant 6: an empty cell shows as
    "n/a" (never as a value of 0) and a low-n cell is flagged. Shared by the CLI
    and the TUI; the API serializes the structured cell instead.
    """
    if cell.status == "empty":
        return "n/a"
    suffix = " (low)" if cell.status == "low_n" else ""
    return f"{cell.value:.2f} [n={cell.n}]{suffix}"


def _distinct_values(session: Session, column: str) -> list[str]:
    rows = session.execute(
        text(f"SELECT DISTINCT {column} AS value FROM responses ORDER BY value")
    ).all()
    return [value for (value,) in rows]


def crosstab(
    session: Session,
    measure: str,
    row: str,
    col: str,
    min_reliable_n: int,
    *,
    agg: str = "average",
    threshold: int = 4,
) -> CrosstabResult:
    """A live cross-tab of a numeric measure by two dimensions, with cell status.

    `agg="average"` gives the mean per cell; `agg="proportion"` gives the share of
    the cell with the measure >= `threshold`.
    """
    measure_column = resolve_measure(measure)  # unknown -> UnknownMeasureError
    if not is_numeric_measure(measure):
        raise UnsupportedAggregationError(measure, "crosstab")
    row_column = resolve_dimension(row)
    col_column = resolve_dimension(col)

    row_values = _distinct_values(session, row_column)
    col_values = _distinct_values(session, col_column)

    if agg == "proportion":
        value_sql = f"AVG(CASE WHEN {measure_column} >= :threshold THEN 1.0 ELSE 0.0 END)::float"
        params: dict[str, int] = {"threshold": threshold}
    else:
        value_sql = f"AVG({measure_column})::float"
        params = {}

    populated: dict[tuple[str, str], tuple[float, int]] = {}
    grouped = session.execute(
        text(
            f"""
            SELECT {row_column} AS row_value, {col_column} AS col_value,
                   {value_sql} AS value, COUNT(*) AS n
            FROM responses
            GROUP BY {row_column}, {col_column}
            """
        ),
        params,
    ).all()
    for row_value, col_value, value, n in grouped:
        populated[(row_value, col_value)] = (value, n)

    cells: list[CrosstabCell] = []
    for row_value in row_values:
        for col_value in col_values:
            found = populated.get((row_value, col_value))
            if found is None:
                cells.append(CrosstabCell(row_value, col_value, "empty", None, 0, None))
                continue
            value, n = found
            if n < min_reliable_n:
                cells.append(CrosstabCell(row_value, col_value, "low_n", value, n, "low"))
            else:
                cells.append(CrosstabCell(row_value, col_value, "populated", value, n, None))
    return CrosstabResult(
        measure=measure,
        row=row,
        col=col,
        agg=agg,
        threshold=threshold if agg == "proportion" else None,
        row_values=row_values,
        col_values=col_values,
        cells=cells,
    )
