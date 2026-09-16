"""SQLite price cache for broad-universe research.

The live pipeline fetches a ~20-name watchlist per run and that is fine. Research
over a 500-name universe is a different problem: refetching 1.25M bars for every
experiment is untenable, and ``.info`` — 78% of a pipeline run's wall clock — is
*today's* data and therefore worthless for history. So research reads bars from
here, fetched once and refreshed incrementally.

Stdlib only (``sqlite3``), matching the same choice made for thesis storage: no
pyarrow, no ORM. One file, one table::

    bars(ticker, date, open, high, low, close, volume)
    PRIMARY KEY (ticker, date)   WITHOUT ROWID

``WITHOUT ROWID`` with that composite key stores rows clustered by ticker, so
loading one name is a range scan rather than an index hop per row. Writes are
``INSERT OR REPLACE``, so re-ingesting an overlapping window is idempotent and a
partial fetch can simply be re-run.

**Adjustment contract:** bars written here must come from the same
``auto_adjust=True``, tz-naive path as :func:`stockanalysis.ingest.fetch_stock_data`
(see :func:`stockanalysis.ingest.fetch_bulk_prices`). A cache built on a different
adjustment convention would silently measure prices the live pipeline never sees.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

from . import config

log = logging.getLogger(__name__)

#: OHLCV column contract, in the order add_indicators and the charts expect.
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
) WITHOUT ROWID;
"""


def connect(path=None) -> sqlite3.Connection:
    """Open (creating if needed) the cache database and ensure its schema.

    ``path`` defaults to :data:`stockanalysis.config.DEFAULT_CACHE_DB`; pass
    ``":memory:"`` for tests.
    """
    path = str(config.DEFAULT_CACHE_DB if path is None else path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def upsert_bars(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Write an OHLCV frame for ``ticker``; returns the number of rows written.

    Idempotent on ``(ticker, date)`` — re-ingesting an overlapping window updates
    in place rather than duplicating, so a refresh can safely re-fetch a few days
    of tail overlap. A frame that is None/empty or missing the column contract
    writes nothing and returns 0 (NaN means fail, never crash).
    """
    if df is None or df.empty or not set(COLUMNS).issubset(df.columns):
        return 0
    idx = pd.to_datetime(df.index)
    rows = [
        (ticker, d.strftime("%Y-%m-%d"), *(None if pd.isna(v) else float(v)
                                           for v in r))
        for d, r in zip(idx, df[COLUMNS].to_numpy())
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO bars "
            "(ticker, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            rows)
    return len(rows)


def load_bars(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    """Read one ticker's history back as an OHLCV frame with a DatetimeIndex.

    Returns an empty frame (with the right columns) when the ticker isn't cached,
    so callers can treat it like any other degraded fetch.
    """
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM bars "
        "WHERE ticker = ? ORDER BY date", (ticker,))
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["date"] + COLUMNS)
    df.index = pd.to_datetime(df.pop("date"))
    df.index.name = None
    return df


def load_universe(conn: sqlite3.Connection, tickers=None) -> dict:
    """``{ticker: OHLCV frame}`` — the shape the backtest consumes.

    ``tickers`` defaults to everything cached. Names with no cached bars are
    omitted rather than yielding empty frames, so downstream length checks behave.
    """
    tickers = cached_tickers(conn) if tickers is None else list(tickers)
    out = {}
    for tk in tickers:
        df = load_bars(conn, tk)
        if not df.empty:
            out[tk] = df
    return out


def cached_tickers(conn: sqlite3.Connection) -> list[str]:
    """Every ticker with at least one cached bar, sorted."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM bars ORDER BY ticker")]


def last_date(conn: sqlite3.Connection, ticker: str):
    """Newest cached bar date for ``ticker`` as a Timestamp, or None.

    The anchor for an incremental refresh: fetch from here (minus a few days of
    overlap, since the upsert is idempotent) rather than refetching all history.
    """
    row = conn.execute("SELECT MAX(date) FROM bars WHERE ticker = ?",
                       (ticker,)).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] else None


def coverage(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per-ticker row count and date span — a cheap 'what do I actually have?'."""
    rows = conn.execute(
        "SELECT ticker, COUNT(*), MIN(date), MAX(date) FROM bars "
        "GROUP BY ticker ORDER BY ticker").fetchall()
    return pd.DataFrame(rows, columns=["Ticker", "Bars", "From", "To"])
