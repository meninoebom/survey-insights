import asyncio

import pytest
from textual.widgets import Select

from survey.config import get_settings
from survey.tui.app import SurveyExplorer


def test_threshold_hidden_until_proportion(monkeypatch: pytest.MonkeyPatch) -> None:
    # The threshold control only applies to proportion, so its box is hidden for the
    # default (average) and revealed when proportion is selected. The unreachable URL
    # keeps this a pure UI check: mounting and toggling touch no database.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    try:

        async def _toggle() -> tuple[bool, bool]:
            app = SurveyExplorer()
            async with app.run_test() as pilot:
                box = app.query_one("#bd-threshold-box")
                hidden_on_average = box.display
                app.query_one("#bd-agg", Select).value = "proportion"
                await pilot.pause()
                shown_on_proportion = box.display
                return hidden_on_average, shown_on_proportion

        hidden_on_average, shown_on_proportion = asyncio.run(_toggle())
        assert not hidden_on_average  # hidden for the default (average)
        assert shown_on_proportion  # revealed when proportion is chosen
    finally:
        get_settings.cache_clear()
