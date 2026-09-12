"""Local on-disk cache of OHLCV price bars.

Bars are immutable history — yesterday's close does not change — so a run
should fetch only what it is missing. Each ticker gets one CSV under
``data/cache/prices/`` holding its **complete** history (a cold miss always
fetches ``period="max"``), and callers asking for ``3y`` / ``5y`` / ``max`` are
all served slices of that one file. Because every stored file is complete from
inception, coverage never has to be re-derived and no metadata sidecar exists.

This module never imports ``yfinance``: it takes a ``fetcher`` callable. That
keeps it testable entirely offline and lets any other fetch site adopt it
later. Every failure degrades (log + fall back) rather than raising.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)

#: Calendar days of already-cached bars to re-request alongside the new tail.
#: The overlap costs nothing extra and is what makes restatement detectable.
OVERLAP_DAYS = 5

#: Relative tolerance when comparing overlapping closes. A larger difference
#: means yfinance restated history (auto_adjust re-applying a split/dividend).
PRICE_RTOL = 1e-4

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(ticker: str) -> str:
    """Filesystem-safe filename stem for a ticker ('^GSPC' -> '_GSPC')."""
    return _UNSAFE.sub("_", ticker.strip().upper())


def cache_path(ticker: str, cache_dir=None) -> Path:
    """Path of ``ticker``'s cache file (defaults under DEFAULT_PRICE_CACHE_DIR)."""
    base = Path(cache_dir) if cache_dir is not None else config.DEFAULT_PRICE_CACHE_DIR
    return base / f"{_sanitize(ticker)}.csv"


def read_cache(ticker: str, cache_dir=None):
    """Return ``ticker``'s cached bars, or ``None`` if missing/corrupt/empty.

    A corrupt file is treated as a miss (logged, then overwritten by the next
    successful fetch) — never an exception.
    """
    path = cache_path(ticker, cache_dir)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=[0])
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "Date"
        return df.sort_index()
    except Exception as e:
        log.warning("%s: price cache unreadable (%s) — treating as a miss.", ticker, e)
        return None


def write_cache(ticker: str, df: pd.DataFrame, cache_dir=None) -> bool:
    """Atomically write ``df`` to ``ticker``'s cache file. Returns success.

    Writes to a temp file then ``os.replace`` (mirroring the thesis store), so
    an interrupted run can never leave a torn cache file. An unwritable cache
    directory costs speed, never correctness: warn and report False.
    """
    path = cache_path(ticker, cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="") as f:
                df.to_csv(f, index_label="Date")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception as e:
        log.warning("%s: could not write price cache (%s) — continuing uncached.",
                    ticker, e)
        return False


_PERIOD_RE = re.compile(r"^(\d+)\s*(d|wk|mo|y)$")

#: Calendar days per period unit. Deliberately generous (a month is 31 days, a
#: year 366) — erring long hands a caller a few extra bars, never fewer than it
#: asked for, which is the safe direction for indicator warm-up windows.
_DAYS_PER_UNIT = {"d": 1, "wk": 7, "mo": 31, "y": 366}


def _period_start(period, today=None):
    """Approximate earliest date ``period`` covers, or ``None`` for "everything".

    ``None`` is returned for ``"max"``, empty input, and anything unparseable —
    all of which mean "don't trim". Unrecognized input therefore fails safe.
    """
    today = (pd.Timestamp.now().normalize() if today is None
             else pd.Timestamp(today).normalize())
    p = (period or "").strip().lower()
    if p == "ytd":
        return pd.Timestamp(year=today.year, month=1, day=1)
    m = _PERIOD_RE.match(p)
    if not m:
        return None
    days = int(m.group(1)) * _DAYS_PER_UNIT[m.group(2)]
    return today - pd.Timedelta(days=days)


def _slice(df: pd.DataFrame, period, today=None) -> pd.DataFrame:
    """Trim a complete-history frame down to what ``period`` asked for."""
    start = _period_start(period, today)
    return df if start is None else df.loc[df.index >= start]


def _merge(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine cached and freshly fetched bars; fresh wins on duplicate dates."""
    combined = pd.concat([cached, fresh])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def _is_restated(cached: pd.DataFrame, fresh: pd.DataFrame, rtol: float = PRICE_RTOL) -> bool:
    """True when yfinance has retroactively restated the overlapping bars.

    ``fetch_stock_data`` fetches with ``auto_adjust=True``, so cached bars are
    split/dividend-adjusted *as of fetch time*. After a 2:1 split every older
    bar is restated at half its previous value; appending a fresh tail onto a
    stale base would open a 50% phantom gap mid-series and silently corrupt
    EMA20/50/200, RSI, ATR14, the regression channel and every technical score
    downstream — with no error anywhere. Comparing the overlap costs no extra
    request and catches exactly that.

    No overlapping dates means nothing to compare, so this reports False.
    """
    shared = cached.index.intersection(fresh.index)
    if len(shared) == 0 or "Close" not in cached.columns or "Close" not in fresh.columns:
        return False
    old = cached.loc[shared, "Close"].astype(float).to_numpy()
    new = fresh.loc[shared, "Close"].astype(float).to_numpy()
    return not np.allclose(old, new, rtol=rtol, atol=0.0, equal_nan=True)


def _call(fetcher, ticker: str, *, period=None, start=None):
    """Invoke ``fetcher`` defensively — a fetch error is a miss, not a crash."""
    try:
        df = fetcher(ticker, period=period, start=start)
    except Exception as e:
        log.warning("%s: price fetch failed (%s).", ticker, e)
        return None
    return None if df is None or df.empty else df


def _rebuild(ticker, period, fetcher, cache_dir, cached):
    """Re-fetch complete history and replace the cache.

    Falls back to the stale cached bars when the network gives us nothing, so a
    fully offline run still produces real (slightly stale) output.
    """
    full = _call(fetcher, ticker, period="max")
    if full is not None:
        write_cache(ticker, full, cache_dir)
        return _slice(full, period)
    log.warning("%s: full refetch failed — serving %d cached bars.", ticker, len(cached))
    return _slice(cached, period)


def cached_history(ticker: str, period, fetcher, cache_dir=None):
    """Return ``ticker``'s bars for ``period``, fetching only what's missing.

    ``fetcher(ticker, period=None, start=None) -> DataFrame | None`` does the
    actual network call; exactly one of ``period``/``start`` is ever passed.
    Returns ``None`` only when there is no cache and every fetch failed —
    matching the uncached behaviour callers already handle.
    """
    cached = read_cache(ticker, cache_dir)

    if cached is None:
        # Cold miss: always pull COMPLETE history so one file serves every
        # caller ('3y', '5y', 'max') without per-file coverage metadata.
        full = _call(fetcher, ticker, period="max")
        if full is not None:
            write_cache(ticker, full, cache_dir)
            return _slice(full, period)
        # Preserve the on-disk invariant: a partial fetch is returned, never stored.
        log.warning("%s: full-history fetch failed — falling back to an uncached "
                    "'%s' fetch.", ticker, period)
        return _call(fetcher, ticker, period=period)

    last = cached.index.max()
    fresh = _call(fetcher, ticker, start=last - pd.Timedelta(days=OVERLAP_DAYS))

    if fresh is None:
        # A failed or truncated tail is never merged in; refetch everything.
        log.warning("%s: tail fetch returned nothing — refetching full history.", ticker)
        return _rebuild(ticker, period, fetcher, cache_dir, cached)

    if _is_restated(cached, fresh):
        log.info("%s: cached bars were restated (split/dividend) — rebuilding cache.",
                 ticker)
        return _rebuild(ticker, period, fetcher, cache_dir, cached)

    merged = _merge(cached, fresh)
    write_cache(ticker, merged, cache_dir)
    return _slice(merged, period)
