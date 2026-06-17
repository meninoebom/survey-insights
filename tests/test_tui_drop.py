"""Tests for the drag-and-drop re-ingest feature.

Two pieces carry real logic worth pinning:
- `_dropped_path`: parsing the file path a terminal hands over on drop (escaped,
  quoted, or a file:// URI), so a regression cannot silently re-ingest the wrong
  file (or none).
- the reload broadcast: `_after_ingest` calls `reload()` on `query(View)`, so that
  selector must find every tab's view; otherwise a dropped file would not refresh
  the open tab.
"""

import asyncio

import pytest

from survey.config import get_settings
from survey.tui.app import SurveyExplorer, _dropped_path
from survey.tui.views import View


@pytest.mark.parametrize(
    "text, expected",
    [
        ("/data/survey.csv", "/data/survey.csv"),
        ("  /data/survey.csv  ", "/data/survey.csv"),  # terminals add stray whitespace
        ("/data/my\\ survey.csv", "/data/my survey.csv"),  # escaped space (macOS Terminal)
        ("'/data/my survey.csv'", "/data/my survey.csv"),  # single-quoted
        ('"/data/my survey.csv"', "/data/my survey.csv"),  # double-quoted
        ("file:///data/my%20survey.csv", "/data/my survey.csv"),  # file URI
        ("/first.csv /second.csv", "/first.csv"),  # multi-file drop: first wins
        ("", None),
        ("   ", None),
    ],
)
def test_dropped_path(text: str, expected: str | None) -> None:
    assert _dropped_path(text) == expected


def test_all_views_are_reloadable(monkeypatch: pytest.MonkeyPatch) -> None:
    # A configured but unreachable URL: mounting and querying the widget tree touch
    # no database, so this stays a pure structural check. query(View) must return
    # all three tab views, since that is what _after_ingest reloads on a drop.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    try:

        async def _count() -> int:
            app = SurveyExplorer()
            async with app.run_test():
                return len(app.query(View))

        assert asyncio.run(_count()) == 3
    finally:
        get_settings.cache_clear()
