"""API routes: thin handlers that resolve params and call the service layer."""

from collections.abc import Iterable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from survey.allowlist import DIMENSION_COLUMNS, MEASURE_COLUMNS, is_numeric_measure
from survey.api.deps import SessionDep
from survey.config import get_settings
from survey.service.breakdown import breakdown_average, breakdown_proportion
from survey.service.crosstab import crosstab
from survey.service.distributions import (
    Bin,
    read_grouped_distribution,
    read_overall_distribution,
)
from survey.service.refresh import RefreshSummary, refresh
from survey.service.upload import current_origin, ingest_upload, reset_to_sample, resolve_source

router = APIRouter()

# Reject an upload larger than this before reading it (defense against a huge body).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _bins_payload(bins: Iterable[Bin]) -> list[dict[str, Any]]:
    return [{"response_value": b.response_value, "count": b.count} for b in bins]


def _summary_payload(summary: RefreshSummary) -> dict[str, Any]:
    """The one summary shape every rebuild endpoint returns (refresh, ingest, reset)."""
    return {
        "files_processed": summary.files_processed,
        "rows_ingested": summary.rows_ingested,
        "rows_dropped": summary.rows_dropped,
        "drop_reasons": summary.drop_reasons,
        "tables_rebuilt": summary.tables_rebuilt,
        "duration_ms": summary.duration_ms,
    }


def _require_source_dir() -> str:
    settings = get_settings()
    if not settings.source_dir:
        raise HTTPException(status_code=400, detail="No SOURCE_DIR configured.")
    return settings.source_dir


@router.get("/meta")
def get_meta() -> dict[str, Any]:
    """The client vocabulary: which measures exist (and which are numeric), the
    dimensions, and the reliability threshold.

    Sourced from the allowlist and config so a UI reads its options from the API
    rather than holding a second copy that could drift. Ordering and display
    labels are presentation concerns left to the caller.
    """
    settings = get_settings()
    return {
        "measures": [{"id": m, "numeric": is_numeric_measure(m)} for m in sorted(MEASURE_COLUMNS)],
        "dimensions": sorted(DIMENSION_COLUMNS),
        "min_reliable_n": settings.min_reliable_n,
        # Whether the live dataset is the bundled sample or a user upload, so the UI
        # can show which is loaded and disable "use sample" when nothing would change.
        "source": current_origin(settings.source_dir) if settings.source_dir else "sample",
    }


@router.get("/distribution")
def get_distribution(measure: str, session: SessionDep, by: str | None = None) -> dict[str, Any]:
    """A measure's distribution: overall, or grouped by a dimension when `by` is set."""
    if by is None:
        result = read_overall_distribution(session, measure)
        return {
            "measure": result.measure,
            "dimension": None,
            "n": result.n,
            "distribution": _bins_payload(result.bins),
        }
    grouped = read_grouped_distribution(session, measure, by)
    return {
        "measure": grouped.measure,
        "dimension": grouped.dimension,
        "groups": [
            {"group_value": g.group_value, "n": g.n, "distribution": _bins_payload(g.bins)}
            for g in grouped.groups
        ],
    }


@router.get("/breakdown")
def get_breakdown(
    measure: str,
    by: str,
    session: SessionDep,
    agg: Literal["average", "proportion", "distribution"] = "average",
    threshold: int = 4,
) -> dict[str, Any]:
    """A one-dimensional breakdown of a measure by a dimension."""
    if agg == "distribution":
        grouped = read_grouped_distribution(session, measure, by)
        return {
            "measure": grouped.measure,
            "dimension": grouped.dimension,
            "groups": [
                {"group_value": g.group_value, "n": g.n, "distribution": _bins_payload(g.bins)}
                for g in grouped.groups
            ],
        }
    result = (
        breakdown_average(session, measure, by)
        if agg == "average"
        else breakdown_proportion(session, measure, by, threshold)
    )
    return {
        "measure": result.measure,
        "dimension": result.dimension,
        "agg": result.agg,
        "threshold": result.threshold,
        "overall": {"value": result.overall.value, "n": result.overall.n},
        "breakdown": [
            {"group_value": c.group_value, "value": c.value, "n": c.n} for c in result.cells
        ],
    }


@router.get("/crosstab")
def get_crosstab(
    measure: str,
    row: str,
    col: str,
    session: SessionDep,
    agg: Literal["average", "proportion"] = "average",
    threshold: int = 4,
) -> dict[str, Any]:
    """Live two-dimensional cross-tab (numeric measures only)."""
    result = crosstab(
        session, measure, row, col, get_settings().min_reliable_n, agg=agg, threshold=threshold
    )
    cells: list[dict[str, Any]] = []
    for c in result.cells:
        cell: dict[str, Any] = {
            "row_value": c.row_value,
            "col_value": c.col_value,
            "status": c.status,
            "value": c.value,
            "n": c.n,
        }
        if c.reliability is not None:
            cell["reliability"] = c.reliability
        cells.append(cell)
    return {
        "measure": result.measure,
        "row": result.row,
        "col": result.col,
        "agg": result.agg,
        "threshold": result.threshold,
        "row_values": result.row_values,
        "col_values": result.col_values,
        "cells": cells,
    }


@router.post("/refresh")
def post_refresh(request: Request) -> dict[str, Any]:
    """Re-ingest the live source (the newest upload, or the bundled sample) and rebuild.

    The source location comes from config, never the request body (Invariant 7).
    """
    source_dir = _require_source_dir()
    source = resolve_source(source_dir, get_settings().bundled_sample)
    if source is None:
        raise HTTPException(status_code=400, detail="Nothing to ingest: no upload and no sample.")
    summary = refresh(source, request.app.state.session_factory)
    return _summary_payload(summary)


@router.post("/ingest")
async def post_ingest(request: Request) -> dict[str, Any]:
    """Ingest an uploaded CSV supplied as the raw request body, atomically.

    The body is the file's *contents* (the browser posts the file directly, no
    multipart). The server writes those bytes into the configured source directory
    itself; it never reads a caller-named path (Invariant 7). On success the upload
    becomes the newest file and the data is replaced; a bad CSV is a clean 4xx with
    the prior data left intact.
    """
    source_dir = _require_source_dir()
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded CSV exceeds the size limit.")
    contents = await request.body()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded CSV exceeds the size limit.")
    if not contents.strip():
        raise HTTPException(status_code=400, detail="Empty upload; expected CSV contents.")
    summary = ingest_upload(contents, source_dir, request.app.state.session_factory)
    return _summary_payload(summary)


@router.post("/ingest/sample")
def post_reset_to_sample(request: Request) -> dict[str, Any]:
    """Restore the bundled sample as the newest dataset and rebuild.

    Reuses the upload machinery; the source location stays config-fixed.
    """
    source_dir = _require_source_dir()
    settings = get_settings()
    if not settings.bundled_sample:
        raise HTTPException(status_code=400, detail="No BUNDLED_SAMPLE configured to reset to.")
    summary = reset_to_sample(
        settings.bundled_sample, source_dir, request.app.state.session_factory
    )
    return _summary_payload(summary)
