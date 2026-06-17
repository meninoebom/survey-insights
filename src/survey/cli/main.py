"""CLI: a thin adapter calling the service layer directly (no HTTP server).

The interface for a non-technical user. Every command prints an aligned table,
and `n` is shown wherever an average or proportion appears.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from survey.allowlist import DIMENSION_COLUMNS, MEASURE_COLUMNS, NUMERIC_MEASURES
from survey.config import get_settings
from survey.db.session import create_db_engine, create_session_factory
from survey.ingest.source import LocalDirectorySource
from survey.service.breakdown import BreakdownResult, breakdown_average, breakdown_proportion
from survey.service.crosstab import CrosstabResult, crosstab, format_crosstab_cell
from survey.service.distributions import (
    DistributionResult,
    GroupedDistribution,
    read_grouped_distribution,
    read_overall_distribution,
)
from survey.service.refresh import RefreshSummary, refresh


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _print_measures() -> None:
    print("Measures")
    rows = [
        [
            measure,
            "distribution, average, proportion" if measure in NUMERIC_MEASURES else "distribution",
        ]
        for measure in sorted(MEASURE_COLUMNS)
    ]
    _print_table(["measure", "aggregations"], rows)


def _print_dimensions() -> None:
    print("Dimensions")
    _print_table(["dimension"], [[dimension] for dimension in sorted(DIMENSION_COLUMNS)])


def _print_overall_distribution(result: DistributionResult) -> None:
    print(f"Distribution of {result.measure} (overall, n={result.n})")
    _print_table(
        ["response_value", "count"], [[b.response_value, str(b.count)] for b in result.bins]
    )


def _print_grouped_distribution(grouped: GroupedDistribution) -> None:
    print(f"Distribution of {grouped.measure} by {grouped.dimension}")
    for group in grouped.groups:
        print(f"  {group.group_value} (n={group.n})")
        _print_table(
            ["  response_value", "count"],
            [[f"  {b.response_value}", str(b.count)] for b in group.bins],
        )


def _print_breakdown(result: BreakdownResult) -> None:
    title = f"Breakdown of {result.measure} by {result.dimension} ({result.agg}"
    if result.agg == "proportion":
        title += f" >= {result.threshold}"
    print(title + ")")
    _print_table(
        [result.dimension, "value", "n"],
        [[c.group_value, f"{c.value:.2f}", str(c.n)] for c in result.cells],
    )


def _print_crosstab(result: CrosstabResult) -> None:
    by_cell = {(c.row_value, c.col_value): c for c in result.cells}
    agg_label = result.agg + (f" >= {result.threshold}" if result.agg == "proportion" else "")
    print(f"Cross-tab of {result.measure} ({agg_label}): rows = {result.row}, cols = {result.col}")
    header = [result.row, *result.col_values]
    rows = [
        [rv, *[format_crosstab_cell(by_cell[(rv, cv)]) for cv in result.col_values]]
        for rv in result.row_values
    ]
    _print_table(header, rows)
    print(
        "Legend: value [n=respondents]; (low) = below reliability threshold; n/a = no respondents"
    )


def _print_refresh(summary: RefreshSummary) -> None:
    print("Refresh complete:")
    print(f"  files_processed: {summary.files_processed}")
    print(f"  rows_ingested:   {summary.rows_ingested}")
    print(f"  rows_dropped:    {summary.rows_dropped}")
    print(f"  drop_reasons:    {summary.drop_reasons or '{}'}")
    print(f"  tables_rebuilt:  {', '.join(summary.tables_rebuilt)}")
    print(f"  duration_ms:     {summary.duration_ms}")


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="survey", description="Survey insights CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    commands: dict[str, argparse.ArgumentParser] = {}

    commands["measures"] = subparsers.add_parser(
        "measures", help="List the measures you can aggregate."
    )
    commands["dimensions"] = subparsers.add_parser(
        "dimensions", help="List the dimensions you can break down by."
    )

    distribution = subparsers.add_parser("distribution", help="Show a measure's distribution.")
    distribution.add_argument("--measure", required=True)
    distribution.add_argument("--by", default=None, help="Group by a dimension.")
    commands["distribution"] = distribution

    breakdown = subparsers.add_parser("breakdown", help="One-dimensional breakdown of a measure.")
    breakdown.add_argument("--measure", required=True)
    breakdown.add_argument("--by", required=True)
    breakdown.add_argument(
        "--agg", choices=["average", "proportion", "distribution"], default="average"
    )
    breakdown.add_argument("--threshold", type=int, default=4)
    commands["breakdown"] = breakdown

    crosstab_cmd = subparsers.add_parser("crosstab", help="Two-dimensional cross-tab of a measure.")
    crosstab_cmd.add_argument("--measure", required=True)
    crosstab_cmd.add_argument("--row", required=True)
    crosstab_cmd.add_argument("--col", required=True)
    crosstab_cmd.add_argument("--agg", choices=["average", "proportion"], default="average")
    crosstab_cmd.add_argument("--threshold", type=int, default=4)
    commands["crosstab"] = crosstab_cmd

    commands["refresh"] = subparsers.add_parser(
        "refresh", help="Re-ingest the configured source and rebuild tables."
    )
    return parser, commands


def _accepted_flags(subparser: argparse.ArgumentParser) -> str:
    """The flags a subcommand accepts, read from the subparser (never hardcoded)."""
    parts: list[str] = []
    for action in subparser._actions:
        if not action.option_strings or "--help" in action.option_strings:
            continue
        flag = action.option_strings[-1]
        parts.append(f"{flag} (required)" if action.required else flag)
    return ", ".join(parts) if parts else "(this command takes no flags)"


def _fail_unrecognized(
    command: str, extras: Sequence[str], subparser: argparse.ArgumentParser
) -> NoReturn:
    print(f"Error: the '{command}' command does not accept: {' '.join(extras)}", file=sys.stderr)
    print(f"Accepted flags: {_accepted_flags(subparser)}", file=sys.stderr)
    print(f"Try: survey {command} --help", file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> None:
    parser, commands = _build_parser()
    args, extras = parser.parse_known_args(argv)
    if extras:
        command = getattr(args, "command", None)
        if isinstance(command, str) and command in commands:
            _fail_unrecognized(command, extras, commands[command])
        parser.error(f"unrecognized arguments: {' '.join(extras)}")  # safety net

    # Discovery commands read the allowlist only; they need no database.
    if args.command == "measures":
        _print_measures()
        return
    if args.command == "dimensions":
        _print_dimensions()
        return

    engine = create_db_engine()
    session_factory = create_session_factory(engine)

    if args.command == "distribution":
        with session_factory() as session:
            if args.by is None:
                _print_overall_distribution(read_overall_distribution(session, args.measure))
            else:
                _print_grouped_distribution(
                    read_grouped_distribution(session, args.measure, args.by)
                )
    elif args.command == "breakdown":
        with session_factory() as session:
            if args.agg == "distribution":
                _print_grouped_distribution(
                    read_grouped_distribution(session, args.measure, args.by)
                )
            elif args.agg == "average":
                _print_breakdown(breakdown_average(session, args.measure, args.by))
            else:
                _print_breakdown(
                    breakdown_proportion(session, args.measure, args.by, args.threshold)
                )
    elif args.command == "crosstab":
        with session_factory() as session:
            result = crosstab(
                session,
                args.measure,
                args.row,
                args.col,
                get_settings().min_reliable_n,
                agg=args.agg,
                threshold=args.threshold,
            )
        _print_crosstab(result)
    elif args.command == "refresh":
        settings = get_settings()
        if not settings.source_dir:
            parser.error("No SOURCE_DIR configured; nothing to refresh.")
        _print_refresh(refresh(LocalDirectorySource(settings.source_dir), session_factory))


if __name__ == "__main__":
    main()
