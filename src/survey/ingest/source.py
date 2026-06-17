"""The swappable data source.

Processing depends only on this interface, never on where the bytes live
(Invariant 7: the source location is configured, never request-supplied). The
production `S3Source` is a sibling swap behind the same interface (named here,
not built).
"""

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RawRows:
    """Raw, unvalidated columns and rows read from a source."""

    columns: list[str]
    rows: list[dict[str, str]]
    files_processed: int


class DataSource(ABC):
    """A source of raw survey rows."""

    @abstractmethod
    def read(self) -> RawRows:
        """Return the raw columns and rows from the source."""


class LocalDirectorySource(DataSource):
    """Reads survey rows from a configured local path (a file or a directory).

    Directory mode ingests the single *newest* CSV, never a concatenation. Each
    upload is saved with a sortable UTC-timestamped name (e.g.
    `survey-20260617T084500Z.csv`), so the files in the directory are a history
    and the lexicographically-greatest name is the newest. Selecting one file is
    deterministic (it sorts by name, not by mtime) and is what avoids the
    `id`-collision that concatenating multiple files would cause (`id` is the
    respondent primary key). This supersedes the earlier single-file-only caveat.

    File-path mode reads exactly that one file (used to validate an upload before
    promoting it). The local volume stands in for object storage; production swaps
    in S3 behind `DataSource`.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _csv_files(self) -> list[Path]:
        if self._path.is_dir():
            newest = max(self._path.glob("*.csv"), default=None)
            return [newest] if newest is not None else []
        return [self._path]

    def read(self) -> RawRows:
        columns: list[str] = []
        rows: list[dict[str, str]] = []
        files = self._csv_files()
        for csv_path in files:
            # utf-8-sig transparently strips a UTF-8 byte-order mark, so a CSV
            # exported from Excel still parses (its first header is `id`, not
            # `﻿id`); a plain UTF-8 file is unaffected.
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    continue
                if not columns:
                    columns = list(reader.fieldnames)
                for row in reader:
                    rows.append({key: (value or "") for key, value in row.items() if key})
        return RawRows(columns=columns, rows=rows, files_processed=len(files))
