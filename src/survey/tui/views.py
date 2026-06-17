"""TUI view widgets: one per analysis (distribution, breakdown, cross-tab).

Each view owns its controls and a results table, handles its own Select changes,
and calls the existing service layer. Invalid combinations (e.g. average on
sentiment, which the service rejects) surface as an inline message instead of
crashing the app. Nothing queries until a valid selection is made, so the views
mount without a database.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Label, Select

from survey.allowlist import (
    DIMENSION_COLUMNS,
    MEASURE_COLUMNS,
    UnknownDimensionError,
    UnknownMeasureError,
)
from survey.config import get_settings
from survey.service.breakdown import (
    UnsupportedAggregationError,
    breakdown_average,
    breakdown_proportion,
)
from survey.service.crosstab import crosstab, format_crosstab_cell
from survey.service.distributions import read_grouped_distribution, read_overall_distribution

_OVERALL = "(overall)"
_SERVICE_ERRORS = (UnsupportedAggregationError, UnknownMeasureError, UnknownDimensionError)
_RATINGS = (1, 2, 3, 4, 5)
Factory = Callable[[], sessionmaker[Session]]
_MEASURES = sorted(MEASURE_COLUMNS)
_DIMENSIONS = sorted(DIMENSION_COLUMNS)


def _measure_select(name: str) -> Select[str]:
    return Select([(m, m) for m in _MEASURES], prompt="measure", id=name)


def _dimension_select(name: str, *, include_overall: bool) -> Select[str]:
    options = [(d, d) for d in _DIMENSIONS]
    if include_overall:
        options = [(_OVERALL, _OVERALL), *options]
    return Select(options, prompt="dimension", id=name)


def _threshold_box(name: str) -> Horizontal:
    # A labeled dropdown of the rating values (1-5) for the proportion threshold.
    return Horizontal(
        Label("Threshold"),
        Select([(str(t), t) for t in _RATINGS], value=4, allow_blank=False, id=name),
        id=f"{name}-box",
        classes="threshold-box",
    )


def _selected_threshold(value: object) -> int:
    return value if isinstance(value, int) else 4


class View(Vertical):
    """Shared base for the analysis views: holds the session factory.

    `reload()` re-runs the current selection and re-renders. The app calls it on
    every view after a drag-and-drop re-ingest, so the open tab reflects the new
    data immediately.
    """

    def __init__(self, factory: Factory) -> None:
        super().__init__()
        self._factory = factory

    def reload(self) -> None:
        """Re-run the current query and re-render. Overridden by each view."""
        raise NotImplementedError


class DistributionView(View):
    """Pick a measure and an optional dimension; see the distribution."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls"):
            yield _measure_select("dist-measure")
            yield _dimension_select("dist-by", include_overall=True)
        yield Label("Pick a measure.", id="dist-status")
        yield DataTable(id="dist-table", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#dist-table", DataTable).add_columns("group", "response_value", "count")

    def on_select_changed(self, event: Select.Changed) -> None:
        self.reload()

    def reload(self) -> None:
        measure = self.query_one("#dist-measure", Select).value
        by = self.query_one("#dist-by", Select).value
        table = self.query_one("#dist-table", DataTable)
        status = self.query_one("#dist-status", Label)
        table.clear()
        if measure not in MEASURE_COLUMNS:
            status.update("Pick a measure.")
            return
        make_session = self._factory()
        with make_session() as session:
            if by in DIMENSION_COLUMNS:
                grouped = read_grouped_distribution(session, str(measure), str(by))
                for group in grouped.groups:
                    for b in group.bins:
                        table.add_row(group.group_value, b.response_value, str(b.count))
                status.update(f"{measure} by {by}")
            else:
                overall = read_overall_distribution(session, str(measure))
                for b in overall.bins:
                    table.add_row(_OVERALL, b.response_value, str(b.count))
                status.update(f"{measure} (overall, n={overall.n})")


class BreakdownView(View):
    """Average or proportion of a numeric measure by a dimension, with n."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls"):
            yield _measure_select("bd-measure")
            yield _dimension_select("bd-by", include_overall=False)
            yield Select(
                [("average", "average"), ("proportion", "proportion")],
                value="average",
                allow_blank=False,
                id="bd-agg",
            )
            yield _threshold_box("bd-threshold")
        yield Label("Pick a measure and a dimension.", id="bd-status")
        yield DataTable(id="bd-table", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#bd-table", DataTable).add_columns("group", "value", "n")
        self._sync_threshold()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._sync_threshold()
        self.reload()

    def _sync_threshold(self) -> None:
        # The threshold applies only to proportion; hide it for average.
        self.query_one("#bd-threshold-box").display = (
            self.query_one("#bd-agg", Select).value == "proportion"
        )

    def _threshold(self) -> int:
        return _selected_threshold(self.query_one("#bd-threshold", Select).value)

    def reload(self) -> None:
        measure = self.query_one("#bd-measure", Select).value
        by = self.query_one("#bd-by", Select).value
        agg = self.query_one("#bd-agg", Select).value
        table = self.query_one("#bd-table", DataTable)
        status = self.query_one("#bd-status", Label)
        table.clear()
        if measure not in MEASURE_COLUMNS or by not in DIMENSION_COLUMNS:
            status.update("Pick a measure and a dimension.")
            return
        threshold = self._threshold()
        try:
            make_session = self._factory()
            with make_session() as session:
                if agg == "proportion":
                    result = breakdown_proportion(session, str(measure), str(by), threshold)
                    label = f"{measure} by {by} (proportion >= {threshold})"
                else:
                    result = breakdown_average(session, str(measure), str(by))
                    label = f"{measure} by {by} (average)"
            for cell in result.cells:
                table.add_row(cell.group_value, f"{cell.value:.2f}", str(cell.n))
            status.update(label)
        except _SERVICE_ERRORS as exc:
            status.update(f"[error] {exc}")


class CrosstabView(View):
    """Live two-dimensional cross-tab of a numeric measure, with cell status."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls"):
            yield _measure_select("ct-measure")
            yield _dimension_select("ct-row", include_overall=False)
            yield _dimension_select("ct-col", include_overall=False)
            yield Select(
                [("average", "average"), ("proportion", "proportion")],
                value="average",
                allow_blank=False,
                id="ct-agg",
            )
            yield _threshold_box("ct-threshold")
        yield Label("Pick a numeric measure, a row, and a column.", id="ct-status")
        yield DataTable(id="ct-table", zebra_stripes=True)

    def on_mount(self) -> None:
        self._sync_threshold()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._sync_threshold()
        self.reload()

    def _sync_threshold(self) -> None:
        # The threshold applies only to proportion; hide it for average.
        self.query_one("#ct-threshold-box").display = (
            self.query_one("#ct-agg", Select).value == "proportion"
        )

    def _threshold(self) -> int:
        return _selected_threshold(self.query_one("#ct-threshold", Select).value)

    def reload(self) -> None:
        measure = self.query_one("#ct-measure", Select).value
        row = self.query_one("#ct-row", Select).value
        col = self.query_one("#ct-col", Select).value
        agg = "proportion" if self.query_one("#ct-agg", Select).value == "proportion" else "average"
        table = self.query_one("#ct-table", DataTable)
        status = self.query_one("#ct-status", Label)
        table.clear(columns=True)
        if (
            measure not in MEASURE_COLUMNS
            or row not in DIMENSION_COLUMNS
            or col not in DIMENSION_COLUMNS
        ):
            status.update("Pick a numeric measure, a row, and a column.")
            return
        threshold = self._threshold()
        try:
            make_session = self._factory()
            with make_session() as session:
                result = crosstab(
                    session,
                    str(measure),
                    str(row),
                    str(col),
                    get_settings().min_reliable_n,
                    agg=agg,
                    threshold=threshold,
                )
            table.add_columns(result.row, *result.col_values)
            by_cell = {(c.row_value, c.col_value): c for c in result.cells}
            for row_value in result.row_values:
                rendered = [
                    format_crosstab_cell(by_cell[(row_value, cv)]) for cv in result.col_values
                ]
                table.add_row(row_value, *rendered)
            label = f"{measure}: {row} x {col} ({agg}"
            label += f" >= {threshold})" if agg == "proportion" else ")"
            status.update(f"{label}   (low = below MIN_RELIABLE_N; n/a = empty)")
        except _SERVICE_ERRORS as exc:
            status.update(f"[error] {exc}")
