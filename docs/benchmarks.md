# Performance benchmark

Reproducible numbers for the load and read paths, produced by `scripts/benchmark.py`
(opt-in, off the reviewer's critical path). The script spins a disposable Postgres,
generates synthetic survey CSVs that match the real schema at several sizes, and times
the **shipped service layer** unmodified, so these are the same functions the API and
CLI call. Cells are **p50 / p95**; load paths are in seconds, read paths in milliseconds.

## Machine and scale context

- **Machine:** Darwin 23.6.0 (arm64)
- **CPU:** Apple M1 Pro, 10 logical cores
- **Python:** 3.12.13
- **Postgres:** PostgreSQL 16.14 (Debian 16.14-1.pgdg13+1) (Docker `postgres:16`, localhost)
- **Dataset sizes:** 10,000, 100,000, 1,000,000 rows
- **Runs:** load paths 3x, read paths 30x (seed 20260617)

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

| Rows | Ingest (s) | Refresh (s) | Distribution (ms) | Breakdown (ms) | Crosstab (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 0.50 / 0.53 | 0.54 / 0.55 | 1.02 / 1.64 | 0.77 / 1.65 | 5.31 / 6.22 |
| 100,000 | 5.13 / 5.85 | 5.76 / 5.76 | 1.23 / 4.10 | 1.25 / 2.08 | 41.30 / 48.46 |
| 1,000,000 | 51.87 / 53.99 | 59.24 / 63.95 | 2.50 / 9.84 | 1.70 / 3.81 | 192.43 / 206.66 |

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
