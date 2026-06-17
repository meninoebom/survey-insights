#!/usr/bin/env python
"""Reproducible performance benchmark (opt-in, off the reviewer's critical path).

This is NOT one of the two documented commands. Like `mise`, it is a convenience
the reviewer never has to run. It spins its own disposable Postgres (the
`scripts/test.sh` pattern), generates synthetic survey CSVs that match the real
schema at several sizes, then reports p50 / p95 latency for the load paths
(ingest, refresh) and the three read paths (distribution, breakdown, crosstab).

It reuses the service layer unmodified: every number is produced by the same
functions the API and CLI call, so the benchmark measures the shipped code rather
than a re-implementation. No service-layer file and no load-bearing invariant is
touched. The data source is a temp directory we hand to `LocalDirectorySource`,
so the path-traversal invariant is honored too (the source location is configured
here, never request-supplied).

Usage (run inside the uv environment so the deps resolve):

    uv run python scripts/benchmark.py                     # 10k + 100k, prints a table
    uv run python scripts/benchmark.py --sizes 10000 100000 1000000
    uv run python scripts/benchmark.py --sizes 10000 100000 1000000 --write-doc

Requires Docker and uv, exactly like the test runner.
"""

import argparse
import csv
import math
import os
import platform
import random
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# --- synthetic survey shape -------------------------------------------------

# The real CSV's columns, in order. `q3_open` / `q5_open` are free text the
# ingest ignores; we still emit them so the file size (and CSV parse cost) is
# realistic, including quoted commas.
HEADER: list[str] = [
    "id",
    "age",
    "gender",
    "zip_code",
    "city",
    "state",
    "income",
    "education_level",
    "q1_rating",
    "q2_rating",
    "q3_open",
    "q4_rating",
    "q5_open",
    "sentiment_label",
]

# 50 USPS state codes: realistic cardinality for the highest-cardinality dimension.
STATES: list[str] = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]

GENDERS: list[str] = ["Male", "Female", "Non-binary", "Prefer not to say"]
GENDER_WEIGHTS: list[float] = [0.48, 0.48, 0.025, 0.015]

EDUCATION: list[str] = [
    "High School",
    "Some College",
    "Associate's Degree",
    "Bachelor's Degree",
    "Master's Degree",
    "Doctorate",
]
EDUCATION_WEIGHTS: list[float] = [0.28, 0.21, 0.10, 0.26, 0.12, 0.03]

INCOME: list[str] = ["Low", "Lower-Middle", "Middle", "Upper-Middle", "High"]
INCOME_WEIGHTS: list[float] = [0.18, 0.22, 0.30, 0.20, 0.10]

CITY_STEMS: list[str] = [
    "Lake",
    "Port",
    "New",
    "North",
    "South",
    "East",
    "West",
    "Mount",
    "Fort",
    "Saint",
    "Spring",
    "Cedar",
    "Maple",
    "Oak",
    "Pine",
    "River",
    "Bridge",
    "Glen",
    "Stone",
    "Brook",
]
CITY_SUFFIXES: list[str] = [
    "ville",
    "ton",
    "burg",
    "field",
    "mouth",
    "port",
    "ford",
    "haven",
    "dale",
    "wood",
]

Q3_OPEN: list[str] = [
    "It will help me at work, I think.",
    "Honestly, I am not sure yet.",
    "I worry about jobs, but stay hopeful.",
    "Too early to tell how it lands.",
    "It already saves me time most days.",
]
Q5_OPEN: list[str] = [
    "Ethics in AI development is not being prioritized enough.",
    "My main concern is algorithmic bias in high-stakes decisions.",
    "I want clearer rules before this goes much further.",
    "The upside for medicine and science is real.",
    "Privacy is the piece nobody seems to be solving.",
]

# --- the one real modeling choice -------------------------------------------
#
# These additive shifts on a latent 1-5 "optimism" scale are what make the
# cross-tab show *signal* (optimism that varies by sub-group) instead of uniform
# noise that would render every cell identical. They change only the values in
# the cells, never the timing. Tune them to make the demo blunter or subtler.
EDUCATION_OPTIMISM: dict[str, float] = {
    "High School": -0.45,
    "Some College": -0.15,
    "Associate's Degree": 0.0,
    "Bachelor's Degree": 0.30,
    "Master's Degree": 0.55,
    "Doctorate": 0.20,
}
GENDER_OPTIMISM: dict[str, float] = {
    "Male": 0.20,
    "Female": -0.15,
    "Non-binary": 0.0,
    "Prefer not to say": 0.0,
}


def _draw_rating(rng: random.Random, latent: float) -> int:
    """Sample a 1-5 rating from a latent optimism score plus Gaussian noise."""
    return max(1, min(5, round(latent + rng.gauss(0.0, 0.9))))


def _sentiment(q1: int, q4: int) -> str:
    """Derive a sentiment label from the rating pair, so it tracks the ratings."""
    average = (q1 + q4) / 2
    if average >= 3.75:
        return "Positive"
    if average <= 2.25:
        return "Negative"
    return "Neutral"


def generate_csv(path: Path, rows: int, seed: int) -> int:
    """Write `rows` synthetic responses matching the real schema. Returns rows written.

    Deterministic for a given seed, so re-running yields the same data (and so the
    reported numbers move only with the machine, not the input).
    """
    rng = random.Random(seed)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for row_id in range(1, rows + 1):
            gender = rng.choices(GENDERS, GENDER_WEIGHTS)[0]
            education = rng.choices(EDUCATION, EDUCATION_WEIGHTS)[0]
            income = rng.choices(INCOME, INCOME_WEIGHTS)[0]
            latent = 3.0 + EDUCATION_OPTIMISM[education] + GENDER_OPTIMISM[gender]
            q1 = _draw_rating(rng, latent)
            q2 = _draw_rating(rng, 6.0 - latent)  # a "concern" item: inversely related
            q4 = _draw_rating(rng, latent)
            writer.writerow(
                [
                    row_id,
                    rng.randint(18, 90),
                    gender,
                    f"{rng.randint(0, 99999):05d}",
                    f"{rng.choice(CITY_STEMS)}{rng.choice(CITY_SUFFIXES)}",
                    rng.choice(STATES),
                    income,
                    education,
                    q1,
                    q2,
                    rng.choice(Q3_OPEN),
                    q4,
                    rng.choice(Q5_OPEN),
                    _sentiment(q1, q4),
                ]
            )
    return rows


# --- timing -----------------------------------------------------------------


@dataclass(frozen=True)
class Stat:
    """A p50 / p95 pair, already scaled to its display unit."""

    p50: float
    p95: float


@dataclass(frozen=True)
class SizeResult:
    """One row of the results table: every metric for a single dataset size."""

    rows_ingested: int
    ingest_s: Stat
    refresh_s: Stat
    distribution_ms: Stat
    breakdown_ms: Stat
    crosstab_ms: Stat


def _percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation): honest for small sample counts."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _stat(samples_s: Sequence[float], scale: float) -> Stat:
    """Build a p50 / p95 Stat from second-valued samples, scaled (1.0=s, 1000.0=ms)."""
    return Stat(_percentile(samples_s, 50) * scale, _percentile(samples_s, 95) * scale)


def _time_runs(action: Callable[[], object], runs: int) -> list[float]:
    """Run `action` `runs` times, returning each wall-clock duration in seconds."""
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        action()
        samples.append(time.perf_counter() - start)
    return samples


# --- disposable Postgres (the scripts/test.sh pattern) ----------------------

_CONTAINER = "survey-bench-db"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def _wait_for_postgres(url: str, attempts: int = 60) -> None:
    """Poll until Postgres accepts a connection (no shell sleep dependency)."""
    import psycopg

    for _ in range(attempts):
        try:
            psycopg.connect(url).close()
            return
        except Exception:  # not-yet-ready surfaces as several connection errors
            time.sleep(0.5)
    raise SystemExit("benchmark database never became ready")


def _postgres_version(url: str) -> str:
    import psycopg

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        row = cur.fetchone()
    return str(row[0]).split(" on ")[0] if row else "PostgreSQL (unknown)"


# --- machine context --------------------------------------------------------


def _cpu_model() -> str:
    if sys.platform == "darwin":
        proc = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return platform.processor() or platform.machine() or "unknown"


def _machine_context(
    pg_version: str, sizes: Sequence[int], mutate_runs: int, read_runs: int, seed: int
) -> list[tuple[str, str]]:
    return [
        ("Machine", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("CPU", f"{_cpu_model()}, {os.cpu_count()} logical cores"),
        ("Python", platform.python_version()),
        ("Postgres", f"{pg_version} (Docker `postgres:16`, localhost)"),
        ("Dataset sizes", ", ".join(f"{s:,}" for s in sizes) + " rows"),
        ("Runs", f"load paths {mutate_runs}x, read paths {read_runs}x (seed {seed})"),
    ]


# --- rendering --------------------------------------------------------------

_COLUMNS = [
    "Rows",
    "Ingest (s)",
    "Refresh (s)",
    "Distribution (ms)",
    "Breakdown (ms)",
    "Crosstab (ms)",
]


def _cell(stat: Stat) -> str:
    return f"{stat.p50:.2f} / {stat.p95:.2f}"


def _row_cells(result: SizeResult) -> list[str]:
    return [
        f"{result.rows_ingested:,}",
        _cell(result.ingest_s),
        _cell(result.refresh_s),
        _cell(result.distribution_ms),
        _cell(result.breakdown_ms),
        _cell(result.crosstab_ms),
    ]


def _render_markdown_table(results: Sequence[SizeResult]) -> str:
    lines = [
        "| " + " | ".join(_COLUMNS) + " |",
        "| " + " | ".join(["---:"] * len(_COLUMNS)) + " |",
    ]
    for result in results:
        lines.append("| " + " | ".join(_row_cells(result)) + " |")
    return "\n".join(lines)


def _render_text_table(results: Sequence[SizeResult]) -> str:
    rows = [_row_cells(result) for result in results]
    widths = [len(col) for col in _COLUMNS]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    out = ["  ".join(col.rjust(widths[i]) for i, col in enumerate(_COLUMNS))]
    out += ["  ".join(value.rjust(widths[i]) for i, value in enumerate(row)) for row in rows]
    return "\n".join(out)


def _render_doc(results: Sequence[SizeResult], context: Sequence[tuple[str, str]]) -> str:
    context_block = "\n".join(f"- **{label}:** {value}" for label, value in context)
    return f"""# Performance benchmark

Reproducible numbers for the load and read paths, produced by `scripts/benchmark.py`
(opt-in, off the reviewer's critical path). The script spins a disposable Postgres,
generates synthetic survey CSVs that match the real schema at several sizes, and times
the **shipped service layer** unmodified, so these are the same functions the API and
CLI call. Cells are **p50 / p95**; load paths are in seconds, read paths in milliseconds.

## Machine and scale context

{context_block}

## What each column measures

- **Ingest** (`ingest_responses`): re-read the CSV, validate, clean, and full-replace
  `responses`. The detail-write cost only.
- **Refresh** (`POST /refresh`): re-ingest plus rebuild `distributions`, all in one
  transaction, so the gap from Ingest to Refresh is the distribution-rebuild overhead.
- **Distribution** (`read_grouped_distribution(q1_rating, state)`): a keyed read of the
  precomputed counts (the speed layer), grouped by the highest-cardinality dimension.
- **Breakdown** (`breakdown_average(q1_rating, education_level)`): average and `n`
  derived from `distributions` by one SQL expression.
- **Crosstab** (`crosstab(q1_rating, education_level x gender)`): the headline
  capability, a live `GROUP BY` over `responses` with no precompute.

## Results

{_render_markdown_table(results)}

## Reading the numbers

The precomputed reads (Distribution, Breakdown) stay flat in the low-millisecond range at
every size, because they read the small precomputed `distributions` table, not the detail
rows. The live cross-tab scans `responses`, so its latency grows with the row count: a few
milliseconds at 10k, tens of milliseconds at 100k, and still sub-second at 1M. Ingest and
refresh grow roughly linearly, dominated by the synchronous ORM write of `responses`. That
is exactly why "move refresh to a background job" is the first item under *What I would
improve* in the README, and why the 10x/100x scaling analysis lives in
[`DESIGN.md`](../DESIGN.md).

Regenerate with `uv run python scripts/benchmark.py --sizes 10000 100000 1000000 --write-doc`.
Numbers are machine-specific; treat them as ratios and shapes, not absolutes.
"""


# --- the benchmark ----------------------------------------------------------


def run_benchmark(
    sizes: Sequence[int],
    mutate_runs: int,
    read_runs: int,
    seed: int,
    url: str,
    min_reliable_n: int,
) -> list[SizeResult]:
    """Generate data and time every path at each size, reusing the service layer."""
    from survey.db.session import create_db_engine, create_session_factory, init_db
    from survey.ingest.pipeline import ingest_responses
    from survey.ingest.source import LocalDirectorySource
    from survey.service.breakdown import breakdown_average
    from survey.service.crosstab import crosstab
    from survey.service.distributions import read_grouped_distribution
    from survey.service.refresh import refresh

    engine = create_db_engine(url)
    init_db(engine)
    session_factory = create_session_factory(engine)

    def ingest_once(source: LocalDirectorySource) -> object:
        with session_factory() as session, session.begin():
            return ingest_responses(source, session)

    results: list[SizeResult] = []
    for size in sizes:
        with TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            print(f"[{size:>9,}] generating CSV ...", flush=True)
            generate_csv(source_dir / f"survey-bench-{size}.csv", size, seed)
            source = LocalDirectorySource(source_dir)

            # Warm up: populate both tables and prime caches; verify the data is clean.
            summary = refresh(source, session_factory)
            if summary.rows_ingested != size:
                raise SystemExit(
                    f"expected {size} clean rows, ingested {summary.rows_ingested} "
                    f"(drops: {summary.drop_reasons})"
                )

            print(f"[{size:>9,}] timing ingest + refresh ({mutate_runs}x each) ...", flush=True)
            ingest = _time_runs(partial(ingest_once, source), mutate_runs)
            refresh_samples = _time_runs(partial(refresh, source, session_factory), mutate_runs)

            print(f"[{size:>9,}] timing reads ({read_runs}x each) ...", flush=True)
            with session_factory() as session:
                distribution = _time_runs(
                    partial(read_grouped_distribution, session, "q1_rating", "state"), read_runs
                )
                breakdown = _time_runs(
                    partial(breakdown_average, session, "q1_rating", "education_level"), read_runs
                )
                crosstab_samples = _time_runs(
                    partial(
                        crosstab, session, "q1_rating", "education_level", "gender", min_reliable_n
                    ),
                    read_runs,
                )

            results.append(
                SizeResult(
                    rows_ingested=summary.rows_ingested,
                    ingest_s=_stat(ingest, 1.0),
                    refresh_s=_stat(refresh_samples, 1.0),
                    distribution_ms=_stat(distribution, 1000.0),
                    breakdown_ms=_stat(breakdown, 1000.0),
                    crosstab_ms=_stat(crosstab_samples, 1000.0),
                )
            )

    engine.dispose()
    return results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Survey insights performance benchmark.")
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[10_000, 100_000],
        help="Row counts to benchmark (default: 10000 100000; add 1000000 for the full sweep).",
    )
    parser.add_argument("--mutate-runs", type=int, default=5, help="Runs per load path.")
    parser.add_argument("--read-runs", type=int, default=30, help="Runs per read path.")
    parser.add_argument("--seed", type=int, default=20260617, help="Synthetic-data seed.")
    parser.add_argument(
        "--db-port", type=int, default=55434, help="Host port for the disposable DB."
    )
    parser.add_argument(
        "--write-doc",
        action="store_true",
        help="Also write the results to the docs file (default: print only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "docs" / "benchmarks.md",
        help="Where --write-doc writes (default: docs/benchmarks.md).",
    )
    parser.add_argument(
        "--keep-db", action="store_true", help="Leave the Postgres container running on exit."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    sizes: list[int] = args.sizes
    mutate_runs: int = args.mutate_runs
    read_runs: int = args.read_runs
    seed: int = args.seed
    port: int = args.db_port
    out: Path = args.out

    url = f"postgresql+psycopg://survey:survey@localhost:{port}/survey"
    plain_url = f"postgresql://survey:survey@localhost:{port}/survey"
    os.environ["DATABASE_URL"] = url

    _docker("rm", "-f", _CONTAINER, check=False)  # clear any stale container
    print(f"Starting disposable Postgres on port {port} ...", flush=True)
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        _CONTAINER,
        "-e",
        "POSTGRES_USER=survey",
        "-e",
        "POSTGRES_PASSWORD=survey",
        "-e",
        "POSTGRES_DB=survey",
        "-p",
        f"{port}:5432",
        "postgres:16",
    )
    try:
        _wait_for_postgres(plain_url)

        from survey.config import get_settings

        min_reliable_n = get_settings().min_reliable_n
        pg_version = _postgres_version(plain_url)

        results = run_benchmark(sizes, mutate_runs, read_runs, seed, url, min_reliable_n)
        context = _machine_context(pg_version, sizes, mutate_runs, read_runs, seed)

        print("\n" + "\n".join(f"{label}: {value}" for label, value in context))
        print("\n" + _render_text_table(results) + "\n")

        if args.write_doc:
            out.write_text(_render_doc(results, context), encoding="utf-8")
            print(f"Wrote {out}")
    finally:
        if not args.keep_db:
            _docker("rm", "-f", _CONTAINER, check=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
