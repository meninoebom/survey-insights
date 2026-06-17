import pytest

import survey.cli.main as cli
from survey.allowlist import DIMENSION_COLUMNS, MEASURE_COLUMNS
from survey.cli.main import main


def test_measures_lists_all_with_aggregations(capsys: pytest.CaptureFixture[str]) -> None:
    main(["measures"])
    out = capsys.readouterr().out
    for measure in MEASURE_COLUMNS:
        assert measure in out
    # numeric measures advertise average + proportion; sentiment is distribution-only
    assert "average" in out
    assert "proportion" in out


def test_dimensions_lists_all(capsys: pytest.CaptureFixture[str]) -> None:
    main(["dimensions"])
    out = capsys.readouterr().out
    for dimension in DIMENSION_COLUMNS:
        assert dimension in out


def test_measures_needs_no_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # If the discovery command tried to open a DB, this would raise.
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("discovery commands must not open a database connection")

    monkeypatch.setattr(cli, "create_db_engine", _boom)
    main(["measures"])
    assert "sentiment_label" in capsys.readouterr().out
