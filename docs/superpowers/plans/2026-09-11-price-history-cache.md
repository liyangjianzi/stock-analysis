# Local Price-History Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist fetched OHLCV bars to disk so a run fetches only the bars it is missing instead of re-downloading full price history every time.

**Architecture:** A new `src/stockanalysis/cache.py` wraps a caller-supplied *fetcher callable* — it never imports yfinance, so it is fully testable offline. One CSV per ticker under `data/cache/prices/` holds that ticker's **complete** history (a cold miss always fetches `period="max"`), and every caller is served a slice of it. Warm runs request only `last_cached_date − 5 days` onward; the re-fetched overlap is compared against the cached bars to detect split/dividend restatements and rebuild automatically. Only `ingest.fetch_stock_data` is wired up, which transitively covers `pipeline.run`, `backtest`, and `thesis.review`.

**Tech Stack:** Python ≥3.10, pandas, numpy, pytest. Standard library only for persistence (`csv` via pandas, `tempfile`, `os.replace`, `re`, `pathlib`). **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-09-11-price-history-cache-design.md`

## Global Constraints

- **No new dependencies.** Storage is CSV via pandas + stdlib. Do not add `pyarrow`, `pyyaml`, or a database driver.
- **`cache.py` must never import `yfinance`.** It takes a `fetcher` callable. This is what keeps the whole module offline-testable and reusable by `overview.fetch_index_data` later.
- **Degrade, never crash.** Every failure path (missing file, corrupt CSV, failed fetch, unwritable directory) logs and returns a usable value or `None`. The cache layer must never raise to its caller.
- **Complete-history invariant:** any file on disk holds the ticker's history from inception. Never write a cache file from a partial-period fetch — that is what removes the need for coverage metadata.
- **Tests are fully offline.** No network, no `yfinance` calls. Every cache test points `cache_dir` at pytest's `tmp_path`; the suite must never touch the real `data/cache/` directory.
- **Defaults preserve today's behaviour.** `use_cache=True`, `cache_dir=None` on every new parameter; no existing call site changes meaning.
- Index dtype is **tz-naive** `DatetimeIndex` named `Date`, matching what `fetch_stock_data` already normalizes to.
- Test style follows the repo: descriptive `test_<behaviour>` names, one behaviour per test, arrange/act/assert with a blank line before the assert.

---

### Task 1: Cache file storage primitives

Establishes where cached bars live and how they are read/written atomically. Nothing in this task fetches anything.

**Files:**
- Create: `src/stockanalysis/cache.py`
- Create: `tests/test_cache.py`
- Modify: `src/stockanalysis/config.py` (add `DEFAULT_PRICE_CACHE_DIR` after `DEFAULT_THESES_DIR`, ~line 29)
- Modify: `.gitignore` (add `data/cache/` beside the existing `data/theses/` entry)

**Interfaces:**
- Consumes: `config.DEFAULT_PRICE_CACHE_DIR` (created here).
- Produces: `cache.cache_path(ticker, cache_dir=None) -> Path`, `cache.read_cache(ticker, cache_dir=None) -> pd.DataFrame | None`, `cache.write_cache(ticker, df, cache_dir=None) -> bool`, `cache._sanitize(ticker) -> str`, and the module constants `cache.OVERLAP_DAYS = 5`, `cache.PRICE_RTOL = 1e-4`. Tasks 2–4 build on all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cache.py`:

```python
"""Tests for the local price-history cache — fully offline.

No network and no yfinance: cache.py takes a fetcher callable, so every test
either exercises pure file I/O or injects a recording fake fetcher. Every test
writes under pytest's tmp_path, never the real data/cache/ directory.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from stockanalysis import cache


def _frame(idx: pd.DatetimeIndex, first_close: float) -> pd.DataFrame:
    idx.name = "Date"
    close = pd.Series([first_close + i for i in range(len(idx))], index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1_000_000},
        index=idx,
    )


def bars(start: str = "2024-01-02", n: int = 5, first_close: float = 100.0) -> pd.DataFrame:
    """A tiny tz-naive OHLCV frame on business days, closes ascending by 1."""
    return _frame(pd.bdate_range(start, periods=n), first_close)


def recent_bars(n: int = 30, first_close: float = 100.0) -> pd.DataFrame:
    """Bars ENDING TODAY.

    Required by any test that exercises period slicing: ``cache._slice``
    measures its cutoff from today, not from the frame's last bar. Asking a
    2024-anchored fixture for '5d' correctly returns an empty frame — which is
    right behaviour and a wrong test.
    """
    return _frame(pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n),
                  first_close)


# --- storage primitives --------------------------------------------------------

def test_write_then_read_round_trips_bars(tmp_path):
    df = bars()

    cache.write_cache("AAPL", df, cache_dir=tmp_path)
    loaded = cache.read_cache("AAPL", cache_dir=tmp_path)

    pd.testing.assert_frame_equal(loaded, df, check_freq=False)


def test_read_cache_returns_none_when_file_missing(tmp_path):
    assert cache.read_cache("NOPE", cache_dir=tmp_path) is None


def test_read_cache_returns_none_for_corrupt_file(tmp_path, caplog):
    path = cache.cache_path("AAPL", cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this,is not\na valid\x00 csv frame")

    # A corrupt cache is a miss, never an exception.
    assert cache.read_cache("AAPL", cache_dir=tmp_path) is None


def test_read_cache_returns_none_for_empty_frame(tmp_path):
    cache.write_cache("AAPL", bars(n=0), cache_dir=tmp_path)

    assert cache.read_cache("AAPL", cache_dir=tmp_path) is None


def test_cache_path_sanitizes_unsafe_characters(tmp_path):
    # '^' (index symbols) and '/' must never reach the filesystem as-is.
    path = cache.cache_path("^GSPC", cache_dir=tmp_path)

    assert path.name == "_GSPC.csv"
    assert path.parent == tmp_path


def test_cache_path_keeps_dots_so_tsx_tickers_stay_readable(tmp_path):
    assert cache.cache_path("su.to", cache_dir=tmp_path).name == "SU.TO.csv"


def test_write_cache_leaves_no_temp_files_behind(tmp_path):
    cache.write_cache("AAPL", bars(), cache_dir=tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == ["AAPL.csv"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores directory permissions")
def test_write_cache_returns_false_when_directory_is_unwritable(tmp_path):
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        # A read-only disk costs speed, never correctness: warn and move on.
        assert cache.write_cache("AAPL", bars(), cache_dir=ro / "prices") is False
    finally:
        ro.chmod(0o700)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockanalysis.cache'`

- [ ] **Step 3: Add the config constant**

In `src/stockanalysis/config.py`, directly after the `DEFAULT_THESES_DIR` block (~line 29), add:

```python
# Cached price bars live under data/cache/ — runtime state like data/theses/,
# not source. One CSV per ticker holding its COMPLETE history; see
# stockanalysis.cache.
DEFAULT_PRICE_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "prices"
```

- [ ] **Step 4: Ignore the cache directory**

In `.gitignore`, directly after the `data/theses/` entry, add:

```
# Cached price history (rebuildable from Yahoo Finance; not source)
data/cache/
```

- [ ] **Step 5: Write the storage primitives**

Create `src/stockanalysis/cache.py`:

```python
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
    except Exception as e:
        log.warning("%s: price cache unreadable (%s) — treating as a miss.", ticker, e)
        return None
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df.sort_index()


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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_cache.py -v`
Expected: PASS (8 tests)

If `test_write_then_read_round_trips_bars` fails on dtype, check that `Volume` round-trips as int64 — if pandas reads it back as int64 but the fixture built it as int64 too, this passes. Do **not** loosen the assertion to `check_dtype=False` without first confirming the mismatch is only the index `freq`.

- [ ] **Step 7: Run the full suite to confirm nothing regressed**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS — all pre-existing tests still green.

- [ ] **Step 8: Commit**

```bash
git add src/stockanalysis/cache.py src/stockanalysis/config.py tests/test_cache.py .gitignore
git commit -m "feat(cache): add atomic per-ticker price-cache storage primitives"
```

---

### Task 2: Period parsing and slicing

Turns a yfinance period string into a date cutoff so one complete-history file can serve `3y`, `5y`, `max` and `ytd` callers.

**Files:**
- Modify: `src/stockanalysis/cache.py` (append)
- Modify: `tests/test_cache.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1 beyond the module existing.
- Produces: `cache._period_start(period, today=None) -> pd.Timestamp | None` (`None` means "no cutoff — return everything") and `cache._slice(df, period, today=None) -> pd.DataFrame`. Task 4 uses `_slice` on every return path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cache.py`:

```python
# --- period parsing / slicing --------------------------------------------------

_TODAY = pd.Timestamp("2026-09-11")


def test_period_start_parses_years_months_weeks_and_days():
    # Deliberately generous units: a year counts as 366 days, a month as 31.
    assert cache._period_start("3y", today=_TODAY) == _TODAY - pd.Timedelta(days=1098)
    assert cache._period_start("6mo", today=_TODAY) == _TODAY - pd.Timedelta(days=186)
    assert cache._period_start("2wk", today=_TODAY) == _TODAY - pd.Timedelta(days=14)
    assert cache._period_start("5d", today=_TODAY) == _TODAY - pd.Timedelta(days=5)


def test_period_start_is_none_for_max():
    assert cache._period_start("max", today=_TODAY) is None


def test_period_start_is_none_for_unrecognized_input():
    # Unknown input must fail SAFE: no cutoff means the caller gets every bar
    # we have, never fewer than it asked for.
    assert cache._period_start("banana", today=_TODAY) is None
    assert cache._period_start(None, today=_TODAY) is None


def test_period_start_resolves_ytd_to_january_first():
    assert cache._period_start("ytd", today=_TODAY) == pd.Timestamp("2026-01-01")


def test_slice_trims_to_the_requested_period():
    df = bars(start="2024-01-01", n=60)
    today = df.index.max()

    out = cache._slice(df, "5d", today=today)

    assert out.index.min() >= today - pd.Timedelta(days=5)
    assert out.index.max() == today


def test_slice_returns_everything_for_max():
    df = bars(start="2024-01-01", n=60)

    pd.testing.assert_frame_equal(cache._slice(df, "max", today=df.index.max()), df)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_cache.py -k "period or slice" -v`
Expected: FAIL with `AttributeError: module 'stockanalysis.cache' has no attribute '_period_start'`

- [ ] **Step 3: Implement period parsing and slicing**

Append to `src/stockanalysis/cache.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_cache.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/cache.py tests/test_cache.py
git commit -m "feat(cache): parse yfinance period strings into slice cutoffs"
```

---

### Task 3: Merge and split/dividend restatement detection

The correctness core. `_is_restated` is what stops a split from silently corrupting every downstream indicator.

**Files:**
- Modify: `src/stockanalysis/cache.py` (append)
- Modify: `tests/test_cache.py` (append)

**Interfaces:**
- Consumes: `PRICE_RTOL` from Task 1.
- Produces: `cache._merge(cached, fresh) -> pd.DataFrame` and `cache._is_restated(cached, fresh, rtol=PRICE_RTOL) -> bool`. Task 4 calls both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cache.py`:

```python
# --- merge / restatement detection ---------------------------------------------

def test_merge_prefers_the_freshly_fetched_row_on_duplicate_dates():
    cached = bars(start="2024-01-01", n=5)
    fresh = cached.tail(2) * 2.0

    merged = cache._merge(cached, fresh)

    assert len(merged) == 5
    assert merged["Close"].iloc[-1] == cached["Close"].iloc[-1] * 2.0


def test_merge_appends_new_bars_and_keeps_the_index_sorted_and_unique():
    cached = bars(start="2024-01-01", n=5)
    fresh = bars(start="2024-01-08", n=3, first_close=200.0)

    merged = cache._merge(cached, fresh)

    assert len(merged) == 8
    assert merged.index.is_monotonic_increasing
    assert merged.index.is_unique


def test_is_restated_is_false_when_overlapping_closes_match():
    cached = bars(start="2024-01-01", n=5)

    assert cache._is_restated(cached, cached.tail(3)) is False


def test_is_restated_is_true_after_a_split_halves_history():
    cached = bars(start="2024-01-01", n=5)
    restated = cached.tail(3) / 2.0          # a 2:1 split restates older bars

    assert cache._is_restated(cached, restated) is True


def test_is_restated_ignores_floating_point_noise():
    cached = bars(start="2024-01-01", n=5)
    jittered = cached.tail(3).copy()
    jittered["Close"] = jittered["Close"] * (1 + 1e-9)

    assert cache._is_restated(cached, jittered) is False


def test_is_restated_is_false_without_overlapping_dates():
    # Nothing to compare means nothing to conclude — merge rather than rebuild.
    cached = bars(start="2024-01-01", n=5)
    disjoint = bars(start="2024-02-01", n=3)

    assert cache._is_restated(cached, disjoint) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_cache.py -k "merge or restated" -v`
Expected: FAIL with `AttributeError: module 'stockanalysis.cache' has no attribute '_merge'`

- [ ] **Step 3: Implement merge and restatement detection**

Append to `src/stockanalysis/cache.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_cache.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/cache.py tests/test_cache.py
git commit -m "feat(cache): merge tails and detect split/dividend restatements"
```

---

### Task 4: `cached_history` — the read path

Wires Tasks 1–3 into the single public entry point, including every fallback.

**Files:**
- Modify: `src/stockanalysis/cache.py` (append)
- Modify: `tests/test_cache.py` (append)

**Interfaces:**
- Consumes: `read_cache`, `write_cache`, `OVERLAP_DAYS` (Task 1); `_slice` (Task 2); `_merge`, `_is_restated` (Task 3).
- Produces: `cache.cached_history(ticker, period, fetcher, cache_dir=None) -> pd.DataFrame | None`. The `fetcher` contract is `fetcher(ticker, period=None, start=None) -> pd.DataFrame | None` — exactly one of `period`/`start` is ever set. Task 5 supplies `ingest._raw_history` as that fetcher.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cache.py`:

```python
# --- cached_history orchestration ----------------------------------------------

class FakeFetcher:
    """Records every call and returns queued responses.

    Asserting on ``.calls`` is the point: the only proof a fetch was actually
    incremental is the arguments it was made with, not the frame it returned.
    Queue an Exception instance to make that call raise.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, ticker, period=None, start=None):
        self.calls.append({"ticker": ticker, "period": period, "start": start})
        if not self.responses:
            raise AssertionError(f"unexpected extra fetch: {ticker} {period} {start}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_cold_miss_fetches_complete_history_and_writes_the_cache(tmp_path):
    full = bars(start="2024-01-01", n=30)
    fetcher = FakeFetcher(full)

    cache.cached_history("AAPL", "5d", fetcher, cache_dir=tmp_path)

    # 'max', NOT the caller's '5d' — that is the complete-history invariant.
    assert fetcher.calls == [{"ticker": "AAPL", "period": "max", "start": None}]
    assert cache.cache_path("AAPL", cache_dir=tmp_path).exists()


def test_cold_miss_returns_only_the_requested_slice(tmp_path):
    full = recent_bars(n=30)          # must end today — see recent_bars' docstring
    fetcher = FakeFetcher(full)

    out = cache.cached_history("AAPL", "5d", fetcher, cache_dir=tmp_path)

    assert len(out) < len(full)
    assert out.index.max() == full.index.max()


def test_warm_hit_requests_only_the_tail(tmp_path):
    cached = bars(start="2024-01-01", n=20)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    fetcher = FakeFetcher(cached.tail(2))

    cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    call = fetcher.calls[0]
    assert call["period"] is None
    assert call["start"] == cached.index.max() - pd.Timedelta(days=cache.OVERLAP_DAYS)


def test_warm_hit_merges_new_bars_into_the_stored_cache(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    tail = bars(start="2024-01-15", n=3, first_close=300.0)
    fetcher = FakeFetcher(tail)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert len(out) == 13
    assert len(cache.read_cache("AAPL", cache_dir=tmp_path)) == 13


def test_max_caller_is_served_from_a_cache_built_by_a_3y_caller(tmp_path):
    """The regression the complete-history invariant exists to prevent:
    thesis.review asks for 'max' on every run and must not refetch it."""
    full = recent_bars(n=30)          # must end today — see recent_bars' docstring
    cold = FakeFetcher(full)
    cache.cached_history("AAPL", "3y", cold, cache_dir=tmp_path)

    warm = FakeFetcher(full.tail(2))
    out = cache.cached_history("AAPL", "max", warm, cache_dir=tmp_path)

    assert len(warm.calls) == 1                  # the tail top-up only
    assert warm.calls[0]["period"] is None       # never a second full download
    assert len(out) == 30


def test_restated_closes_trigger_a_full_rebuild(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    restated_tail = cached.tail(3) / 2.0          # a 2:1 split
    rebuilt = bars(start="2024-01-01", n=12, first_close=50.0)
    fetcher = FakeFetcher(restated_tail, rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "max"
    assert len(out) == 12
    # The stale pre-split bars are gone, not merged alongside the new ones.
    pd.testing.assert_frame_equal(
        cache.read_cache("AAPL", cache_dir=tmp_path), rebuilt, check_freq=False)


def test_failed_top_up_refetches_complete_history(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    rebuilt = bars(start="2024-01-01", n=14)
    fetcher = FakeFetcher(RuntimeError("connection reset"), rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "max"
    assert len(out) == 14


def test_empty_top_up_response_also_refetches_complete_history(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    rebuilt = bars(start="2024-01-01", n=14)
    fetcher = FakeFetcher(bars(n=0), rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert len(out) == 14


def test_failed_top_up_and_failed_rebuild_serve_the_cached_bars(tmp_path):
    """Fully offline with a warm cache: real, slightly stale output beats none."""
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    fetcher = FakeFetcher(RuntimeError("offline"), RuntimeError("offline"))

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    pd.testing.assert_frame_equal(out, cached, check_freq=False)


def test_failed_cold_max_falls_back_to_the_requested_period_unwritten(tmp_path):
    partial = bars(start="2024-01-01", n=5)
    fetcher = FakeFetcher(RuntimeError("max unavailable"), partial)

    out = cache.cached_history("AAPL", "3y", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "3y"
    pd.testing.assert_frame_equal(out, partial, check_freq=False)
    # Never store a partial fetch — that is what keeps coverage metadata-free.
    assert not cache.cache_path("AAPL", cache_dir=tmp_path).exists()


def test_returns_none_when_every_fetch_fails_with_no_cache(tmp_path):
    fetcher = FakeFetcher(RuntimeError("offline"), RuntimeError("offline"))

    assert cache.cached_history("AAPL", "3y", fetcher, cache_dir=tmp_path) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_cache.py -k "cold or warm or restated_closes or top_up or max_caller or every_fetch" -v`
Expected: FAIL with `AttributeError: module 'stockanalysis.cache' has no attribute 'cached_history'`

- [ ] **Step 3: Implement the read path**

Append to `src/stockanalysis/cache.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_cache.py -v`
Expected: PASS (31 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/cache.py tests/test_cache.py
git commit -m "feat(cache): add cached_history with incremental top-up and rebuild"
```

---

### Task 5: Wire the cache into `ingest`

Makes the cache real for callers. `backtest` and `thesis.review` call `fetch_stock_data` and inherit caching with no change on their side.

**Files:**
- Modify: `src/stockanalysis/ingest.py:20-44` (`fetch_stock_data`), `:144-171` (`load_watchlist`), and the import block at `:7-17`
- Modify: `tests/test_ingest.py` (append)

**Interfaces:**
- Consumes: `cache.cached_history` (Task 4).
- Produces: `ingest._raw_history(ticker, period=None, start=None) -> pd.DataFrame | None` (the fetcher); `ingest.fetch_stock_data(ticker, period=..., use_cache=True, cache_dir=None)`; `ingest.load_watchlist(watchlist=None, period=..., use_cache=True, cache_dir=None)`. Task 6 calls `load_watchlist` with the two new kwargs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest.py`:

```python
# --- price caching -------------------------------------------------------------

import types

import pandas as pd

from stockanalysis import ingest


def _ohlcv(n=5):
    # Ends today: fetch_stock_data slices by period, and a stale-dated fixture
    # would correctly slice down to nothing.
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n)
    close = pd.Series([100.0 + i for i in range(n)], index=idx)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1_000_000},
        index=idx,
    )


def _stub_info(monkeypatch, info=None):
    """Stub yf.Ticker so only the .info half of fetch_stock_data is faked."""
    monkeypatch.setattr(ingest.yf, "Ticker",
                        lambda ticker: types.SimpleNamespace(info=info or {}))


def test_fetch_stock_data_without_cache_requests_the_full_period(monkeypatch, tmp_path):
    calls = []

    def fake_raw(ticker, period=None, start=None):
        calls.append((period, start))
        return _ohlcv()

    monkeypatch.setattr(ingest, "_raw_history", fake_raw)
    _stub_info(monkeypatch)

    hist, _ = ingest.fetch_stock_data("AAPL", period="3y",
                                      use_cache=False, cache_dir=tmp_path)

    assert calls == [("3y", None)]
    assert len(hist) == 5
    assert not list(tmp_path.iterdir())      # disk untouched when caching is off


def test_fetch_stock_data_with_cache_writes_then_reuses_the_file(monkeypatch, tmp_path):
    calls = []

    def fake_raw(ticker, period=None, start=None):
        calls.append((period, start))
        return _ohlcv()

    monkeypatch.setattr(ingest, "_raw_history", fake_raw)
    _stub_info(monkeypatch)

    ingest.fetch_stock_data("AAPL", period="3y", use_cache=True, cache_dir=tmp_path)
    ingest.fetch_stock_data("AAPL", period="3y", use_cache=True, cache_dir=tmp_path)

    assert calls[0] == ("max", None)         # cold miss pulls complete history
    assert calls[1][0] is None               # second run tops up the tail only
    assert calls[1][1] is not None


def test_fetch_stock_data_returns_none_pair_when_there_is_no_history(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_raw_history", lambda t, period=None, start=None: None)
    _stub_info(monkeypatch)

    assert ingest.fetch_stock_data("AAPL", use_cache=True, cache_dir=tmp_path) == (None, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `AttributeError: module 'stockanalysis.ingest' has no attribute '_raw_history'`

- [ ] **Step 3: Add the raw fetcher and rewrite `fetch_stock_data`**

In `src/stockanalysis/ingest.py`, add `cache` to the package import at line 15:

```python
from . import cache, config
```

Then replace `fetch_stock_data` (lines 20-44) with:

```python
def _raw_history(ticker: str, period=None, start=None):
    """Raw yfinance price fetch — the fetcher :mod:`cache` wraps.

    Exactly one of ``period``/``start`` is used. Returns ``None`` on empty
    data so the cache layer can treat it as a miss.
    """
    kwargs = {"start": start} if start is not None else {"period": period}
    # auto_adjust=True gives split/dividend-adjusted OHLC (cleaner for TA) —
    # and is why cache._is_restated has to watch for retroactive restatements.
    hist = yf.Ticker(ticker).history(interval="1d", auto_adjust=True, **kwargs)
    if hist is None or hist.empty:
        return None
    # Normalise index to tz-naive dates for consistent plotting/joins
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    return hist


def fetch_stock_data(ticker: str, period: str = config.HISTORY_PERIOD,
                     use_cache: bool = True, cache_dir=None):
    """Fetch daily OHLCV history + the fundamentals ``.info`` dict for one ticker.

    Returns ``(history_df, info_dict)``. On any failure or empty data, returns
    ``(None, None)`` and logs a warning so the caller can skip gracefully.

    With ``use_cache`` (the default) price bars come from the local cache in
    ``data/cache/prices/``, which fetches only the bars it is missing. Pass
    ``use_cache=False`` to force a full network fetch. ``.info`` is never
    cached, so it is still fetched on every call.
    """
    try:
        if use_cache:
            hist = cache.cached_history(ticker, period, _raw_history, cache_dir=cache_dir)
        else:
            hist = _raw_history(ticker, period=period)
        if hist is None or hist.empty:
            log.warning("%s: no price history returned — skipping.", ticker)
            return None, None
        # .info can be flaky; tolerate failure and fall back to empty dict
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as e:
            log.warning("%s: .info unavailable (%s). Proceeding with prices only.", ticker, e)
            info = {}
        return hist, info
    except Exception as e:
        log.error("%s: fetch failed (%s) — skipping.", ticker, e)
        return None, None
```

- [ ] **Step 4: Thread the flags through `load_watchlist`**

In `src/stockanalysis/ingest.py`, change the `load_watchlist` signature (line 144) and its fetch call (line 158):

```python
def load_watchlist(watchlist: dict | None = None, period: str = config.HISTORY_PERIOD,
                   use_cache: bool = True, cache_dir=None):
```

```python
        hist, info = fetch_stock_data(tk, period=period,
                                      use_cache=use_cache, cache_dir=cache_dir)
```

Add to the `load_watchlist` docstring, after the existing Returns block:

```
    ``use_cache``/``cache_dir`` are forwarded to :func:`fetch_stock_data`.
```

Update its opening log line (line 156), which no longer tells the truth once bars are cached:

```python
    log.info("Fetching data for %d tickers (cache %s)...",
             len(watchlist), "on" if use_cache else "off")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS. `backtest` and `thesis.review` call `fetch_stock_data` positionally, so they pick up caching with no signature change — confirm `tests/test_backtest.py` and `tests/test_thesis_review.py` are still green.

- [ ] **Step 7: Commit**

```bash
git add src/stockanalysis/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): serve price history from the local cache by default"
```

---

### Task 6: Expose the cache through `pipeline` / CLI, and document it

**Files:**
- Modify: `src/stockanalysis/pipeline.py:60-97` (`run` signature, docstring, `load_watchlist` call)
- Modify: `src/stockanalysis/cli.py:16-34` (`_add_run_parser`), `:80-90` (the `pipeline.run` call)
- Modify: `tests/test_pipeline.py:53` (the `load_watchlist` stub — **it will break otherwise**)
- Modify: `tests/test_cli_run.py` (append)
- Modify: `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes: `ingest.load_watchlist(..., use_cache=, cache_dir=)` (Task 5).
- Produces: `pipeline.run(..., use_cache=True, cache_dir=None)` and the `stock-analysis run --no-cache` flag. Nothing downstream depends on these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_run.py`:

```python
def test_cli_run_defaults_to_caching_enabled():
    _, kwargs = _run_cli(["run", "--target", "none"])

    assert kwargs["use_cache"] is True


def test_cli_run_no_cache_disables_the_price_cache():
    _, kwargs = _run_cli(["run", "--target", "none", "--no-cache"])

    assert kwargs["use_cache"] is False
```

Append to `tests/test_pipeline.py`:

```python
def test_run_forwards_cache_flags_to_the_ingest_layer(monkeypatch, tmp_path):
    seen = {}

    def fake_load_watchlist(wl, period=None, use_cache=True, cache_dir=None):
        seen.update(use_cache=use_cache, cache_dir=cache_dir)
        return {}, pd.DataFrame()

    _stub_run(monkeypatch, ["AAPL"])
    monkeypatch.setattr(pipeline, "load_watchlist", fake_load_watchlist)

    pipeline.run(watchlist={"AAPL": "Technology"}, export_target=None,
                 save_report=False, use_cache=False, cache_dir=tmp_path,
                 out_dir=str(tmp_path))

    assert seen == {"use_cache": False, "cache_dir": tmp_path}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_cli_run.py tests/test_pipeline.py -v`
Expected: FAIL — `KeyError: 'use_cache'` on the CLI tests and `TypeError: run() got an unexpected keyword argument 'use_cache'` on the pipeline test.

- [ ] **Step 3: Fix the existing `load_watchlist` stub**

In `tests/test_pipeline.py:53`, the stub signature must accept the new kwargs or every `_stub_run` test fails with a `TypeError`. Change:

```python
    monkeypatch.setattr(pipeline, "load_watchlist",
                        lambda wl, period=None: ({t: pd.DataFrame() for t in tickers}, screened))
```

to:

```python
    monkeypatch.setattr(
        pipeline, "load_watchlist",
        lambda wl, period=None, use_cache=True, cache_dir=None:
            ({t: pd.DataFrame() for t in tickers}, screened))
```

- [ ] **Step 4: Add the parameters to `pipeline.run`**

In `src/stockanalysis/pipeline.py`, add two parameters to the `run` signature (after `top_n`):

```python
        top_n: int | None = 5,
        use_cache: bool = True,
        cache_dir=None,
        out_dir: str = "output") -> Results:
```

Add to the docstring's parameter list, after the `top_n` entry:

```
    use_cache     : serve price bars from the local cache in
                    data/cache/prices/, fetching only the missing tail
                    (default True). False forces a full network fetch.
    cache_dir     : override the cache location (defaults to
                    config.DEFAULT_PRICE_CACHE_DIR).
```

And forward them at the `load_watchlist` call (line 97):

```python
    prices, fundamentals_df = load_watchlist(watchlist, period=period,
                                             use_cache=use_cache, cache_dir=cache_dir)
```

- [ ] **Step 5: Add the CLI flag**

In `src/stockanalysis/cli.py`, append to `_add_run_parser` (after the `--top` argument):

```python
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass the local price cache (data/cache/prices/) and "
                        "refetch full history from Yahoo Finance.")
```

And pass it through in the `pipeline.run` call:

```python
                top_n=args.top,
                use_cache=not args.no_cache,
                out_dir=args.out,
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest -q`
Expected: PASS — the whole suite.

- [ ] **Step 7: Verify the package still imports and the CLI help renders**

Run:
```bash
PYTHONPATH=src ./venv/bin/python -c "import stockanalysis; print('ok')"
PYTHONPATH=src ./venv/bin/python -m stockanalysis run --help
```
Expected: `ok`, and `--no-cache` listed in the help output.

- [ ] **Step 8: Update `CLAUDE.md`**

Add a row to the module map table, directly after the `config.py` row:

```markdown
| `cache.py` | Local price-bar cache — `cached_history(ticker, period, fetcher)` serves one complete-history CSV per ticker from `data/cache/prices/` (`DEFAULT_PRICE_CACHE_DIR`), fetching only the missing tail. Never imports yfinance (takes a fetcher callable) |
```

Add to the "Conventions that are easy to get wrong" section:

```markdown
- **The price cache stores COMPLETE history, and the overlap check is not
  optional.** A cold miss fetches `period="max"` regardless of what the caller
  asked for, so one CSV serves `3y`/`5y`/`max` callers as slices and coverage
  needs no metadata — which is why a partial-period fetch is **never written to
  disk** (`cache.cached_history` returns it unstored). On a warm run the cache
  re-requests the last `OVERLAP_DAYS` (5) of cached bars alongside the new tail
  and compares closes: `auto_adjust=True` means a split retroactively restates
  every older bar, so appending a fresh tail onto a stale base would open a
  phantom gap that silently corrupts every EMA/RSI/ATR downstream with no error.
  A mismatch rebuilds the file from scratch. Don't "optimize away" the overlap.
```

- [ ] **Step 9: Update `README.md`**

In the running/usage section, after the existing `stock-analysis run` example, add:

```markdown
Price history is cached locally in `data/cache/prices/` (one CSV per ticker,
complete history), so subsequent runs fetch only the bars they're missing.
Splits and dividends are detected and the affected ticker is rebuilt
automatically. Pass `--no-cache` to bypass it, or delete the folder to start
clean — it is rebuilt on the next run.

**Note:** `.info` fundamentals are *not* cached, so a warm run still makes one
`.info` request per ticker.
```

- [ ] **Step 10: Commit**

```bash
git add src/stockanalysis/pipeline.py src/stockanalysis/cli.py \
        tests/test_pipeline.py tests/test_cli_run.py CLAUDE.md README.md
git commit -m "feat(cli): add --no-cache and document the price cache"
```

---

## Manual verification (after Task 6, requires network)

The test suite is fully offline, so one live run is worth doing to confirm the cache behaves against real Yahoo Finance data:

```bash
rm -rf data/cache/prices
time ./venv/bin/stock-analysis run --target none --no-report   # cold: fetches max
ls -la data/cache/prices/ | head
time ./venv/bin/stock-analysis run --target none --no-report   # warm: tail only
```

Expect the second run to be faster, `data/cache/prices/` to hold one CSV per watchlist ticker, and the logs to show no "rebuilding cache" lines on the warm run. A `-v` run shows the per-ticker cache decisions.

Then confirm the offline path:

```bash
# With networking disabled (e.g. Wi-Fi off):
./venv/bin/stock-analysis run --target none --no-report
```

Expect warnings about failed fetches but a populated `prices` dict served from cache — where today's behaviour would be an empty run. Fundamentals will still be NaN (`.info` is uncached), so nothing screens; that is expected.
