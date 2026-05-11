# Stock Data Pipeline

Python + SQLite ETL that normalizes a denormalized stock-price CSV into a
relational schema and keeps it in sync across re-runs.

## Layout

```
scripts/       pipeline.py
test/          queries.py
data/          stock-data-se-owl.csv, stock-data-se-owl-part2.csv
db/            stock.db  (runtime, gitignored)
pyproject.toml project metadata (requires-python >=3.10)
uv.lock        pinned lockfile
```

## Installing uv

uv is a fast Python package and project manager. Install it once globally:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# macOS via Homebrew
brew install uv

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify the install:

```bash
uv --version
```

## Setup

Clone the repo and let uv create the virtual environment:

```bash
git clone <repo-url>
cd owl-pipeline
uv sync          # reads pyproject.toml + uv.lock, creates .venv
```

## Running the pipeline

```bash
# Load v1 CSV (default)
uv run python scripts/pipeline.py

# Load a specific CSV explicitly
uv run python scripts/pipeline.py data/stock-data-se-owl.csv

# Load v2 CSV — triggers mktcap_usd migration and applies Apple split adjustment
uv run python scripts/pipeline.py data/stock-data-se-owl-part2.csv
```

The pipeline is idempotent — re-running against the same file is always safe.

## Running the queries

```bash
uv run python test/queries.py
```

Prints three queries to stdout. Run the pipeline first so the DB exists.

### Query 1 — Cumulative price return per company

Finds the first and last closing price for each company across its full date
range, then computes `(last − first) / first × 100` as a percentage return.
Results are sorted highest return first.

Exercises a multi-table join (`stock_prices → companies → sectors`) and uses
two CTEs — `first_price` and `last_price` — to pin the open and close values
before joining them together.

### Query 2 — Average daily trading volume by sector

Groups every `stock_prices` row by sector, counts how many distinct companies
sit in each sector, and averages the daily volume across all companies and all
dates in that sector. Sorted highest volume first.

Useful for spotting which sectors are most actively traded — e.g. Technology
vs Consumer Cyclicals — without needing to look at individual companies.

### Query 3 — Latest market cap per company

For each company, finds the most recent date where `mktcap_usd` is not null
and returns that value converted to USD billions. Sorted largest cap first.

Only populated after loading the v2 CSV (which introduces the `mktcap_usd`
column). If the v2 data hasn't been loaded yet, the query prints a notice
instead of an empty table.

Requires Python 3.10+. No third-party runtime dependencies — `uv sync` only
creates the virtual environment; the pipeline itself uses the stdlib only.

## Schema

```
sectors      id | sector_level1 | sector_level2
companies    id | name | sector_id →sectors
stock_prices company_id →companies | asof | volume | close_usd | mktcap_usd
```

The denormalized CSV is a Cartesian product of company metadata and daily
prices. Splitting it into three tables removes the repeated sector and company
strings from every price row and makes joins natural.

## Idempotency

- `sectors` / `companies`: `INSERT OR IGNORE` — reference data that never
  changes.
- `stock_prices`: `INSERT … ON CONFLICT DO UPDATE SET volume, close_usd, mktcap_usd` — the
  natural key is `(company_id, asof)`.  Re-running against the same or an
  updated CSV replaces stale values while leaving untouched rows alone.

## Example queries (`test/queries.py`)

| Query | What it shows |
|---|---|
| `cumulative_return` | (last close − first close) / first close per company; joins all three tables |
| `avg_daily_volume_by_sector` | average daily volume grouped by sector |
| `latest_market_cap` | most recent market cap per company (populated after v2 load) |

## v2 changes (Commit 2)

**New column — `mktcap_usd`**

`_DDL` now includes `mktcap_usd REAL` so fresh installs get it immediately.
`_migrate()` runs unconditionally at startup and issues
`ALTER TABLE stock_prices ADD COLUMN mktcap_usd REAL` if the column is absent —
covering any DB created by Commit 1.  No manual migration step required.

**Backfill strategy**

The v2 CSV ships every historical row with `mktcap_usd` already populated.
The upsert uses `COALESCE(excluded.mktcap_usd, stock_prices.mktcap_usd)` so:
- rows with a value in the new CSV get the new value;
- rows without one (e.g. a partial re-export) keep whatever was already stored.

**Apple 2-for-1 split adjustment**

The v2 CSV contains adjusted close prices (halved) and volumes (doubled) for
every Apple row.  Because `stock_prices` is keyed on `(company_id, asof)` and
the upsert unconditionally overwrites `volume` and `close_usd`, re-running the
pipeline against the v2 CSV propagates all 6 244 corrections in one pass with
no special-casing.

## Scale notes

- **Ingest throughput**: replace row-by-row `executemany` with PostgreSQL
  `COPY` or a bulk staging table + `MERGE` (`INSERT … ON CONFLICT`).  SQLite
  WAL mode already batches writes, so the current approach handles millions of
  rows in seconds locally.
- **Schema migrations**: adopt Alembic (or Flyway for PG) to version each
  `ALTER TABLE`; the current `PRAGMA table_info` guard is fine for a small
  pipeline but doesn't track migration history.
- **Partitioning**: partition `stock_prices` by year or company for queries
  that scan large date ranges.
- **Indexes**: add `CREATE INDEX ON stock_prices (asof)` and
  `(company_id, asof DESC)` once query patterns are known.
- **Audit trail**: instead of in-place updates, append new rows with a
  `loaded_at` timestamp and keep the latest with a view — useful when you need
  to answer "what did we think the price was on date X?"
