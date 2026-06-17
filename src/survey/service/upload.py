"""Ingest a CSV supplied as uploaded contents (drag-and-drop), and reset to sample.

The upload area (the configured `SOURCE_DIR`, a writable volume) holds only user
uploads, saved as timestamped CSVs; the newest one wins. The bundled sample is a
separate, read-only fallback (`BUNDLED_SAMPLE`): it is never written into the upload
area. The live dataset is the newest upload if any exists, otherwise the sample.

So provenance is structural (is there an upload, or not), not a stored flag, and
"reset to sample" simply discards the uploads so the fallback takes over.

A request carries file *contents*; the server decides where to write them. It never
reads a caller-named path (constraint 7).

Upload atomicity (inside the one writer-locked critical section):
1. Write the uploaded bytes to a temp file outside the upload area, so a failed
   validation never leaves a partial file there.
2. Ingest + rebuild from that temp file in one transaction. A bad CSV (a missing
   required column) raises here, the transaction rolls back, and the upload area is
   left untouched.
3. Only after the commit succeeds, promote the temp file into the upload area as a
   new timestamped file, so it becomes the newest. We promote after the commit, so a
   file is never the newest without its data already committed.
"""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from survey.ingest.source import LocalDirectorySource
from survey.service.refresh import _REFRESH_LOCK, RefreshSummary, rebuild_from_source


def _timestamped_name() -> str:
    """A sortable, effectively-unique UTC filename: `survey-<UTC>.csv`."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    return f"survey-{stamp}.csv"


def _has_upload(source_dir: Path) -> bool:
    return source_dir.is_dir() and any(source_dir.glob("*.csv"))


def current_origin(source_dir: str | Path) -> str:
    """Whether the live dataset is a user "upload" or the bundled "sample".

    Structural, not a stored flag: an upload is live exactly when the upload area
    holds a CSV. Falls back to "sample" otherwise (including a missing directory).
    """
    return "upload" if _has_upload(Path(source_dir)) else "sample"


def resolve_source(
    source_dir: str | Path, bundled_sample: str | Path | None
) -> LocalDirectorySource | None:
    """The live source: the newest upload if any, else the bundled sample fallback.

    Returns None only when there is neither an upload nor a configured sample, so a
    caller can leave the tables empty rather than ingest from nothing.
    """
    source_dir = Path(source_dir)
    if _has_upload(source_dir):
        return LocalDirectorySource(source_dir)  # the directory yields its newest CSV
    if bundled_sample:
        return LocalDirectorySource(bundled_sample)
    return None


def ingest_upload(
    contents: bytes, source_dir: str | Path, session_factory: sessionmaker[Session]
) -> RefreshSummary:
    """Ingest uploaded CSV bytes atomically, then promote them as the newest upload.

    On success the bytes are saved into the upload area as a timestamped CSV (now the
    newest, consistent with the DB). On failure nothing is written and the prior data
    is left intact. Raises the same validation errors as the pipeline.
    """
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    with _REFRESH_LOCK:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(contents)
        try:
            summary = rebuild_from_source(LocalDirectorySource(temp_path), session_factory)
            # Promote only after a clean commit, so the upload area never advertises a
            # newest file whose data did not land.
            shutil.move(str(temp_path), str(source_dir / _timestamped_name()))
            return summary
        finally:
            temp_path.unlink(missing_ok=True)


def reset_to_sample(
    bundled_sample: str | Path, source_dir: str | Path, session_factory: sessionmaker[Session]
) -> RefreshSummary:
    """Discard any uploads and rebuild from the bundled sample (the fallback).

    Clears the upload area so the live source falls back to the bundled sample, then
    rebuilds the tables from it, under the single writer lock. Uploads are cleared
    first: if the rebuild somehow failed, a later boot or refresh still resolves to
    the sample, so the system self-heals rather than silently reverting to an upload.
    """
    source_dir = Path(source_dir)
    with _REFRESH_LOCK:
        for csv_file in source_dir.glob("*.csv"):
            csv_file.unlink(missing_ok=True)
        return rebuild_from_source(LocalDirectorySource(bundled_sample), session_factory)
