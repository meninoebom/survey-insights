"""A Textual TUI over the service layer: a third thin adapter, sibling to the API and CLI.

Three tabs (distribution, breakdown, cross-tab), each a view widget over the same
service layer. The app owns a single lazy session
factory and hands it to the views, so building the app touches no database; a
connection opens only on the first valid query.

Drag-and-drop re-ingest: drop a CSV onto the terminal and the app re-runs the
transactional `refresh` (re-ingest + rebuild distributions) against the dropped
file, then reloads every view. This works because the TUI is a host process, so
it can read the dropped host path directly; an in-container adapter could not.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session, sessionmaker
from textual import work
from textual.app import App, ComposeResult
from textual.events import Paste
from textual.widgets import Footer, Header, TabbedContent, TabPane

from survey.db.session import create_db_engine, create_session_factory
from survey.ingest.source import LocalDirectorySource
from survey.service.refresh import RefreshSummary, refresh
from survey.tui.views import BreakdownView, CrosstabView, DistributionView, View


def _dropped_path(text: str) -> str | None:
    """Pull a single filesystem path out of terminal drag-and-drop / paste text.

    A terminal delivers a dropped file as pasted text: a path with spaces
    backslash-escaped, or wrapped in quotes, or (some environments) a file:// URI.
    Returns the first path, or None when the paste is not a usable path.
    """
    text = text.strip()
    if not text:
        return None
    if text.startswith("file://"):
        return unquote(urlparse(text).path) or None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    return tokens[0] if tokens else None


class SurveyExplorer(App[None]):
    """Tabbed explorer for survey insights."""

    TITLE = "Survey Insights Explorer"
    SUB_TITLE = "drop a CSV onto the window to re-ingest"
    CSS = """
    .controls { height: auto; padding: 1; }
    .controls Select { width: 1fr; }
    .threshold-box { width: auto; height: auto; }
    .threshold-box Label { height: 3; content-align: left middle; }
    #bd-threshold, #ct-threshold { width: 10; }
    Label { padding: 0 1; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._session_factory: sessionmaker[Session] | None = None

    def _factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._session_factory = create_session_factory(create_db_engine())
        return self._session_factory

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Distribution", id="tab-distribution"):
                yield DistributionView(self._factory)
            with TabPane("Breakdown", id="tab-breakdown"):
                yield BreakdownView(self._factory)
            with TabPane("Cross-tab", id="tab-crosstab"):
                yield CrosstabView(self._factory)
        yield Footer()

    def on_paste(self, event: Paste) -> None:
        """A terminal drag-and-drop arrives as a paste; re-ingest if it is a file.

        Caveat: if a threshold Input has focus it consumes the paste first, so drop
        onto the table or a dropdown. Stray (non-path) pastes are ignored.
        """
        path = _dropped_path(event.text)
        if path is None:
            return
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            # Only complain when it looked like a path; ignore stray text pastes.
            if "/" in path or path.startswith("~"):
                self.notify(f"Not a file: {path}", severity="error")
            return
        event.stop()
        self.notify(f"Ingesting {candidate.name}...")
        self._ingest(str(candidate))

    @work(thread=True, exclusive=True, group="ingest")
    def _ingest(self, path: str) -> None:
        """Re-ingest the dropped CSV off the UI thread, then reload the views."""
        try:
            summary = refresh(LocalDirectorySource(path), self._factory())
        except Exception as exc:  # surface bad files in the UI; never crash the TUI
            self.call_from_thread(self.notify, f"Ingest failed: {exc}", severity="error")
            return
        self.call_from_thread(self._after_ingest, summary)

    def _after_ingest(self, summary: RefreshSummary) -> None:
        for view in self.query(View):
            view.reload()
        message = f"Ingested {summary.rows_ingested} rows from {summary.files_processed} file(s)"
        if summary.rows_dropped:
            message += f"; dropped {summary.rows_dropped}"
        self.notify(message)


def run() -> None:
    SurveyExplorer().run()
