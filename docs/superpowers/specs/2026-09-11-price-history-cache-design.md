# Local Price-History Cache — Design

**Date:** 2026-09-11
**Status:** Approved (pending spec review)
**Feature:** A local, incremental on-disk cache of OHLCV bars so a run fetches
only the bars it is missing instead of re-downloading full history every time.

## Problem

Every `stock-analysis run` re-downloads the complete price history for every
watchlist ticker. With ~20 tickers at `period="3y"` that is ~20 full
multi-year downloads per run, repeated in full even when two runs happen
minutes apart and only one new bar exists. `backtest` (`period="5y"`) and
`thesis` MAE/MFE review (`period="max"`) pay the same cost again, separately.

Bars are immutable history: yesterday's close does not change. Re-fetching
them is pure waste.

## Goal

Persist fetched OHLCV bars locally and, on subsequent runs, request only the
tail after the last cached bar — while remaining correct across splits and
dividends, and degrading gracefully when the network is unavailable.

**Non-goals (v1, YAGNI):** caching `.info` fundamentals, `.news`, or
`.calendar`; caching `overview.fetch_index_data`; intraday/sub-daily bars; a
cache-eviction or size-cap policy; a cross-process lock.

## Honest scope note: what this does and does not buy

Caching bars shrinks **payloads, not round trips**. With a warm cache
`fetch_stock_data` still issues two requests per ticker — a (now tiny)
`history()` call plus the uncached `.info` call. Since yfinance's cost is
dominated by per-request latency and `.info` is typically the slower of the
two, wall-clock improvement for the standard `run` will be real but partial.

What it unambiguously buys:

- `backtest` (`5y`) and `thesis` review (`max`) stop re-pulling deep history.
- Runs become usable **offline**: cached bars serve charts, indicators and the
  technical posture instead of producing empty output.
- It establishes the storage/merge layer that a later `.info` cache (the
  obvious phase 2, where the remaining latency lives) can reuse.

## Decisions

| Question | Decision |
|---|---|
| What is cached | **Price history only** (OHLCV bars) |
| Freshness policy | **Always top up the tail** — every run fetches bars after the last cached date; no TTL, no skip-if-recent |
| Storage format | **CSV, one file per ticker** — no new dependency, human-readable, matches the repo's stdlib-only, file-per-item convention |
| Split / dividend restatement | **Auto-heal via an overlap check** — no manual refresh flag |
| Call sites wired up | **`ingest.fetch_stock_data` only** (which transitively covers `pipeline.run`, `backtest`, and `thesis.review`) |

## Architecture

A new module, **`src/stockanalysis/cache.py`**, that wraps a *fetcher callable*:

```python
cached_history(ticker, period, fetcher, cache_dir=None) -> pd.DataFrame | None
```

`fetcher(ticker, period=None, start=None) -> pd.DataFrame | None` is supplied
by the caller. `ingest.fetch_stock_data` passes its own raw yfinance call.

**`cache.py` never imports yfinance.** That keeps the whole module testable
offline with a fake fetcher, and lets `overview.fetch_index_data` (which uses
`yf.download`, not `yf.Ticker().history`) adopt the same cache later without
reshaping anything.

Rejected alternatives:

- **Cache logic inline in `ingest.py`** — grows `ingest.py` to ~300 lines
  mixing network, normalization and persistence, and makes the cache testable
  only by monkeypatching yfinance.
- **A generic disk-memoize decorator on `fetch_stock_data`** — a decorator can
  only cache whole return values by key; it cannot do the incremental merge or
  the overlap check, which is the entire point.

## Storage

```
data/cache/prices/<TICKER>.csv
```

- New `config.DEFAULT_PRICE_CACHE_DIR`, defined next to `DEFAULT_THESES_DIR`
  (`parents[2] / "data" / "cache" / "prices"`). Runtime state, not source.
- Added to `.gitignore` alongside `data/theses/`.
- CSV layout: a `Date` index (**tz-naive**, matching what `fetch_stock_data`
  already normalizes to) plus yfinance's native columns (`Open`, `High`,
  `Low`, `Close`, `Volume`, and whatever else the fetcher returned, e.g.
  `Dividends`, `Stock Splits`). Column set is written as-received rather than
  being pinned, so a fetcher returning extra columns round-trips intact.
- Filenames are sanitized for the filesystem — characters outside
  `[A-Za-z0-9._-]` (notably `^` in index symbols and `/`) are replaced — so
  extending the cache to indices later needs no migration.
- Writes are **atomic**: write `<TICKER>.csv.tmp`, then `os.replace`. This
  mirrors the thesis store's convention and means an interrupted run can never
  leave a half-written cache file.

## Period handling

The cache file holds a **superset** of bars for a ticker; it is *not* keyed by
period string. A request for `period` resolves to the earliest date that period
requires:

- If the cache's earliest bar is **later** than the required start (e.g. the
  file holds 3y but `thesis.review` asks for `max`), the cache cannot satisfy
  the request → do a **full fetch for `period`** and store the wider result.
- Otherwise → serve a **slice** of the cached frame, after topping up the tail.

So `3y`, `5y` and `max` callers share one file and progressively widen it,
instead of fighting over it or maintaining duplicate per-period entries.

Period strings are parsed into an approximate calendar lookback (`"3y"`,
`"6mo"`, `"5d"`, `"max"`, `"ytd"`). The comparison is deliberately tolerant:
an *approximate* required-start that is a few days off only risks an
unnecessary full re-fetch, never silently short history. A period string the
parser does not recognize is treated as "cannot be satisfied from cache" → full
fetch, so unknown inputs fail safe.

## Read path

1. **No file / unreadable / empty** → full `period` fetch via `fetcher`, store,
   return.
2. **Cache does not cover the required start** (see above) → same as 1.
3. **File present and covering** → fetch
   `start = last_cached_date − OVERLAP_DAYS` (5 calendar days).
4. **Overlap check:** compare the re-fetched rows against the cached rows on
   their shared dates. If any `Close` differs by more than a small relative
   tolerance (`1e-4`), Yahoo has retroactively restated history — a split or
   dividend — so **discard the file, do a full `period` re-fetch, overwrite**,
   and log at INFO that the cache was rebuilt.
5. **Top-up fetch failed or returned empty** → **retry as a full `period`
   fetch** and overwrite the cache. A failed or truncated tail response is
   never merged into the store; the cheap incremental path is an optimization,
   and the moment it misbehaves the cache falls back to fetching everything the
   caller needs. Only if that full fetch *also* fails does the cached frame get
   served (see Error handling).
6. **Otherwise merge:** concatenate, drop duplicate dates keeping the
   **freshly fetched** row, sort by date, write back atomically, and return the
   slice the caller asked for.

### Why the overlap check is not optional

`fetch_stock_data` fetches with `auto_adjust=True`, so cached bars are
split/dividend-adjusted *as of fetch time*. After a 2:1 split, every older bar
is restated at half its previous value. Blindly appending a fresh tail onto a
stale base would produce a 50% phantom gap mid-series, silently corrupting
EMA20/50/200, RSI, ATR14, the regression channel and every technical score
downstream — with no error anywhere. The overlap check costs **no extra
request** (the overlap rides along in the tail fetch that already happens) and
self-corrects automatically.

## Error handling

Consistent with the module's existing "degrade, never crash" rule:

| Failure | Behaviour |
|---|---|
| Cache file missing | Treat as a miss → full fetch |
| Cache file corrupt / unparseable | Log a warning, treat as a miss, overwrite on the next successful fetch. Never raises. |
| Top-up fetch fails or returns empty, cache present | **Retry as a full `period` fetch** and overwrite the cache. A garbled or partial tail is never merged in. |
| That fallback full fetch also fails, cache present | **Serve the cached bars** as a last resort, log a warning. This is what makes offline runs produce real (slightly stale) output instead of empty output. |
| Full fetch fails, no cache | Return `None` — exactly today's behaviour |
| Cache directory not writable | Log a warning once and proceed uncached; a read-only disk degrades performance, never correctness |

## Public surface

```python
ingest.fetch_stock_data(ticker, period=config.HISTORY_PERIOD,
                        use_cache=True, cache_dir=None)
ingest.load_watchlist(watchlist=None, period=config.HISTORY_PERIOD,
                      use_cache=True, cache_dir=None)
pipeline.run(..., use_cache=True, cache_dir=None)
```

CLI: `stock-analysis run --no-cache` (escape hatch — combined with deleting
`data/cache/prices/`, this covers manual recovery without a dedicated
`--refresh-cache` flag).

Defaults keep every existing call site behaving identically, only faster.
`backtest` and `thesis.review` call `fetch_stock_data` positionally and inherit
caching with no signature change on their side.

## Testing

All offline. New `tests/test_cache.py`, driven by an **injected fake fetcher**
that records the arguments it was called with:

- Cold miss → fetcher called with `period`, file written, bars returned.
- Warm hit → fetcher called with a `start` (not `period`), and the asserted
  `start` equals `last_cached_date − 5 days`. Asserting the *request* matters
  more than the result: it is the only proof the fetch was actually incremental.
- Merge/dedupe → overlapping dates resolve to the freshly fetched values, index
  stays sorted and unique.
- Split restatement → a fetcher returning restated overlap closes triggers
  exactly one full re-fetch and the stored file contains only restated bars.
- Corrupt CSV → treated as a miss, no exception.
- Failed top-up with a warm cache → exactly one full `period` re-fetch is
  attempted, and its result replaces the cache.
- Failed top-up *and* failed full fetch with a warm cache → cached bars
  returned, no exception.
- Period widening → a `3y` cache followed by a `max` request triggers a full
  fetch and widens the stored file; a subsequent `3y` request is served from it.
- Filename sanitization round-trip for a symbol containing `^` and `.`.

Plus, in `tests/test_ingest.py`: `use_cache=False` bypasses disk entirely (no
file created, fetcher always asked for the full period).

Every test points `cache_dir` at pytest's `tmp_path`, so the suite never
touches the real `data/cache/` directory.

## Documentation

- `CLAUDE.md`: add `cache.py` to the module map; note the cache directory under
  the data-flow section; document the auto-heal overlap check under
  "Conventions that are easy to get wrong" (it is exactly the kind of invariant
  a future edit could unknowingly break).
- `README.md`: mention `--no-cache` and where cached bars live.
