---
name: tuning-signals
description: Use when changing any threshold in this repo's signal engine — the predicates in signals.py TECHNICAL_COMPONENTS (EMA50 slope, RSI3 dip, ATR pullback zone, volume multiple), which components are gating, DEFAULT_FUND_MIN or the screen_fundamentals thresholds (P/E, growth, debt/equity, dividend yield, FCF), or the placement knobs in config.py (ATR_STOP_MULT, STOP_BUFFER_ATR, MIN_STOP_ATR, MIN_TARGET_ATR, MIN_RR). Also use when asked to "improve expectancy", "tune the gate", "make the signal profitable", "optimize the parameters", when proposing a new entry rule or strategy family (momentum, breakout, mean reversion, another indicator), or when a backtest result is about to be reported as an improvement.
---

# Tuning Signals

This repo can price any parameter set in ~5 minutes over ~7,250 trades. That makes
**finding a better number trivially easy and almost always meaningless** — with
~8 tunable thresholds and one cached dataset, a search will always surface a
combination that looks better in-sample. The discipline here is not about how to
search. It is about what you are allowed to conclude.

**Changing a threshold and reporting the improved number is the failure mode.**

## The measured baseline — don't re-derive it

Gate entries, plan exits, 503 S&P 500 names, 10y (`b93b46d`):

| | |
|---|---|
| Trades / win rate | 7,253 / 48.2% |
| Expectancy | **+0.030R** |
| Naive SE, t, p | 0.0155, 1.97, 0.049 — **do not quote this one, see below** |
| **Cluster-robust SE, t, p** | **0.0348, 0.88, 0.39** (clustered by calendar month) |
| **95% CI** | **[−0.038, +0.099]R** — a month-block bootstrap agrees: [−0.036, +0.098], p=0.386 |
| Train / test halves | 2016–21: **+0.091R** (n=3,643) · 2022–26: **−0.030R** (n=3,610) |
| Positive years | **6 of 11** — negative in 2016 (n=5), **2021, 2022, 2023** consecutively, and 2026 (partial) |
| Gate fire rate | **0.66%** of bars (broad universe; a tech-heavy 21-name sample gives 1.12% — don't quote that one) |
| Score distribution | `0/5 4.9% · 1/5 50.6% · 2/5 35.1% · 3/5 8.5% · 4/5 0.8% · 5/5 0.1%` (21-name replay, 10,523 bars — the posture cutoffs are calibrated to *this*, so re-derive it if you change the registry) |

**Every universe number in this skill is on today's constituents**
(`data/universe_sp500.csv`), and that list is measurably flattering: an equal-weight
index rebuilt from it beats RSP, the real equal-weight S&P 500 ETF, by **+5.0%/yr**
over 2006–26, against +1.1%/yr (about RSP's fee) when rebuilt from point-in-time
membership (`research/mom_*.py`). The gate's +0.030R is flattered too; by how much
in R is unmeasured.

### The benchmark is not zero — it is a random entry

Running **randomly chosen bars** through the same trade plan and exit walk gives:

| Null (same plan, same exits, 12 reps) | Expectancy | Win rate |
|---|---|---|
| Unconditional random ticker + bar | **+0.063R** | 50.2% |
| Date-matched (real dates, random ticker) | +0.042R | 49.2% |
| Ticker-matched (real ticker, random bar) | +0.073R | 50.1% |
| **The shipped gate** | **+0.030R** | 48.2% |

**The gate is no better than random, and its point estimate sits below it.** The
12-rep study above put it at z = −2.25 against the ticker-matched null (−1.87
unconditional), but that z divides by the spread *between null draws* (sd 0.019R)
and ignores the gate's own sampling error. Clustered by month — how the shipped
`edge vs random` line computes it — the edge is **−0.050R, 95% CI [−0.140, +0.040],
p=0.28** (2026-09-25 re-verification, 1 rep): below random, but not distinguishably so. **Don't quote "worse than
random" or z = −2.25.** Either way the positive expectancy in this repo comes from
the trade plan's stop/target geometry, not from the entry rule, which adds nothing
to it. So "beat +0.030R" is the wrong bar. **A variant must beat ≈+0.06R, the
random-entry null, measured on the same period.** Anything that can't clear it is
indistinguishable from picking bars with a dice roll, however good its backtest looks.

Only ~0.01R of the gap is execution (gate entries gap up +0.114% overnight to the
fill vs +0.056% for random bars, t=3.5 — real but small). The rest is selection —
the gate's bars score below average, though the clustered CI can't rule out that
the gap is zero.

Any variant must beat **that null**, on the same data, by more than its own error bar.

**The honest read of that table: the edge is not distinguishable from zero.** The
per-trade SE treats 7,253 correlated entries as 7,253 independent draws; they are
really ~118 monthly clusters, and clustering triples the SE and drops t from 1.97
to 0.88. The `p=0.049` that earlier passes reported is an artifact of the wrong
error bar, not a marginal result. Everything below is therefore about *not making
it worse*, not about finding the number that unlocks it.

Verify these against a fresh run before trusting them — they were measured on
2026-09-15 and the cache moves. `git log --oneline -- src/stockanalysis/signals.py`
will show whether the engine changed underneath them.

**Last re-verified 2026-09-25** — engine unchanged, cache rebuilt with `--full` after
the adjustment-drift fix, bars cut at 2026-09-24: 7,262 trades / 48.1%, **+0.030R,
95% CI [−0.039, +0.098], p=0.39** (117 months); halves +0.091R (n=3,624) / −0.031R
(n=3,638); 6 of 10 years positive (the 10y window has slid past 2016; still
negative 2021–23 and 2026). Same conclusions. Two things move between re-runs and
are not signal: the 1-rep random null (+0.073 → +0.075 → +0.080R across runs —
pass `--null-reps 12`, ~4 min more, if the edge line is what you're reading), and a few trades
per year from Yahoo restating adjusted history (COR, IVZ, MCK moved up to 1%).

## Required workflow

The shipped backtest now does the arithmetic for steps 1–3 and 5 — use it rather
than a hand-rolled harness, and quote what it prints:

```bash
stock-analysis backtest --exits plan --universe data/universe_sp500.csv --period 10y --split 2022-01-01   # ~5 min
```

1. **Reproduce the baseline first.** It must print ~+0.030R on the full universe.
   If your variant's harness doesn't reproduce that, the harness is wrong — fix it
   before believing any variant. If the shipped command itself drifts with
   `signals.py` unchanged, suspect the cache before the code: nightly top-ups
   rebuild names a dividend/split re-adjusted, but only
   `stock-analysis cache --full` catches Yahoo restating older history.
2. **Split before you look.** Tune on 2016–2021, confirm on 2022–2026. The
   `halves (split 2022-01-01)` line is exactly this; report both halves.
   A variant that only works in-sample is not a variant, it is a coincidence.
3. **Report the interval, never the point estimate.** The printed `95% CI` is
   clustered by month — SE ≈ 0.035R on ~7k trades, so an improvement under
   ~+0.07R is inside the noise. (The naive per-trade SE, 0.016R, is the wrong one.)
4. **Require a plateau.** Sweep each threshold across ≥5 neighbouring values. If
   RSI3<25 works and <22 / <28 don't, you found noise, not an edge.
5. **Beat the random entry, not zero.** The `edge vs random` line compares against
   the same plan walked from random bars of the same tickers. A variant whose
   verdict is not `beats random entry` has not shown an edge, however positive
   its expectancy.

### Running a variant in-memory

Don't edit source to try a variant — run it through
`backtest.build_results_from_prices`, which returns the same `robustness` block
the CLI prints. Two traps, both verified: it takes no `components=`, and
**patching a `config` knob does nothing** — `build_trade_plan` binds those as
defaults at import, so the run comes back bit-identical, which reads exactly
like "this knob doesn't matter". Patch the names `backtest` actually calls, and
vary entry and placement in **separate** runs:

```python
from functools import partial
from unittest import mock
from stockanalysis import backtest as bt, cache, config, signals
from stockanalysis.indicators import value_at
from stockanalysis.tradeplan import build_trade_plan

with cache.connect() as conn:
    prices = cache.load_universe(conn, list(config.load_watchlist_csv("data/universe_sp500.csv")))
run = lambda: bt.build_results_from_prices(prices, exits="plan", split_at="2022-01-01")

# entry variant: a predicate threshold (NaN compares False, i.e. fails, as shipped).
# Plan backtests enter on the *gating* components only, so this swap alone leaves
# every trade identical — dip_deep/vol_pattern move the score, never an entry. A
# flat sweep of a non-gating threshold is that, not robustness.
dip_22 = lambda df: value_at(df, "RSI3", 1) < 22
variant = [c._replace(predicate=dip_22) if c.name == "dip_deep" else c
           for c in signals.TECHNICAL_COMPONENTS]      # or c._replace(gating=...)
with mock.patch("stockanalysis.backtest.TECHNICAL_COMPONENTS", variant):
    r = run()

# placement variant: a trade-plan knob
with mock.patch("stockanalysis.backtest.build_trade_plan",
                partial(build_trade_plan, stop_buffer_atr=1.0)):
    r = run()

r.robustness["first"]["gate"], r.robustness["second"]["gate"]   # train / test
r.robustness["all"]["edge"]["verdict"]                           # vs random entry
```

For sweeps too big for ~5 min a variant, `research/` holds the 09-16 harness
(vectorized predicates, memoized exit walks — seconds per variant). It mirrors
the predicates by hand: run `research/verify_equivalence.py` first (needs no
pickles; ALL MATCH as of 2026-09-25) and again whenever `signals.py` changes, then
`run_baseline.py` to rebuild the gitignored pickles everything else reads.

## Already tested — do not re-propose

- **R:R floor filter** (skip entries below 1.0/1.5/2.0/2.5 planned R:R). Expectancy
  stays ~0 at *every* threshold while the error bars widen; win rate falls exactly
  in step with the improving R:R. That is what no-edge looks like.
- **Fixed-horizon forward returns** as evidence. They showed +5.27% at 1m for a
  setup worth +0.03R, because they ignore the stop. Use `--exits plan`.

### The 2026-09-16 pass — 8 entry variants, 26 exit variants, nothing survived

Train 2016–21 / test 2022–26, cluster-robust CIs. **Every** variant was positive on
train (+0.09R to +0.24R) and collapsed to ~0 on test. That uniformity *is* the
finding: 2016–21 was a bull regime in which nearly any confirming-bar entry paid,
so a train-only number measures the regime, not the rule.

| Variant | Train | Test |
|---|---|---|
| baseline gate | +0.091 | **−0.030** |
| + breadth filter (skip entries when <72% of the universe is above its EMA200) | +0.219 | **−0.012** |
| `trend_up` gain 2% → 0% (no trend-slope requirement) | +0.109 | **−0.029** |
| `pullback_zone` 1.0 → 0.5 ATR | +0.128 | +0.029 |
| `dip_deep` promoted to gating | +0.117 | +0.003 |
| drop `trend_up` (pullback + turn only) | +0.133 | +0.010 |
| drop `trend_up`, + breadth filter | +0.241 | +0.022 |
| `dip_deep` + `turn_confirm` only | +0.136 | +0.033 |

Specifically **do not re-propose**:

- **A market-regime / breadth filter.** Train says the edge lives in *weak* breadth
  (buy dips in a washed-out tape, +0.22R) and that filtering *for* strong breadth
  monotonically destroys it. None of it replicates: the same filter is −0.012R on
  test. The direction is not even stable — the low-breadth years in train (2018–20)
  were good and the low-breadth years in test (2022) were the worst in the sample.
- **`trend_up`'s two knobs (`ema_gain`, `gain_bars`).** Swept 0–7% and 10–60 bars.
  Test expectancy is *flat at ≈ −0.03R across the entire range* — the knobs carry
  no information. Train shows a tidy monotone rise with tighter `ema_gain` and a
  peak at `gain_bars=10` (+0.205R); that peak is the single worst cell out of
  sample (−0.093R). Textbook peak-not-plateau.
- **Tightening `pullback_zone`.** This is the *only* gradient that replicated in
  shape — expectancy falls monotonically as the zone widens, in both halves — and
  it still fails. Bucketing entries by extension above EMA50, the tightest bucket
  (≤0.25 ATR) is +0.111R pooled with a clustered CI of [+0.018, +0.204], the one
  interval in the whole study excluding zero. But out of sample alone it is
  +0.087R, **p=0.195**, and the underlying continuous relationship does not exist:
  Spearman(extension, R) = −0.011 on train and **−0.0045 on test** (p=0.68). A
  monotone bucket chart with no rank correlation behind it is a bucketing artifact.
- **Exit-side tuning of any kind** — fixed R-multiple targets (1R–4R), ATR trailing
  stops (1.0–3.0 ATR), `stop_buffer_atr` (0–1.0), `min_target_atr` (0.5–3.0),
  `max_hold` (10–126 bars). All 26 are ≤0 on test. Exits are the **most seductive
  overfit surface in this repo**: train expectancy can be dialled from +0.05R to
  **+0.38R** (target = 4R) or +0.31R (3-ATR trail) purely by choosing an exit, with
  no out-of-sample effect whatsoever. A train-only exit number proves nothing.

### Swapping in different indicators — also tested, also dead

The natural next proposal after tuning fails is "use different indicators."
Seven structurally distinct families were run as standalone gates through the
identical plan and exits (train / test, against a period-matched random null):

| Entry family | Train | Test |
|---|---|---|
| *random null (3 draws)* | *+0.093 … +0.112* | *+0.005 … +0.021* |
| shipped gate | +0.091 | **−0.030** |
| Donchian-20 breakout | +0.100 | +0.005 |
| 52-week-high momentum | +0.080 | −0.018 |
| MACD cross up | +0.122 | −0.011 |
| 2-sigma band reversion | +0.117 | +0.025 |
| RSI(14) cross up through 30 | +0.168 | +0.056 |
| EMA200 + RSI<40 | +0.083 | +0.001 |
| **cross-sectional 12-1 momentum, top decile** | **+0.065** | **+0.061** |

Every family lands **inside the random-entry null band** on test. The shipped gate
is the only one below it. Note the train column: the random null itself scores
+0.09 to +0.11 on train, i.e. **better in-sample than the shipped gate** — more
evidence that a train-only number here measures the 2016–21 regime and nothing else.

The reason is structural, not incidental: RSI, MACD, Stochastic, CCI, Williams %R
and friends are near-collinear transforms of one price series. Swapping among them
is a change of coordinates, not new information. **Do not re-propose a different
oscillator, a different moving-average pair, or a different band.**

The last row was the pass's one open lead — the only rule whose test expectancy
matched its train expectancy. It is now **closed**: measured as the portfolio it
is, on point-in-time membership, it loses to an equal-weight benchmark (next section).

### 12-1 momentum as a portfolio — tested point-in-time, dead (2026-09-25)

The row above walked trades through the plan's stops on today's 503 names.
`research/mom_*.py` (run order in `research/README.md`) measures it as the
strategy it is: at each month-end hold the top 10% of *that month's* S&P 500
members (fja05680 reconstruction) by 12-1 return, equal weight, for one month,
against the equal-weight average of all members, net of 10 bps a side. 2006–26,
of which 2006–15 had never been looked at.

| 12-1 top 10%, net excess vs equal weight | All | 2006–15 | 2016–21 | 2022–26 |
|---|---|---|---|---|
| Point-in-time membership | **−2.18%/yr [−7.00, +2.63]** | −5.80% | −4.53% | +8.77% |
| Today's constituents | +3.49%/yr [−1.81, +8.78] | −0.97% | +0.68% | +16.88% |

10 of 21 years positive; the net excess drew down −61%, worst month 2009-03
(−19.7%, a top decile of defensives into the junk rally). Specifically do not
re-propose:

- **Momentum with another lookback or cutoff.** All 16 cells (top 5/10/20/30% ×
  6-1, 9-1, 12-1, 12-0) are negative in 2006–15 and in 2016–21 and positive in
  2022–26 — a plateau of *regime*, not of edge. 2022–26 is when the AI and power
  names ran (May 2024's top decile: SMCI, VST, NVDA, CEG).
- **Momentum long-short.** Top minus bottom decile: −5.51%/yr [−17.5, +6.5].
- **2022–26 as evidence.** On today's list it reads +16.9%/yr at p=0.015 — the
  most significant number this repo has produced — and about half of it is
  survivorship: point-in-time it is +8.8%/yr, CI [−2.2, +19.8].

Yahoo prices only ~26% of the names that left the index, so the point-in-time
universe still misses 44% of members in 2006 (1% by 2026); the RSP gap bounds
that at ~1%/yr on the benchmark, too small to rescue −2.2%/yr.

### The fundamental screen — tested point-in-time, no effect (2026-09-25)

`backtest --scope composite` cannot test `DEFAULT_FUND_MIN`: yfinance `.info` is
today's data, so it scores 2016 bars with 2026 fundamentals. `research/pit_*.py`
(run order in `research/README.md`) rebuilds the six metrics from SEC filings as
filed — validated against live Yahoo at 90–100% per-test agreement, 95% on the
`>=4` cut — and scores them with the shipped `screen_fundamentals`.

| Pre-declared test | All | 2016–21 | 2022–26 |
|---|---|---|---|
| Cross-section: `>=4` minus `<4`, sector-neutral 3m return | −0.27% [−0.88, +0.34] | −0.52% | +0.04% |
| Gate trades: Buy (`gate AND >=4`) minus gate with `<4` | −0.004R [−0.111, +0.103] | −0.066R | +0.062R |
| Buy minus random entries on `>=4` names | −0.015R [−0.110, +0.080] | −0.037R | +0.009R |

The quality test adds nothing to the gate, and the gate adds nothing to a random
entry: **both halves of the Buy rule measure as zero.** Specifically do not re-propose:

- **Raising or lowering `DEFAULT_FUND_MIN`.** Every cutoff from `>=2` to `>=6` is
  ≤0 on the sector-neutral 3m spread. Gate-trade expectancy by score is flat
  (3: +0.069R, 4: +0.044R, 5: −0.000R, 6: +0.002R — every CI spans zero).
- **Re-weighting or dropping individual tests.** Pass minus fail, sector-neutral
  3m: only revenue growth is positive in both halves (+0.68%, p=0.051 — one of six,
  so it fails any multiple-test correction); FCF>0 (−1.77%) and dividend yield
  (−0.79%) point the wrong way. Keeping the winner after seeing all six is the
  same search as tuning a threshold.
- **Leaning on the fundamental score in `RANK_WEIGHTS` as if it ranked anything.**
  Monthly rank IC against 3m returns is −0.007.

Read the sign with care. For a quality screen's *relative* spread, current-
constituent bias runs **against** the screen: low-quality firms that failed are
absent, the ones that succeeded remain. So this shows the screen does not rank
*survivors*; it cannot show whether it avoids blow-ups that left the index. That
is the one open question, and it needs point-in-time index membership. Don't
claim the screen is harmful from these numbers either.

Since 2026-09-25 the dividend-yield unit fix in `ingest.fetch_fundamentals` fails
sub-1% payers on `Pass_Div`, so live scores dropped a point for 16 of the 31
watchlist names. A Buy/Hold/Watch count that shifted that day shifted because of
the fix, not the market.

### Structural facts found along the way

- **`config.ATR_STOP_MULT` is unreachable.** Sweeping it 1.0 → 3.0 returns
  *bit-identical* results, because **100.00%** of 7,253 entries take the structural
  stop — with `MIN_STOP_ATR=0.5` and 20 levels searched over 252 bars there is
  always a qualifying support. The ATR fallback never fires on this universe.
  (`min_target_atr`'s 2R fallback fires 0.86% of the time.) Treat it as dead
  config: don't "tune" it, and don't believe a result that claims it mattered.
  This was measured by passing the knob to `build_trade_plan` directly plus a
  stop-basis census — not by patching `config`, which is bit-identical for
  *every* knob (see "Running a variant in-memory").
- **30.2% of plans have a planned R:R below 1.0** (median 1.30). The gate finds
  entries whose nearest structure is worse than break-even on paper. Filtering
  those out is the already-dead R:R floor above — the point here is only that a
  low median R:R is the *normal* state of this plan, not a bug to chase.
- **`config.MIN_RR` filters nothing.** Its only reader is `report.py`, which
  flags plans below it in the HTML report. No entry, plan or backtest reads it,
  so changing it cannot move a result; skipping low-R:R entries is the dead R:R
  floor filter above.

## Rationalization table

| Excuse | Reality |
|---|---|
| "p=0.049 is significant" | It was never 0.049. Clustered by month it is **0.39**. And with ~8 knobs and one dataset, p just under 0.05 is the *expected* output of searching even when it is computed correctly. |
| "The point estimate went up" | 142 trades gave +0.034R; 7,253 gave +0.030R. Point estimates barely moved — only the CI shrank. Report the CI. |
| "I only changed one parameter" | You chose *which* one after seeing the data. That is still a search. |
| "It's better in 8 of 11 years" | Say which years, and whether the losing ones are consecutive. 2021–23 being all-negative is regime dependence, not variance. |
| "Survivorship bias makes it conservative" | Backwards. Current-constituent bias *inflates* a long-only result — by **~5%/yr** on an equal-weight S&P 500 (today's list vs RSP, 2006–26), and it turned 12-1 momentum from −2.2%/yr into +3.5%/yr. A flattered measurement still showing ~0 is worse news, not better. |
| "It's significant on the recent period" | 12-1 momentum, 2022–26, today's list: +16.9%/yr, p=0.015. Point-in-time over 2006–26: −2.2%/yr. One recent regime on a survivor list is the easiest significant number there is. |
| "The user needs it profitable" | Then the honest answer is that it isn't, not a number that will lose their money more slowly. |
| "Fundamentals are a quality filter — of course they help" | Measured point-in-time: Buy minus gate-with-`<4` is −0.004R [−0.111, +0.103]. Until a test on historical membership says otherwise, "they help" is a belief, not a result. |
| "Survivorship hides the screen's value, so it probably works" | It makes the test blind to blow-up avoidance; it does not turn a null into an edge. "Probably works" is exactly what the data failed to show. |

## Red flags — stop

- Reporting a tuned number without a held-out period
- Reporting expectancy without SE or CI
- A winning value with losing neighbours (peak, not plateau)
- "Let me try a few more combinations" after a variant already failed
- Changing what counts as an entry *and* the exits in the same comparison
- Quoting a `--scope composite` backtest as evidence about fundamentals — it
  applies today's `.info` to every past bar
- Reporting a cross-sectional or universe-wide rule measured only on today's
  constituents — `research/mom_fetch.py` has the point-in-time membership path

**If the honest result is "no improvement", that is the deliverable.** This repo
already has a properly-powered measurement saying the setup is worth ~0. Confirming
that costs nothing; fabricating an improvement costs real money.
