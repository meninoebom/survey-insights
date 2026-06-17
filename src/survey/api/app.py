"""FastAPI application: a thin adapter over the service layer.

On boot the lifespan creates the tables and, if SOURCE_DIR is configured, ingests
the live source: the newest uploaded CSV if any, otherwise the bundled sample
fallback (the same transactional `refresh` path the endpoint uses), so a cold
`docker compose up` comes up queryable.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from survey.allowlist import UnknownDimensionError, UnknownMeasureError
from survey.api.routes import router
from survey.config import get_settings
from survey.db.session import create_db_engine, create_session_factory, init_db
from survey.ingest.validation import MissingColumnsError
from survey.service.breakdown import UnsupportedAggregationError
from survey.service.refresh import refresh
from survey.service.upload import resolve_source


def _ingest_live_source(app: FastAPI) -> None:
    """On boot: ingest the live source (the newest upload, or the bundled sample).

    A persisted upload survives a restart and wins; with no upload, the bundled
    sample is the fallback. With neither (no upload and no configured sample) the
    tables are left empty rather than failing column validation on nothing.
    """
    settings = get_settings()
    if not settings.source_dir:
        return
    source = resolve_source(settings.source_dir, settings.bundled_sample)
    if source is not None:
        refresh(source, app.state.session_factory)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_db_engine()
    init_db(engine)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    _ingest_live_source(app)
    try:
        yield
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Survey Insights System", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)

    # The web UI: a read-only API consumer served same-origin (no CORS). Visiting
    # the root redirects to it; the API endpoints keep their own paths.
    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    static_dir = Path(__file__).resolve().parent.parent / "web" / "static"
    app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    @app.exception_handler(UnknownMeasureError)
    async def _on_unknown_measure(request: Request, exc: UnknownMeasureError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": str(exc), "valid_measures": exc.valid_measures}
        )

    @app.exception_handler(UnknownDimensionError)
    async def _on_unknown_dimension(request: Request, exc: UnknownDimensionError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": str(exc), "valid_dimensions": exc.valid_dimensions}
        )

    @app.exception_handler(UnsupportedAggregationError)
    async def _on_unsupported_aggregation(
        request: Request, exc: UnsupportedAggregationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc), "measure": exc.measure, "agg": exc.agg},
        )

    @app.exception_handler(MissingColumnsError)
    async def _on_missing_columns(request: Request, exc: MissingColumnsError) -> JSONResponse:
        # A bad upload (missing required columns) is a client error, not a 500.
        return JSONResponse(
            status_code=400, content={"error": str(exc), "missing_columns": exc.missing}
        )

    return app


app = create_app()
