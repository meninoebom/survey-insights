"""Transactional, idempotent full recompute.

`refresh` re-reads the source, re-cleans, and rebuilds both derived tables from
scratch inside ONE transaction (DELETE + INSERT, never TRUNCATE, so a concurrent
reader sees old-then-new via MVCC, never a half-empty table). An in-process lock
serializes refreshes (single writer). Running it twice on the same data yields
identical tables (Invariant 4).
"""

import threading
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from survey.ingest.pipeline import ingest_responses
from survey.ingest.source import DataSource
from survey.service.distributions import rebuild_distributions

_REFRESH_LOCK = threading.Lock()


@dataclass(frozen=True)
class RefreshSummary:
    files_processed: int
    rows_ingested: int
    rows_dropped: int
    drop_reasons: dict[str, int]
    tables_rebuilt: list[str]
    duration_ms: int


def rebuild_from_source(
    source: DataSource, session_factory: sessionmaker[Session]
) -> RefreshSummary:
    """Rebuild `responses` + `distributions` from a source in ONE transaction.

    The shared unit of work behind both `refresh` and the upload path, so there is
    one definition of "ingest a source and rebuild". It does NOT take the
    single-writer lock; the caller holds `_REFRESH_LOCK` around it so an upload and
    a refresh cannot interleave.
    """
    start = time.monotonic()
    with session_factory() as session, session.begin():
        ingest_summary = ingest_responses(source, session)
        rebuild_distributions(session)
    duration_ms = int((time.monotonic() - start) * 1000)
    return RefreshSummary(
        files_processed=ingest_summary.files_processed,
        rows_ingested=ingest_summary.rows_ingested,
        rows_dropped=ingest_summary.rows_dropped,
        drop_reasons=ingest_summary.drop_reasons,
        tables_rebuilt=["responses", "distributions"],
        duration_ms=duration_ms,
    )


def refresh(source: DataSource, session_factory: sessionmaker[Session]) -> RefreshSummary:
    """Rebuild `responses` + `distributions` from the source under the writer lock."""
    with _REFRESH_LOCK:
        return rebuild_from_source(source, session_factory)
