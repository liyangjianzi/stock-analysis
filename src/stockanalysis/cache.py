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
