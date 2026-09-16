---
name: tuning-signals
description: Use when changing any threshold in this repo's signal engine — the predicates in signals.py TECHNICAL_COMPONENTS (EMA50 slope, RSI3 dip, ATR pullback zone, volume multiple), which components are gating, DEFAULT_FUND_MIN, or the placement knobs in config.py (ATR_STOP_MULT, STOP_BUFFER_ATR, MIN_STOP_ATR, MIN_TARGET_ATR, MIN_RR). Also use when asked to "improve expectancy", "tune the gate", "make the signal profitable", "optimize the parameters", or when a backtest result is about to be reported as an improvement.
---

# Tuning Signals

This repo can price any parameter set in ~6 minutes over 7,253 trades. That makes
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
| SE, t, p | 0.0155, 1.97, **0.049** |
| 95% CI | **[+0.000, +0.061]R** |
| Positive years | **6 of 11** — negative in 2016 (n=5), **2021, 2022, 2023** consecutively, and 2026 (partial) |
| Gate fire rate | **0.66%** of bars (broad universe; a tech-heavy 21-name sample gives 1.12% — don't quote that one) |
| Score distribution | `0/5 4.9% · 1/5 50.6% · 2/5 35.1% · 3/5 8.5% · 4/5 0.8% · 5/5 0.1%` (21-name replay, 10,523 bars — the posture cutoffs are calibrated to *this*, so re-derive it if you change the registry) |

Any variant must beat **this**, on the same data, by more than its own error bar.

Verify these against a fresh run before trusting them — they were measured on
2026-09-15 and the cache moves. `git log --oneline -- src/stockanalysis/signals.py`
will show whether the engine changed underneath them.

## Required workflow

1. **Reproduce the baseline first.** If your harness doesn't return +0.030R on the
   full universe, the harness is wrong — fix that before believing any variant.
2. **Split before you look.** Tune on 2016–2021, confirm on 2022–2026. Report both.
   A variant that only works in-sample is not a variant, it is a coincidence.
3. **Report the interval, never the point estimate.** `expectancy ± 1.96·SE`.
   With ~7k trades SE ≈ 0.016R, so an improvement under ~+0.03R is inside the noise.
4. **Require a plateau.** Sweep each threshold across ≥5 neighbouring values. If
   RSI3<25 works and <22 / <28 don't, you found noise, not an edge.
5. **Count effective sample, not trades.** 7,253 trades cluster into ~118 months
   across correlated names. Divide by cluster, not by row.

```bash
stock-analysis backtest --exits plan --universe data/universe_sp500.csv   # ~6 min
```
Search variants in-memory instead — `compute_technical_posture(df, components=...)`
accepts a custom registry, so no source edit is needed to price an idea.

## Already tested — do not re-propose

- **R:R floor filter** (skip entries below 1.0/1.5/2.0/2.5 planned R:R). Expectancy
  stays ~0 at *every* threshold while the error bars widen; win rate falls exactly
  in step with the improving R:R. That is what no-edge looks like.
- **Fixed-horizon forward returns** as evidence. They showed +5.27% at 1m for a
  setup worth +0.03R, because they ignore the stop. Use `--exits plan`.

## Rationalization table

| Excuse | Reality |
|---|---|
| "p=0.049 is significant" | With ~8 knobs and one dataset, p just under 0.05 is the *expected* output of searching. It is not evidence. |
| "The point estimate went up" | 142 trades gave +0.034R; 7,253 gave +0.030R. Point estimates barely moved — only the CI shrank. Report the CI. |
| "I only changed one parameter" | You chose *which* one after seeing the data. That is still a search. |
| "It's better in 8 of 11 years" | Say which years, and whether the losing ones are consecutive. 2021–23 being all-negative is regime dependence, not variance. |
| "Survivorship bias makes it conservative" | Backwards. Current-constituent bias *inflates* a long-only result. A flattered measurement still showing ~0 is worse news, not better. |
| "The user needs it profitable" | Then the honest answer is that it isn't, not a number that will lose their money more slowly. |

## Red flags — stop

- Reporting a tuned number without a held-out period
- Reporting expectancy without SE or CI
- A winning value with losing neighbours (peak, not plateau)
- "Let me try a few more combinations" after a variant already failed
- Changing what counts as an entry *and* the exits in the same comparison

**If the honest result is "no improvement", that is the deliverable.** This repo
already has a properly-powered measurement saying the setup is worth ~0. Confirming
that costs nothing; fabricating an improvement costs real money.
