"""Guardrail: a directory source ingests only the newest CSV, not a concatenation.

Multiple CSVs in the source directory are a history (each upload is timestamped);
the lexicographically-greatest filename is the newest and is the one ingested.
Reading only one file is what avoids the `id`-collision that concatenating files
would cause. Single-file path mode is unchanged (used to validate an upload).
"""

import csv
from pathlib import Path

from survey.ingest.source import LocalDirectorySource

COLUMNS = ["id", "q1_rating"]


def _write(path: Path, ids: list[int]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for i in ids:
            writer.writerow({"id": str(i), "q1_rating": "5"})


def test_directory_source_reads_only_the_newest_file(tmp_path: Path) -> None:
    # Timestamped names sort lexicographically; the greatest is newest.
    _write(tmp_path / "survey-20260101T000000Z.csv", [1, 2])
    _write(tmp_path / "survey-20260617T120000Z.csv", [10, 11, 12])

    raw = LocalDirectorySource(tmp_path).read()

    assert raw.files_processed == 1
    assert [r["id"] for r in raw.rows] == ["10", "11", "12"]  # newest only, not concatenated


def test_single_file_path_mode_reads_that_file(tmp_path: Path) -> None:
    path = tmp_path / "one.csv"
    _write(path, [7, 8])

    raw = LocalDirectorySource(path).read()

    assert raw.files_processed == 1
    assert [r["id"] for r in raw.rows] == ["7", "8"]


def test_empty_directory_reads_nothing(tmp_path: Path) -> None:
    raw = LocalDirectorySource(tmp_path).read()
    assert raw.files_processed == 0
    assert raw.rows == []
