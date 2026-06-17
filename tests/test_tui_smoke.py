import asyncio

import pytest
from textual.widgets import DataTable

from survey.config import get_settings
from survey.tui.app import SurveyExplorer


def test_app_mounts_without_querying(monkeypatch: pytest.MonkeyPatch) -> None:
    # A configured but unreachable URL: if mounting tried to query the DB it would
    # fail to connect. It must not (no selection yet), so the views mount and the
    # distribution table gets its three columns. Driven headless.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    try:

        async def _dist_columns() -> int:
            app = SurveyExplorer()
            async with app.run_test():
                return len(app.query_one("#dist-table", DataTable).columns)

        assert asyncio.run(_dist_columns()) == 3
    finally:
        get_settings.cache_clear()
