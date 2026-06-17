# Survey Insights System: Design Document

## Problem and what I optimized for

Researchers answer stakeholder questions ("how do people feel about AI, and does it vary by
region, education, income, age?") by hand-building survey breakdowns, redoing them on every
data update, and assembling results by hand. That loop is manual, repeated, and inconsistent
across people.

This system precomputes the common breakdowns for instant reads, exposes a refresh that
recomputes everything, and defines each number once so it is canonical. The headline capability
is instant two-dimensional cross-tabs. I optimized for **read speed** (the hot path),
**simplicity and low operating cost** (boring Postgres, one `docker compose up`), and
**consistency** (one definition per number). **Scalability** I designed for as a path (see
Scaling) rather than building now, and I traded away breadth: fewer aggregations and one primary
interface, done well.

## Architecture

![Production architecture: the browser talks to FastAPI (Fly/Railway); uploads land in object storage (S3/R2) and an event triggers an ingest worker that writes cleaned detail and precomputed cubes to Postgres (Neon) atomically; FastAPI reads the cubes and runs live cross-tabs. A columnar engine (DuckDB, then a warehouse) engages only at scale.](docs/architecture.png)

*Local (`docker compose`) runs the same graph with local bindings: object storage becomes a
mounted volume, the ingest worker an in-process call, Neon a Docker Postgres, the columnar
engine a Postgres `GROUP BY`.*

- **DataSource**: CSVs read through an interface, so processing never knows where the bytes came
  from. A local-directory reader ships (newest file wins, never a concatenation); an `S3Source`
  is a one-class swap.
- **Ingestion** (Python): validates types, cleans, derives `age_bucket`, then computes the
  precomputed `distributions`.
- **PostgreSQL**, two grains: `responses` (cleaned detail, the source of truth) and
  `distributions` (precomputed per-value counts, the speed layer).
- **FastAPI**: exposes aggregated insights, never raw rows. `measure` and `dimension` names
  resolve through a fixed allowlist, so caller strings never reach SQL; an upload carries file
  contents, never a server path.
- **Web UI** (browser): the primary non-technical interface, a read-only client that consumes the
  HTTP API and recomputes nothing, which is what makes the API the load-bearing spine. A **CLI**
  is a thin adapter over the same service layer.

The insight logic is a service layer of pure typed functions; the API and CLI are thin adapters
over it.

## Data flow and storage

![Survey Insights data flow: a CSV's path from upload through ingestion, Postgres, and FastAPI to the read clients, annotated with the choices that make reads fast, the choices that make refresh repeatable, and the one swap that engages at 10x/100x behind an interface.](docs/data-flow.png)

A CSV lands (a non-technical user drags it onto the UI, which POSTs the contents). On startup or
`POST /refresh`: read via `DataSource`, validate types, normalize (derive `age_bucket`,
canonicalize categoricals, trim text), and drop and count bad rows. Cleaned rows go to
`responses`; then `distributions` are computed as per-value counts in long format (one row per
`measure`/`dimension`/`group_value`/`response_value`). Average, proportion, and `n` are not
stored: the one-dimensional cuts derive them from the distribution on read by one SQL expression,
since the counts are a sufficient statistic (`mean = Σ(value·count)/Σcount`).

**Storage choice.** Postgres, because the data is tabular and `GROUP BY` is the native operation
for every insight here; nothing exotic is justified at this scale. Two grains because the
precompute makes common reads instant, while the detail rows stay the source of truth for
rebuilds and for cross-tabs (a two-way grouping needs joint per-respondent detail; you cannot
average averages).

**Fast query performance.** Distributions and one-dimensional breakdowns read straight from the
precomputed table, with no compute at request time. Cross-tabs run one live `GROUP BY` on
`responses`, trivial at this scale and general for any dimension pair. Every average or proportion
carries its `n`; an empty cross-tab cell is distinct from a low-`n` one and is never shown as an
average of zero.

## Scaling

The prototype targets production scale, not 50 rows, and is built so the move to hundreds of
millions of rows is a swap of triggers and engines, not a rewrite. Refresh is an idempotent,
atomic full recompute (DELETE + INSERT in one transaction): correct up to a few hundred thousand
rows, and the property that makes every later tier a safe swap.

**What breaks first (~10x, low millions):** the recompute runs inside the HTTP request under a
process-local lock, so the request blocks for seconds (webhook senders time out and retry) and
the lock does not serialize across more than one API worker. **Change:** move ingest off the
request thread. `/ingest` and `/refresh` persist the bytes, enqueue a job, and return `202`; a
background worker runs the existing `rebuild_from_source`; the process lock becomes a Postgres
advisory lock; and a per-source content hash makes a redelivered upload a no-op.

**What changes for 100x (tens of millions):** full-recompute cost, and one Postgres doing both
ingest and live scans. Stream and batch the ingest (`DataSource` yields chunks; the detail write
becomes `COPY`); take the `S3Source` swap so a file landing in a bucket fires the event that
enqueues the job; recompute only the affected cuts; partition `responses` by time; and move heavy
aggregation to a columnar engine (DuckDB over Parquet first, then a warehouse such as BigQuery or
ClickHouse). Throughout: precompute the hot cross-tabs into the serving table, serve the long
tail live.

**Where bottlenecks emerge:** ingest first (the synchronous recompute), then the single Postgres
doing both writes and analytical scans.

## Production architecture

**Cloud services, storage, and compute (described, not deployed):** CSVs in **object storage (S3
or R2)**; a **FastAPI** service on **Fly or Railway**; **hosted Postgres (Neon)** for the detail
rows and the serving cubes; a **columnar engine** (DuckDB, then a warehouse) for heavy
aggregation; ingest run by a **background worker**. The pipeline code is the prototype's: only the
trigger, the execution model, the storage, and the aggregation engine swap in.

## What I chose not to build, and why

- **Auth / user management:** irrelevant to the analytical problem at this scale.
- **NLP on open text:** `sentiment_label` is provided; aggregating it delivers the insight at a
  fraction of the complexity.
- **`city` / `zip` as dimensions:** too high-cardinality; geography belongs at a state rollup.
- **Sampling weights:** real estimates are weighted, so these aggregates describe the sample, not
  the population; adding them is additive (store `(Σw, Σ(w·value))` per group).
- **Streaming / incremental refresh:** batch full-reprocess is trivially correct at this scale.
- **Three-way and higher cross-tabs:** the sample cannot support them statistically.
- **A heavy SPA frontend (build step):** the UI ships as a single static page (React from a CDN,
  no bundler) that consumes the API and recomputes nothing.
