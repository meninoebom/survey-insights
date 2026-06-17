import pytest

from survey.cli.main import main


def test_unknown_flag_shows_the_commands_accepted_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["distribution", "--measure", "q1_rating", "--ag", "average"])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "distribution" in err  # names the command
    assert "--ag" in err  # echoes the rejected token
    assert "--measure" in err and "--by" in err  # lists what the command accepts
    # and does NOT fall back to argparse's unhelpful top-level command list
    assert "{distribution,breakdown,crosstab,refresh}" not in err


def test_unknown_flag_lists_accepted_flags_for_crosstab(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["crosstab", "--measure", "q1_rating", "--rows", "gender"])
    err = capsys.readouterr().err
    assert "crosstab" in err
    assert "--row" in err and "--col" in err
