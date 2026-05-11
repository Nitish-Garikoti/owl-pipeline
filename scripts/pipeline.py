#!/usr/bin/env python3
"""
ETL pipeline: loads stock CSV into a normalized SQLite schema.

Schema
------
  sectors      – unique (sector_level1, sector_level2) pairs
  companies    – one row per company, FK → sectors
  stock_prices – one row per (company, date), FK → companies

Idempotency
-----------
  sectors / companies : INSERT OR IGNORE (static reference data)
  stock_prices        : INSERT … ON CONFLICT DO UPDATE SET volume, close_usd
                        → price corrections and split adjustments propagate
                          automatically on re-run
"""

import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
DB_PATH = _ROOT / "db" / "stock.db"
DEFAULT_CSV = _ROOT / "data" / "stock-data-se-owl.csv"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str:
    """Normalize M/D/YY or YYYY-MM-DD → YYYY-MM-DD."""
    raw = raw.strip()
    for fmt in ("%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date: {raw!r}")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS sectors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_level1 TEXT NOT NULL,
    sector_level2 TEXT NOT NULL,
    UNIQUE (sector_level1, sector_level2)
);

CREATE TABLE IF NOT EXISTS companies (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    sector_id INTEGER NOT NULL REFERENCES sectors (id)
);

CREATE TABLE IF NOT EXISTS stock_prices (
    company_id INTEGER NOT NULL REFERENCES companies (id),
    asof       TEXT    NOT NULL,
    volume     INTEGER,
    close_usd  REAL,
    PRIMARY KEY (company_id, asof)
);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def _load(conn: sqlite3.Connection, csv_path: Path) -> int:
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    # sectors
    conn.executemany(
        "INSERT OR IGNORE INTO sectors (sector_level1, sector_level2) VALUES (?, ?)",
        {(r["sector_level1"].strip(), r["sector_level2"].strip()) for r in rows},
    )
    sector_id: dict[tuple[str, str], int] = {
        (s1, s2): sid
        for sid, s1, s2 in conn.execute(
            "SELECT id, sector_level1, sector_level2 FROM sectors"
        )
    }

    # companies
    conn.executemany(
        """
        INSERT INTO companies (name, sector_id) VALUES (?, ?)
        ON CONFLICT (name) DO UPDATE SET sector_id = excluded.sector_id
        """,
        {
            r["name"].strip(): sector_id[
                (r["sector_level1"].strip(), r["sector_level2"].strip())
            ]
            for r in rows
        }.items(),
    )
    company_id: dict[str, int] = {
        name: cid
        for cid, name in conn.execute("SELECT id, name FROM companies")
    }

    # stock prices – upsert so corrections / split-adjustments propagate
    conn.executemany(
        """
        INSERT INTO stock_prices (company_id, asof, volume, close_usd)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (company_id, asof) DO UPDATE SET
            volume    = excluded.volume,
            close_usd = excluded.close_usd
        """,
        [
            (
                company_id[r["name"].strip()],
                _parse_date(r["asof"]),
                int(r["volume"])       if r["volume"]    else None,
                float(r["close_usd"]) if r["close_usd"] else None,
            )
            for r in rows
        ],
    )

    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def run(csv_path: Path = DEFAULT_CSV) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _init_schema(conn)
        n = _load(conn, csv_path)
    print(f"Loaded {n:,} rows from '{csv_path.name}' → {DB_PATH.name}")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    run(path)
