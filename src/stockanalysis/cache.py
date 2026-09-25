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
The same holds *over time*: a dividend or split rescales every earlier bar, so a
top-up window is only upserted when :func:`drifted` says it still agrees with
the cache; otherwise the ticker is refetched whole and :func:`replace_bars`
swaps its history out in one transaction.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np
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


#: Relative Close tolerance for :func:`drifted`. Yahoo's float noise is ~1e-7; a
#: quarterly dividend re-adjustment is ~1e-3 (APH, 2026-09-24: x0.998451).
DRIFT_RTOL = 1e-4

_INSERT = ("INSERT OR REPLACE INTO bars "
           "(ticker, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)")


def _rows(ticker: str, df: pd.DataFrame):
    """``df`` as insert tuples, or None when it is None/empty/off-contract."""
    if df is None or df.empty or not set(COLUMNS).issubset(df.columns):
        return None
    idx = pd.to_datetime(df.index)
    return [
        (ticker, d.strftime("%Y-%m-%d"), *(None if pd.isna(v) else float(v)
                                           for v in r))
        for d, r in zip(idx, df[COLUMNS].to_numpy())
    ]


def upsert_bars(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Write an OHLCV frame for ``ticker``; returns the number of rows written.

    Idempotent on ``(ticker, date)`` — re-ingesting an overlapping window updates
    in place rather than duplicating, so a refresh can safely re-fetch a few days
    of tail overlap. A frame that is None/empty or missing the column contract
    writes nothing and returns 0 (NaN means fail, never crash).

    Only safe for a window on the *same* adjustment basis as the cache — check
    :func:`drifted` first; a full refetch goes through :func:`replace_bars`.
    """
    rows = _rows(ticker, df)
    if not rows:
        return 0
    with conn:
        conn.executemany(_INSERT, rows)
    return len(rows)


def replace_bars(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Rewrite ``ticker``'s whole history as ``df``; returns rows written.

    Unlike :func:`upsert_bars`, cached rows outside ``df`` are dropped: after a
    full refetch, any older bar was fetched on the previous adjustment basis and
    would sit as a stale stub at the start of history. Delete and insert share
    one transaction; a degenerate frame deletes nothing.
    """
    rows = _rows(ticker, df)
    if not rows:
        return 0
    with conn:
        conn.execute("DELETE FROM bars WHERE ticker = ?", (ticker,))
        conn.executemany(_INSERT, rows)
    return len(rows)


def drifted(conn: sqlite3.Connection, ticker: str, window: pd.DataFrame,
            rtol: float = DRIFT_RTOL) -> bool:
    """True when a freshly fetched ``window`` can't be stitched onto the cache.

    ``auto_adjust=True`` rescales every bar before a dividend/split ex-date, but a
    top-up only rewrites its own bars — upserting it would leave two adjustment
    bases with a step between them. A re-adjustment shows as the window's closes
    disagreeing with the cached closes on shared dates. The newest cached bar is
    excluded: it may have been fetched intraday and legitimately settled since.
    No shared bars at all (a refresh gap longer than the window) also counts —
    there is nothing to verify the basis against, and the upsert would leave a
    hole. An uncached ticker or an empty window is not drift.
    """
    if last_date(conn, ticker) is None or window is None or window.empty \
            or "Close" not in window:
        return False
    fresh = pd.Series(window["Close"].to_numpy(dtype=float),
                      index=pd.to_datetime(window.index).normalize())
    cached = load_bars(conn, ticker, start=fresh.index.min())["Close"].iloc[:-1]  # newest out
    both = pd.DataFrame({"cached": cached, "fresh": fresh}).dropna()   # shared dates
    return both.empty or not np.allclose(both["fresh"], both["cached"],
                                         rtol=rtol, atol=0.0)


def load_bars(conn: sqlite3.Connection, ticker: str, start=None) -> pd.DataFrame:
    """Read one ticker's history back as an OHLCV frame with a DatetimeIndex.

    ``start`` (any Timestamp-like) keeps bars on or after that date. Returns an
    empty frame (with the right columns) when nothing matches, so callers can
    treat it like any other degraded fetch.
    """
    since = "" if start is None else pd.Timestamp(start).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM bars "
        "WHERE ticker = ? AND date >= ? ORDER BY date", (ticker, since))
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


def refresh(conn: sqlite3.Connection, tickers, *, period: str = "10y",
            refresh_period: str = "1mo", full: bool = False,
            chunk: int = 50) -> dict:
    """Bring ``tickers`` up to date — the one safe way to write fetched bars.

    Incremental by default: uncached names fetch ``period`` of history, cached
    ones only a short ``refresh_period`` window, so a nightly run moves ~10k bars
    rather than 1.2M. A window is upserted only if :func:`drifted` says it still
    matches the cache; one that doesn't (a dividend/split re-adjustment, or a gap
    longer than the window) is refetched over ``period`` and swapped in whole by
    :func:`replace_bars`, never stitched onto the old basis. If that refetch
    fails the name keeps its old, consistent bars and is caught again next run.
    ``full`` refetches and replaces every name.

    Returns ``{"fetched", "topped_up", "rebuilt"}`` — sorted names that got bars
    by each route (fetch failures are logged by ``fetch_bulk_prices`` and simply
    absent) — plus ``"bars"`` written.
    """
    from . import ingest              # lazy: keeps yfinance off cache's import path
    known = set(cached_tickers(conn))
    fresh = list(tickers) if full else [t for t in tickers if t not in known]
    topup = [] if full else [t for t in tickers if t in known]

    whole: dict = {}
    windows: dict = {}
    rebuilt: dict = {}
    if fresh:
        log.info("Fetching %d ticker(s) over %s...", len(fresh), period)
        whole = ingest.fetch_bulk_prices(fresh, period=period, chunk=chunk)
    if topup:
        log.info("Topping up %d cached ticker(s) over %s...", len(topup), refresh_period)
        windows = ingest.fetch_bulk_prices(topup, period=refresh_period, chunk=chunk)
        rebase = sorted(t for t, df in windows.items() if drifted(conn, t, df))
        if rebase:
            log.info("Rebuilding %d ticker(s) whose top-up didn't match the cache "
                     "over %s: %s", len(rebase), period, ", ".join(rebase))
            windows = {t: df for t, df in windows.items() if t not in rebase}
            rebuilt = ingest.fetch_bulk_prices(rebase, period=period, chunk=chunk)

    bars = sum(replace_bars(conn, t, df) for t, df in {**whole, **rebuilt}.items())
    bars += sum(upsert_bars(conn, t, df) for t, df in windows.items())
    return {"fetched": sorted(whole), "topped_up": sorted(windows),
            "rebuilt": sorted(rebuilt), "bars": bars}
