# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **structured Python package**, `stockanalysis` (under `src/`), implementing an end-to-end equity analysis pipeline for North American markets (NYSE, NASDAQ, TSX): a fundamental screener, technical-indicator computation, interactive Plotly dashboards, and a fused Buy/Hold/Watch signal engine. It is **library-first** — the core is importable with no notebook/IO side effects so a CLI drives it today and a server can later — plus a `stock-analysis` CLI and a thin demo notebook (`notebooks/stock_analysis.ipynb`).

This was converted from a single Jupyter notebook; the logic was ported function-for-function into modules. (The original `stock_analysis.ipynb` may still sit at the project root pending removal.)

## Running

```bash
python -m venv venv && source venv/bin/activate
pip install -e .                 # core; pip install -e ".[gsheets]" adds Google Sheets
stock-analysis run --target excel --out output/    # or: python -m stockanalysis run ...
```

Requires **live network access** (Yahoo Finance via `yfinance`); offline runs fetch nothing and tables/charts come out empty (by design — every fetch degrades gracefully rather than crashing). Each run writes into a fresh timestamped subfolder `output/<YYYY-MM-DD_HHMMSS>/` (so runs don't overwrite each other): `output/<timestamp>/signal_matrix.xlsx` + `output/<timestamp>/report.html` — one combined report bundling the Fundamental Screener and Combined Signal Matrix (both in full) with the Trade Plan table, top-N Technical Dashboards, top-N Fundamental Profiles, and the Daily Market Overview chart. `--top N` (default 5) restricts the report's dashboards/profiles to the N strongest names of the ranked matrix; the screener/signal-matrix tables always show every screened ticker. `--fund-min N` sets the quality cutoff; `--account USD` / `--risk-pct PCT` / `--max-weight PCT` drive the position sizing (the CLI takes **percents**, e.g. `--risk-pct 1.0` = 1%; the library API takes fractions). The subdir is resolved once per run by `pipeline.run_output_dir()` and echoed back as `Results.run_dir` — **callers should read it from there rather than resolving a second one**.

Library entry point:
```python
from stockanalysis import run
results = run(export_target="excel")              # -> Results dataclass; report.html on by default (top_n=5)
results = run(save_report=False)                   # skip the combined report entirely
results = run(top_n=10)                            # widen the dashboards/profiles selection
```

### Broad-universe research (offline after one fetch)
```bash
stock-analysis universe --out data/universe_sp500.csv   # 503 tickers, committed
stock-analysis cache --universe data/universe_sp500.csv --period 10y   # ~55s, 1.23M bars
stock-analysis cache --universe data/universe_sp500.csv   # incremental top-up (~15s)
stock-analysis cache --status                            # coverage report
stock-analysis backtest --exits plan --universe data/universe_sp500.csv  # no network
```
`cache` is **incremental by default**: only tickers with no cached bars fetch full
`--period` history; the rest get a short `--refresh-period` (default `1mo`) window,
which the `(ticker, date)` upsert folds in without duplicating. A nightly refresh
is ~10k bars, not 1.23M. `--full` forces a complete refetch after a data
correction. `scripts/run_daily.sh` runs this after the pipeline, non-fatally.

The cache (`data/cache/prices.db`) is gitignored — a rebuildable artifact. Its bars
**must** come from the same `auto_adjust=True`, tz-naive path as `fetch_stock_data`
(`ingest.fetch_bulk_prices` pins this); a different adjustment convention would
silently backtest prices the live pipeline never sees. That holds **over time**
too: a dividend or split rescales every earlier bar, but a top-up window only
rewrites its own. So `cache.refresh` (which the CLI calls — write fetched bars
through it, not `upsert_bars` directly) upserts a top-up only if `cache.drifted`
finds its closes agree with the cached ones on shared dates (newest cached bar excluded — it may
have been intraday); a ticker that disagrees is refetched whole and swapped in by
`cache.replace_bars` (as is everything under `--full`), never stitched onto the
old basis. Before this check, APH drifted ×0.998451 in a week of nightly top-ups. The S&P 500 list is
**current constituents only** — delisted names are absent, so results still carry
survivorship bias, just far less sector concentration than the 21-name watchlist.

`--exits plan` never prints a bare expectancy. Every run also reports the 95% CI
**clustered by entry month** (7k correlated trades are ~118 draws, not 7k), the two
halves either side of `--split YYYY-MM-DD` (default: the calendar midpoint) with
the positive-year count, and the **edge over a random-entry null** — the same plan
and exits walked from random bars of the same tickers (`--null-reps N`, default 1,
~20 s per rep on 503 names; 0 skips it). The null is the bar, not zero: the stop/target
geometry is positive on its own. The workbook gains `Robustness` + `Yearly R` sheets.

`research/` holds the offline harness behind the 2026-09-16 tuning study (vectorized
predicates, memoized exit walks) — scratch code, **not** part of the package or its
tests; its `README.md` gives the run order. It mirrors the predicates by hand, so
re-run `research/verify_equivalence.py` after any change to `signals.py`.

### Sanity-checking edits without network
- **Test suite (fully offline):** `pip install -e ".[test]"` then `pytest`. The
  `tests/` dir covers the pure-logic surface (screener, indicators, signals,
  `fetch_fundamentals` normalization, `load_watchlist_csv`, the Excel exporter,
  `profile.save_report`, `report.build_full_report`/`save_report` (the combined
  HTML report), `pipeline.run`'s artifact selection (`top_n`, via monkeypatched
  fetch+report layers), the `run`/`backtest` CLI flags, the backtest (plan exits,
  `robustness`, the random-entry null, and the single-pass equivalence tests),
  the research cache (`refresh`, drift detection, `replace_bars`, via a
  monkeypatched `ingest.fetch_bulk_prices`), and the whole `thesis/` subpackage
  — model/store/sources/review/CLI, with an injected fake price adapter for
  MAE/MFE) with synthetic OHLCV fixtures — no network / yfinance calls.
- Import check: `PYTHONPATH=src python3 -c "import stockanalysis"`
- Pure-logic functions (`screen_fundamentals`, `add_indicators`, `fit_regression_channel`, `find_support_resistance`, `compute_technical_posture`, `generate_signals`) depend only on pandas/numpy/ta and can be exercised against synthetic OHLCV DataFrames. The Excel exporter and chart builders also run fully offline.

## Architecture

Module map (one responsibility each; core modules never import IPython/`display`):

| Module | Responsibility |
|---|---|
| `config.py` | `load_watchlist_csv()` reads the ticker→sector watchlist from `data/watchlist.csv` (TSX uses `.TO`; default path `DEFAULT_WATCHLIST_CSV`); `HISTORY_PERIOD`, Stage-0 universe/indices |
| `ingest.py` | `fetch_stock_data`, `fetch_fundamentals`, `fetch_profile`, `load_watchlist()` driver |
| `screener.py` | `screen_fundamentals` (0–6) |
| `indicators.py` | `add_indicators` + `fit_regression_channel` + `find_support_resistance` + `value_at` (the one NaN-safe reader of the column contract, shared by the signal predicates and the trade plan) |
| `signals.py` | `compute_technical_posture` (registry-driven 0–N, default 5) + `decide_action` (quality test + entry gate) + `generate_signals` + `top_tickers` (head of the ranked matrix) |
| `backtest.py` | Point-in-time replay + event study + portfolio sim. `--exits plan` walks each gate entry to its own trade-plan stop/target and reports **R-multiples** (`simulate_planned_trades`/`aggregate_trade_stats`), plus the ticker-matched random-entry null (`random_entry_trades`); `--exits horizon` keeps the fixed-horizon forward returns |
| `robustness.py` | Pure inference for plan backtests: `cluster_expectancy` (month-clustered SE/CI/p), `compare` (edge over the null + verdict), `evaluate` (all / first / second half + yearly). Fills `BacktestResults.robustness` |
| `cache.py` | SQLite research price cache (stdlib) — `connect`/`refresh`/`load_universe`, with `drifted`/`replace_bars`/`upsert_bars` beneath `refresh`; bars only, no `.info`. Feeds broad-universe backtests offline |
| `tradeplan.py` | `build_trade_plan` — stop / target / R:R / share count for one ticker, from `ATR14` + `find_support_resistance`. Pure, no fetches |
| `overview.py` | Stage-0 daily market overview — **data only**, returns dicts |
| `profile.py` | `build_profile` — deep fundamental report (returns a dict incl. a `report` string); `save_report` writes that string as UTF-8 text (mirrors `charts.save_html`) |
| `charts.py` | `build_technical_dashboard`/`build_index_overview` → Plotly `Figure`; `save_html` |
| `report.py` | `build_full_report` (pure) — combined per-run HTML: screener + signal matrix (full) + Trade Plan (Buy/Hold rows) + top-N dashboards/profiles + overview; `save_report` (I/O, mirrors `charts.save_html`) |
| `pipeline.py` | `Results` dataclass + `run()` orchestrator — **the server-callable API** |
| `cli.py` | `stock-analysis` console script |
| `outputs/` | `Exporter` ABC + `ExcelExporter` + `GSheetsExporter`; `get_exporter(target)` factory |
| `thesis/` | **Thesis memory** — track an idea idea→entry→exit→postmortem. `model` (shape/ids/validation), `store` (JSON-per-thesis persistence + lifecycle), `sources` (`from_signal_matrix`/`from_manual`), `review` (MAE/MFE, postmortem, summary), `report` (aggregated HTML journal → `output/theses/<ts>/report.html`), `cli` (the `thesis` subcommand). Separate persistent feature, **not** part of `pipeline.run`. |

Data flow (in `pipeline.run`): `load_watchlist` → `prices` + `fundamentals_df` → `screen_fundamentals` → `screened_df` → `add_indicators` (per ticker) → `tech` → `generate_signals` → `signal_matrix` → exporter / combined HTML report (`report.build_full_report`, embedding dashboards + profiles + `overview.daily_overview`). The report's dashboards/profiles selection is `top_tickers(signal_matrix, top_n)`, falling back to whatever was fetched when nothing screened; the screener/signal-matrix sections it embeds are never capped by that selection.

Thesis flow (separate, on demand): `signal_matrix` (or manual input) → `thesis.sources` → `IDEA` thesis → lifecycle transitions in `thesis.store` (JSON under `data/theses/`, `DEFAULT_THESES_DIR`) → `thesis.review` postmortem/summary → `thesis.report` aggregated HTML journal (`stock-analysis thesis report` → `output/theses/<ts>/report.html`). Driven by `stock-analysis thesis …` or the `stockanalysis.thesis` library API; demo in `notebooks/thesis_tracking.ipynb`. The `stock-analysis thesis` command surface and workflow are documented as a project skill: `.claude/skills/thesis-tracking/SKILL.md`.

**Before changing any signal threshold** — the `TECHNICAL_COMPONENTS` predicates, which components are `gating`, `DEFAULT_FUND_MIN`, or the `config.py` placement knobs — read the project skill `.claude/skills/tuning-signals/SKILL.md`. It carries the measured baseline (7,253 trades, +0.030R, 95% CI [−0.038, +0.099] clustered by month — indistinguishable from zero and from a random entry), the required hold-out/plateau workflow, and the variants already tested and found dead, so a tuning pass doesn't rediscover them.

Presentation (pandas `Styler`, `fig.show()`, printing `profile["report"]`) lives **only** in the notebook/CLI, never in the package core — the rule is about *interactive* display, not a pure function that returns a string. `report.py` and `thesis/report.py` are the two deliberate, precedented exceptions: both build a self-contained HTML string from already-fetched data with no `Styler`/`fig.show()`/print side effects, so they're safe to call from a headless job.

## Conventions that are easy to get wrong

- **NaN means fail, never crash.** `fetch_fundamentals` returns `np.nan` for any missing field, and `screen_fundamentals` relies on the fact that comparisons against NaN yield `False` — do not "fix" this by dropping NaN rows or it changes scoring semantics.
- **Unit normalization in `fetch_fundamentals`.** yfinance reports `debtToEquity` as a percent (divided by 100 → ratio) and `dividendYield` is defensively divided by 100 when it looks like a percent (>1). Growth fields are already fractional. Thresholds in `screen_fundamentals` assume these normalized units (e.g. `div_yield_min=0.015` = 1.5%).
- **Two independent scores, and they are never averaged into the decision.** Fundamental score is **0–6** (one point per threshold passed). The technical score is **registry-driven**: each predicate in `signals.TECHNICAL_COMPONENTS` is worth +1, so the score is **0–len(components)** (default **5**, a pullback/reversal pattern rather than independent confirmations — bracket notation `[n]` means "n bars before the latest bar"): `trend_up` (`Close > EMA50` and EMA50 up ≥2% over 20 bars), `dip_deep` (`RSI(3)[1] < 25` — yesterday's 3-day RSI showed a deep oversold dip), `pullback_zone` (`(Close−EMA50)/ATR14 ≤ 1.0` — price within one ATR of EMA50), `turn_confirm` (`Close > High[1]` and `Close > Open` — today's bar confirms a turn), `vol_pattern` (`SMA(Volume,5)[1] < VOL_SMA20` and `Volume ≥ 1.2×VOL_SMA20` — a quiet spell followed by a pickup). Posture **auto-scales** with the component count: Bearish at `score ≤ floor(0.0·max)` (nothing fired), Bullish at `score ≥ ceil(0.55·max)` (≥3 of 5), else Neutral. **Don't hardcode either cutoff** — both derive from `len(TECHNICAL_COMPONENTS)` via `_posture()`. The fractions are **calibrated against the score's measured distribution**, not assumed symmetric: a point-in-time replay over 10,523 ticker-bars gives `0/5 4.9% · 1/5 50.6% · 2/5 35.1% · 3/5 8.5% · 4/5 0.8% · 5/5 0.1%`, because a *conjunctive* pattern piles up at 1–2. The original mirrored ⅓/⅔ fractions assumed the symmetric spread of independent confirmations and labelled 55.5% of bars Bearish and 0.9% Bullish — re-derive the fractions from a fresh replay if you change the registry's components, don't just rescale them. **Add or remove a component by editing the `TECHNICAL_COMPONENTS` list** — the max, composite divisor, posture cutoffs, `detail` keys **and the entry gate** all derive from it; pass `components=` to `compute_technical_posture`/`decide_action` to override per call. Entries are `TechnicalComponent(name, predicate, gating=False)` NamedTuples; a plain `(name, predicate)` tuple is still accepted for ad-hoc registries (normalized by `_components`, non-gating).
- **The action comes from `signals.decide_action`, not from `Composite`.** Fundamentals say *what is worth owning*; the technical **entry gate** says *whether today is the day*. `Buy` = `fund_score ≥ fund_min` (default 4) **AND** every `gating=True` component in the registry (`trend_up`, `pullback_zone`, `turn_confirm` — context, location, trigger) fires; `Hold` = quality without the gate; `Watch` = below `fund_min`, whatever the chart looks like. `dip_deep` and `vol_pattern` stay **scored but non-blocking**. `signals.GATE_COMPONENTS` is *derived* from the registry (`tuple(c.name for c in ... if c.gating)`) — read it, never edit it as a second source of truth. Because gating is a property of the entry, a custom `components=` registry gates on its own entries and cannot silently fall back to the default gate. `Composite` — computed by `signals.rank_score` (weights in `RANK_WEIGHTS`, `0.70*(fund/6) + 0.30*(tech/len(components))`) — still drives the **ranking** (sort order, heatmaps, `top_tickers`, `overview.action_plan`) — it just no longer decides anything. **Don't reintroduce a composite threshold as the action**: averaging a conjunctive pattern hands out partial credit for half a setup, which is how `fund=6, tech=0` used to score 0.700 and clear a 0.60 "Buy" bar with no technical confirmation at all. `backtest.posture_timeline(mode="composite")` calls the same `decide_action` — keep it that way, or the backtest measures a rule that doesn't ship. `posture_timeline` records a **`gate` column in every mode**, so plan-based exits reuse that one O(N²) replay instead of walking it twice; `mode="gate"` labels bars `Entry`/`Flat` from the gate alone, the only lookahead-free entry rule (yfinance fundamentals are always *today's*). **`fast=True`** (default in `build_results_from_prices`) computes `add_indicators` once instead of per trailing slice — exact, because every column the predicates read is *causal*; the envelope is not, but nothing scores it. Pinned by an equivalence test, and `compute_technical_posture(with_levels=False)` skips the unscored S/R context that otherwise dominates per-bar cost. `simulate_planned_trades` does the same for trade plans — one `add_indicators` pass per ticker, sliced per entry — exact because `build_trade_plan` reads only causal columns (`Close`, `ATR14`, the High/Low pivots), also pinned by a test. Together: a 503-name, 10-year plan backtest goes from ~40 min to ~5, including a 1-rep null. `WARMUP_BARS` is shared by the replay and `random_entry_trades`, so the null draws from exactly the bars the gate can fire on.
- **Every non-Watch row carries a trade plan.** `generate_signals` merges `tradeplan.build_trade_plan` into each row, adding the `tradeplan.MATRIX_COLUMNS` — `Entry`, `Stop`, `Stop Basis`, `Target`, `Target Basis`, `R:R`, `Shares`, `Risk $`. Entry is the **last close** (fills assumed next open, matching `backtest.forward_returns`). Stop is **structure-first with an ATR floor**: the nearest support *below* price less `STOP_BUFFER_ATR`×ATR14, falling back to `entry − ATR_STOP_MULT×ATR14` when no support qualifies. Target is the nearest resistance *above*, else `entry + 2R`. **Both level searches skip anything inside a distance floor** (`MIN_STOP_ATR`/`MIN_TARGET_ATR`) and take the next level out — price frequently sits *on* a level, and without the floor the stop lands 0.8% away (stopped out by an ordinary day's range) and the target 0.2% away (a meaningless R:R of 0.05). This was caught only by running against live data; synthetic fixtures never put price on a level. Size is fixed-fractional (`account_size × risk_pct / risk_per_share`) then capped at `max_weight` of the account. Two traps: `find_support_resistance` ranks by touch count and truncates, so the plan calls it with `max_levels=20` and filters by **distance** — using the default 6 can silently drop the nearest level; and a missing or zero `ATR14` must yield `tradeplan.empty_plan()` rather than a division by zero. Watch rows get `empty_plan()` (which also skips the S/R fit for every rejected name). Sizing defaults live in `config.py` (`DEFAULT_ACCOUNT_SIZE` is a **notional** $100k, echoed in the report); the decision knobs (`GATE_COMPONENTS`, `DEFAULT_FUND_MIN`) live next to the registry in `signals.py`.
- **Indicator column names are a contract.** `add_indicators` writes `EMA20/EMA50/EMA200`, `ENV_UP/ENV_DOWN` (a **data-driven asymmetric** band around EMA20: lower/upper edges at the 2.5th/97.5th percentile of `Close/EMA20−1` so ~`envelope_coverage`=95% of closes fall inside; symmetric ±`envelope_fallback_pct` on <20-bar series), `MACD/MACD_SIG/MACD_HIST`, `RSI` (14) + `RSI3` (3-period, used by `dip_deep`), `ATR14` (average true range, used by `pullback_zone`), and the volume columns `VOL_SMA5`/`VOL_SMA20` (5/20-day avg volume) + `OBV` (On-Balance Volume). `charts.build_technical_dashboard` and `compute_technical_posture` read these exact names. Trend channels and support/resistance are **not** stored columns — they are window-dependent overlays computed on demand by `fit_regression_channel(close)` (regression mid ±2σ, returns `slope`) and `find_support_resistance(df)` (swing-pivot clustering → horizontal levels), reused by both the dashboard and (for `nearest_level` context) the posture engine.
- **Exporters are pluggable.** Add an output destination by subclassing `outputs.base.Exporter` and registering it in `outputs.get_exporter`. Keep `gspread`/`google-auth` lazily imported (optional `gsheets` extra) so Excel works without them.
- **Thesis lifecycle is forward-only and ledger-based.** `thesis.store` mutates status only through its lifecycle functions (`transition`/`open_position`/`trim`/`close`/`terminate`/`mark_reviewed`); each enforces `model.STATUS_ORDER` (IDEA→ENTRY_READY→ACTIVE→PARTIALLY_CLOSED→CLOSED, plus INVALIDATED), appends to `status_history`, re-validates, and writes atomically. Realized P&L is **summed from immutable ledger entries** (each trim/close carries `realized_pnl`) — never recompute by mutating the entry price. All exits go through one `store._record_sale` helper (compute realized → decrement `shares_remaining` → append the ledger row), so `trim` / `close` / a priced `terminate` always write an identically-shaped row — add new exit paths via that helper, don't hand-roll another. Registration is **idempotent on `origin.fingerprint`**, so re-ingesting the same `signal_matrix` won't duplicate. Storage is **JSON-per-thesis + `_index.json`** (stdlib only — don't add `pyyaml`/`jsonschema`; validation is plain Python in `model.validate_thesis`). MAE/MFE uses `ingest.fetch_stock_data` via `review.YFinancePriceAdapter` (injectable — tests pass a fake; **no FMP key**, unlike the upstream skill).
