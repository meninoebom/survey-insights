# Survey Insights System

Turns a survey CSV into fast, repeatable, **canonical** insights. Researchers ask
"how do people feel about AI, and does it vary by region, education, income, age?"
and, instead of hand-building pivots that go stale on every data refresh, get
instant breakdowns. The headline capability is **instant two-dimensional
cross-tabs** with a respondent count (`n`) in every cell.

Architecture, data flow, scaling, and the proposed production setup live in
**[`DESIGN.md`](DESIGN.md)**.

## Quick start (one command)

Requires Docker. From the repo root:

```bash
docker compose up
```

That builds the app, starts Postgres, creates the tables, seeds a persistent named
volume with the bundled sample `us_ai_survey_unique_50.csv`, and ingests it on boot.
Then:

- **Web UI** (primary): http://localhost:8000/ui/ (the root redirects there)
- **API**: same origin, http://localhost:8000 (`curl localhost:8000/health` returns `{"status":"ok"}`)

In the UI, pick a measure and one or two dimensions in the left rail to move between
distribution, breakdown, and the headline cross-tab. Drag a CSV onto the page to
replace the data. The browser reads only the API and recomputes nothing.

## Using it

The primary interface is a read-only **web UI** that consumes the HTTP **API**; both
return aggregated insights, never raw rows.

- **Measures** (what you aggregate): `q1_rating`, `q2_rating`, `q4_rating`
  (integer 1-5) and `sentiment_label` (`Positive` / `Neutral` / `Negative`).
- **Dimensions** (what you break down by): `state`, `gender`, `education_level`,
  `income`, `age_bucket`.

### Web UI (primary)

A single static page (React from a CDN, no build step) at http://localhost:8000/ui/.
One adaptive canvas: choose a **measure**, then **Split by** one dimension and
optionally **And by** a second, to move from distribution to breakdown to cross-tab.
The cross-tab grid shows three honest cell states: populated (value + `n`), low
reliability (`n` < 30, hatched and off the color scale), and no respondents (`n/a`,
never a zero). Every average and proportion shows its `n`. It reads its menus from
`GET /meta`, so the UI cannot drift from the server allowlist.

### Repopulate with a CSV

Replace the whole dataset without the terminal: drag a CSV onto the window, click
**Upload CSV**, or `POST /ingest`. **Use sample data** (or `POST /ingest/sample`)
restores the bundled 50-row sample. Uploads are saved on a persistent volume with
sortable timestamped names and the **newest wins**, so data survives restarts, and an
indicator names what is loaded. An upload carries the file's **contents**, never a
server path, and is **atomic**: a bad CSV is rejected with a `4xx` and the previous
data is left intact. (`docker compose down -v` discards uploads and re-seeds the sample.)

### API (curl)

```bash
# What the web UI reads to build its menus (measures + which are numeric, dimensions)
curl "localhost:8000/meta"

# Distribution of a rating, overall; add &by=<dimension> to group (works for sentiment)
curl "localhost:8000/distribution?measure=q1_rating"
curl "localhost:8000/distribution?measure=sentiment_label&by=gender"

# One-dimensional breakdown: average or proportion past a threshold (carries n per group)
curl "localhost:8000/breakdown?measure=q1_rating&by=education_level&agg=average"
curl "localhost:8000/breakdown?measure=q1_rating&by=income&agg=proportion&threshold=4"

# The headline: a live two-dimensional cross-tab with per-cell n and status
# (add &agg=proportion&threshold=4 for share-based cells instead of the mean)
curl "localhost:8000/crosstab?measure=q1_rating&row=education_level&col=gender"

# Re-ingest the newest source and rebuild everything (idempotent, atomic)
curl -X POST "localhost:8000/refresh"

# Replace the dataset by uploading a CSV's contents (raw body, no multipart); newest wins
curl -X POST --data-binary @my_survey.csv "localhost:8000/ingest"

# Restore the bundled 50-row sample
curl -X POST "localhost:8000/ingest/sample"
```

`average` / `proportion` on `sentiment_label` returns a clear 400 (sentiment is
distribution-only). An unknown measure or dimension returns a 400 listing the valid
options.

## Running the tests

```bash
mise run test
```

Spins a disposable Postgres, runs the full `pytest` suite against it, and tears it
down. `mise run check` adds format, lint, and type checks. Requires
[mise](https://mise.jdx.dev/), Docker, and [uv](https://docs.astral.sh/uv/); without
mise, the same suite runs via `bash scripts/test.sh`. Each test guards one
load-bearing invariant (canonical numbers, `n` travels with the statistic, cross-tab
corners, atomic idempotent refresh, allowlist enforcement), not coverage for its own
sake.

## Key decisions and tradeoffs

- **Canonical numbers.** Each statistic has one definition and carries its own `n`, so an
  average is never returned without it. The one-dimensional cuts (overall and breakdowns)
  derive `average`, `proportion`, and `n` from the precomputed per-response-value counts by
  one SQL expression. The two-dimensional cross-tab is the deliberate exception: a joint
  cell needs the joint detail, which the one-dimensional counts cannot reconstruct, so it
  computes the same mean live off `responses`. Same definition, applied to the joint grain.
- **Means of 1-5 ratings, with a proportion alternative.** Averaging a Likert rating treats
  the scale as if the gaps between points were equal (an ordinal-as-interval convention that
  is common but imperfect), so the `proportion` aggregation (share at or above a threshold)
  is offered alongside as the distribution-faithful cut, and `sentiment_label` is
  distribution-only.
- **Two tables at two grains.** `responses` (detail, source of truth) plus
  `distributions` (precomputed counts, the speed layer): common cuts are instant, while
  the rows stay for rebuilds and live cross-tabs (a two-way grouping needs the joint
  detail, which one-dimensional cuts cannot reconstruct).
- **Live cross-tabs off `responses`.** Milliseconds at this scale and general for any
  dimension pair, instead of materializing every combination.
- **The web UI consumes the API, so the API is the spine.** The other interfaces call the
  service layer in-process, which left the required HTTP API exercised only by tests and
  `curl`; a browser client of the endpoints makes it load-bearing. It reads its menu
  vocabulary from `GET /meta` and recomputes no statistic.
- **No SQL injection by construction.** Request-supplied measure / dimension names map
  through a fixed allowlist to real columns before any SQL is built.
- **SQLAlchemy model vs. typed dataclasses, not SQLModel.** The stored row and the
  computed insight are different shapes, so they are two small explicit types. Pydantic
  v2 (`pydantic-settings`) handles typed config.
- **Boring, portable stack.** Postgres + `GROUP BY`, `uv` for deps, stdlib `csv`
  (no pandas), one `docker compose up`.

## Performance: hours to seconds

The workflow this targets: a researcher fielding *"does AI optimism vary by education
and gender?"* hand-builds a pivot, waits, and redoes it on every data refresh. Here it
is one `crosstab` call returning a canonical grid with per-cell `n` in milliseconds. Hot
one-dimensional cuts are precomputed at ingest (a keyed read, no compute at request
time); cross-tabs run a single live `GROUP BY`, trivial at this scale. A reproducible,
opt-in benchmark (`scripts/benchmark.py`, off the reviewer's critical path like `mise`)
backs this up with p50/p95 timings against synthetic data at 10k/100k/1M rows, with the
numbers and machine context in [`docs/benchmarks.md`](docs/benchmarks.md). The full
10x/100x scaling analysis and production architecture are in [`DESIGN.md`](DESIGN.md).

## What I would improve given more time

- Move the synchronous refresh to a background job (the first thing that strains at ~10x).
- Type the API responses as Pydantic models for a published OpenAPI schema.
- Add the weighted-aggregate table (the one sufficient statistic a bare distribution
  lacks), so the system can produce population estimates, not just sample composition.

What I deliberately chose **not** to build, and why, is in [`DESIGN.md`](DESIGN.md).

## Configuration

Loaded from the environment via `pydantic-settings`, fail-fast on missing required
values. Docker Compose sets these; `.env.example` documents them for local runs.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | yes | none | PostgreSQL connection URL |
| `SOURCE_DIR` | no | none | writable upload directory; uploads land here (newest wins) and persist. The live dataset is the newest upload, else `BUNDLED_SAMPLE` |
| `BUNDLED_SAMPLE` | no | none | read-only fallback CSV, ingested when no upload is present and restored by "Use sample data" |
| `MIN_RELIABLE_N` | no | `30` | cross-tab cells below this are flagged low reliability (a quality flag, not privacy suppression) |
| `LLM_API_KEY` | no | none | optional, reserved for an out-of-scope report feature |

## Project layout

```
src/survey/
  config.py        typed settings (pydantic-settings)
  allowlist.py     measure/dimension -> column allowlist (SQL-injection guard)
  db/              SQLAlchemy models + engine/session
  ingest/          DataSource (newest-file-wins), validation, cleaning, responses write
  service/         the deep module: distributions, breakdown, crosstab, refresh, upload
  api/             FastAPI adapter (routes, deps, app + lifespan)
  cli/             argparse adapter
tests/             unit (no DB) + integration (disposable Postgres)
scripts/test.sh    test runner, wrapped by `mise run test`
```

## Other interfaces I explored

The web UI is the interface I shipped. Because the insight logic is a service layer of
pure functions, a new interface is mostly a thin adapter, so I explored a few others over
the same service layer and kept them in the repo for reference, not as the primary path.

- **CLI** (built). A thin `argparse` adapter that calls the service layer in-process, no
  running server needed. It mirrors the API and prints an aligned grid with `n` per cell,
  low-reliability cells flagged `(low)`, and empty cells as `n/a` (never an average of 0).

  ```bash
  docker compose exec app survey measures
  docker compose exec app survey crosstab --measure q1_rating --row education_level --col gender
  docker compose exec app survey refresh
  ```

- **TUI** (spiked). A Textual terminal UI, kept as an experiment. Run `./run-tui.sh` (one
  command, throwaway Postgres, supports drag-and-drop re-ingest; on a Mac, double-click
  `Explore Survey.command`), or `docker compose exec app survey-tui` inside a running stack.

- **MCP server** (considered, not built). Exposing the same service layer to an agent as
  tools and resources would be one more thin adapter; I scoped it but left it out of this build.
