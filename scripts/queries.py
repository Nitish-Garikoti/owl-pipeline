#!/usr/bin/env python3
"""
Example queries against the normalized stock schema.

Run after pipeline.py has loaded data:
    uv run python scripts/pipeline.py
    uv run python scripts/queries.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "stock.db"


def cumulative_return(conn: sqlite3.Connection) -> None:
    """
    Cumulative price return per company over the full history.
    Exercises a multi-table join (stock_prices → companies → sectors)
    and aggregation (first / last close per company).
    """
    rows = conn.execute("""
        WITH bounds AS (
            SELECT company_id,
                   MIN(asof) AS first_date,
                   MAX(asof) AS last_date
            FROM   stock_prices
            GROUP  BY company_id
        ),
        first_price AS (
            SELECT sp.company_id, sp.close_usd
            FROM   stock_prices sp
            JOIN   bounds b ON b.company_id = sp.company_id
                           AND sp.asof      = b.first_date
        ),
        last_price AS (
            SELECT sp.company_id, sp.close_usd
            FROM   stock_prices sp
            JOIN   bounds b ON b.company_id = sp.company_id
                           AND sp.asof      = b.last_date
        )
        SELECT c.name,
               s.sector_level1,
               b.first_date,
               b.last_date,
               ROUND(fp.close_usd, 4)                                    AS first_close,
               ROUND(lp.close_usd, 4)                                    AS last_close,
               ROUND((lp.close_usd - fp.close_usd) / fp.close_usd * 100, 2) AS return_pct
        FROM   bounds b
        JOIN   first_price fp ON fp.company_id = b.company_id
        JOIN   last_price  lp ON lp.company_id = b.company_id
        JOIN   companies   c  ON c.id           = b.company_id
        JOIN   sectors     s  ON s.id           = c.sector_id
        ORDER  BY return_pct DESC
    """).fetchall()

    print("=== Cumulative Return Per Company ===")
    header = (
        f"{'Company':<25} {'Sector':<25} {'First Date':<12}"
        f" {'Last Date':<12} {'First $':>10} {'Last $':>10} {'Return':>9}"
    )
    print(header)
    print("-" * len(header))
    for name, sector, fd, ld, fc, lc, ret in rows:
        print(
            f"{name:<25} {sector:<25} {fd:<12} {ld:<12}"
            f" {fc:>10.4f} {lc:>10.4f} {ret:>8.2f}%"
        )


def avg_daily_volume_by_sector(conn: sqlite3.Connection) -> None:
    """Average daily trading volume grouped by sector."""
    rows = conn.execute("""
        SELECT s.sector_level1,
               s.sector_level2,
               COUNT(DISTINCT sp.company_id)  AS companies,
               ROUND(AVG(sp.volume), 0)        AS avg_daily_volume
        FROM   stock_prices sp
        JOIN   companies c ON c.id  = sp.company_id
        JOIN   sectors   s ON s.id  = c.sector_id
        GROUP  BY s.id
        ORDER  BY avg_daily_volume DESC
    """).fetchall()

    print("\n=== Average Daily Volume by Sector ===")
    for s1, s2, n, vol in rows:
        print(f"  {s1} / {s2:<28}  {n} co.  avg vol {vol:>16,.0f}")


def latest_market_cap(conn: sqlite3.Connection) -> None:
    """Most recent market cap per company (requires mktcap_usd from v2 CSV)."""
    rows = conn.execute("""
        SELECT c.name,
               s.sector_level1,
               sp.asof,
               ROUND(sp.mktcap_usd / 1e9, 2) AS mktcap_bn_usd
        FROM   stock_prices sp
        JOIN   companies c ON c.id = sp.company_id
        JOIN   sectors   s ON s.id = c.sector_id
        WHERE  sp.mktcap_usd IS NOT NULL
          AND  sp.asof = (
              SELECT MAX(asof) FROM stock_prices sp2
              WHERE  sp2.company_id  = sp.company_id
                AND  sp2.mktcap_usd IS NOT NULL
          )
        ORDER  BY sp.mktcap_usd DESC
    """).fetchall()

    if not rows:
        print("\n(mktcap_usd not yet loaded — run pipeline against v2 CSV first)")
        return

    print("\n=== Latest Market Cap Per Company (USD bn) ===")
    for name, sector, asof, mcap in rows:
        print(f"  {name:<25} {sector:<25} as of {asof}  ${mcap:>10,.2f}bn")


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cumulative_return(conn)
        avg_daily_volume_by_sector(conn)
        latest_market_cap(conn)


if __name__ == "__main__":
    main()
