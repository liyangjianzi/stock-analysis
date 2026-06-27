# Backtest / Signal Validation — Design

**Date:** 2026-06-27
**Status:** Approved (pending spec review)
**Feature:** A backtesting module that validates the pipeline's signals by replaying
them over price history and measuring forward returns and a portfolio equity curve.

## Problem

The pipeline produces Buy / Hold / Watch signals (and an underlying technical
posture) but never validates them. There is no evidence that a past "Buy" or a
"Bullish" posture actually preceded above-baseline returns. Without that, every
change to the scoring model — including the recently merged configurable
technical-component registry — is unmeasurable.

## Goal

Provide an offline-capable, statistically honest backtest that answers two
questions:

1. **Event study (core):** Does a signal predict forward returns *above a
   baseline*?
2. **Portfolio simulation (layer):** Would acting on the signal have produced a
   competitive equity curve vs. a benchmark after realistic costs?

Non-goals (v1, YAGNI): walk-forward parameter *optimization*, intraday data,
shorting, options, live trading.

## Scope of what is validated

Per decision, build **both** modes, clearly separated:

- **`technical` (default, the honest core):** backtests only the registry-driven
  technical posture/score. Computed purely from price/volume history → **zero
  lookahead bias**, runs fully offline from cached history, and directly
  validates the configurable component work.
- **`composite` (optional, behind a flag):** backtests the fused
  `0.70·fundamental + 0.30·technical` Buy/Hold/Watch signal. yfinance exposes
  only *today's* fundamentals, so past composites apply current financials to
  past prices = **lookahead bias**. This mode is useful for sanity-checking the
  live product only; it prints a loud caveat and stamps the bias warning onto
  every output artifact.

## Architecture

Chosen approach: **a new `backtest.py` module + `run_backtest()` orchestrator**
mirroring `pipeline.run()`. Rejected alternatives: a `mode=` flag on
`pipeline.run()` (muddies the current-snapshot, server-callable contract); a
`backtest/` subpackage (heavier than v1 needs — easy to graduate to later if it
grows).

Reuse: `ingest` (fetch history), `indicators.add_indicators`,
`signals.compute_technical_posture` / `generate_signals`. New output via the
existing `outputs` exporter pattern and a new `charts.build_backtest_report()`.
A new `stock-analysis backtest` CLI subcommand drives it.

### The point-in-time correctness requirement (critical)

Posture must be recomputed **as it would have looked on each past date**, using
only data up to that bar. Most indicators are already causal (EMA/RSI/MACD read
trailing data), but two parts of the current code peek at the future and MUST be
handled:

- `indicators.add_indicators` derives `ENV_UP`/`ENV_DOWN` from the 2.5/97.5
  percentiles of the **entire** series → an old bar's envelope already "knows"
  the full future distribution.
- `fit_regression_channel` and `find_support_resistance` fit over whatever window
  they are handed.

The `near_lower_env`, `trend_up`, and `ema50_up` components depend on these, so a
naive replay leaks the future and produces "too good to be true" results.

**Mechanism:** the backtest walks each ticker bar-by-bar and, at each evaluation
date `t`, computes posture on the trailing slice `df.iloc[:t+1]`, re-running
`add_indicators` on that slice so envelope percentiles and channels see only the
past. This is O(N²) per ticker — acceptable for a watchlist of dozens over 3–5y
(hundreds of bars). **Optional optimization** (only if needed): compute the
causal columns once and recompute only the three window-dependent components per
step.

## Signal timeline & entry events

The bar-by-bar walk yields a per-ticker **posture timeline**
(date → posture, tech_score, composite).

To avoid fake sample size from autocorrelated consecutive Bullish days, the core
unit is an **entry event = a transition into Bullish/Buy** (de-overlapped).
Outputs report **both** the de-overlapped event count (the honest *n*) and the
naive daily count (for comparison).

## Event study (statistical core)

For each entry event, record forward **1m / 3m / 6m** returns (entry at
next-day open). Aggregate per bucket (Bullish vs Neutral vs Bearish; in composite
mode Buy vs Hold vs Watch):

- hit-rate (% positive), mean & median forward return, expectancy
- **baseline-relative:** the same stats minus the all-dates average return and
  minus SPY's same-window return, isolating *edge over simply being invested*
- **year-by-year** breakdown for regime robustness (methodology requires positive
  expectancy in a majority of years)

## Portfolio simulation (the money curve)

Reuses the same event timeline. Defaults, all configurable:

- **Equal-weight**, max *N* concurrent positions (default `N = 10`); enter
  next-day open on a fresh Buy/Bullish event. Events beyond the cap are skipped
  (not queued).
- **Exit** when posture leaves Bullish *or* a fixed max-hold elapses (whichever
  comes first).
- **Costs:** commission + conservative slippage baked in (default ~10 bps/side,
  deliberately pessimistic), with a `--slippage-mult` knob to stress at 1.5–2×.
- **Output:** equity curve + drawdown vs SPY, plus the summary metrics the
  `backtest-expert` evaluation script consumes (total trades, win-rate, avg
  win/loss, max drawdown, years tested, expectancy), so a run can pipe straight
  into that Deploy/Refine/Abandon scorer.

## Outputs, API, CLI

- **`BacktestResults` dataclass** (mirrors `Results`): `event_stats`,
  `portfolio_curve`, `summary`, `per_ticker`, `config`.
- **Excel:** new sheets through the existing exporter pattern
  (`Backtest Summary`, `Event Study`).
- **HTML:** new `charts.build_backtest_report()` → Plotly (equity curve +
  forward-return distributions), saved like the dashboards.
- **CLI:** `stock-analysis backtest --period 5y --scope technical|composite
  --horizon 3m --max-hold 60d --slippage-mult 1.0`. Composite mode prints a loud
  lookahead caveat and stamps it on the report.

## Testing (fully offline, matches the existing suite)

- **Lookahead guard (critical):** synthetic series with a known future spike;
  assert posture on an early date is identical whether or not future bars exist →
  proves no leakage.
- De-overlapping logic, forward-return math, drawdown/equity math, and
  baseline subtraction — all on synthetic OHLCV fixtures, no network/yfinance.

## Robustness & scope guardrails

- Robustness in v1 comes from the **year-by-year** breakdown plus a
  **slippage-multiplier sweep**, not walk-forward optimization (the components are
  on/off — nothing continuous to optimize yet).
- Explicitly out of scope: walk-forward optimization, intraday, shorting,
  options, live execution.
