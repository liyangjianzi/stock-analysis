# research/ — offline tuning harness (2026-09-16 pass)

Scratch research code for the signal-tuning pass documented in
`.claude/skills/tuning-signals/SKILL.md`. **Not part of the `stockanalysis`
package** and not imported by it. Everything runs offline against
`data/cache/prices.db`.

Run from this directory with the project venv active.

| File | What it does |
|---|---|
| `tune_harness.py` | Vectorized mirrors of the `signals.TECHNICAL_COMPONENTS` predicates, plus expectancy stats with **naive and month-cluster** error bars. |
| `verify_equivalence.py` | **Run this first** (needs no pickles). Asserts the vectorized gate equals `backtest.posture_timeline(mode="gate")` bar for bar, including entry de-overlapping. If it fails, every number below is void. Last run 2026-09-25: ALL MATCH. |
| `memo.py` | Memoizes trade outcomes by `(ticker, entry bar)`. The exit walk depends only on the entry bar, never on which gate selected it, so variants share results — a 66s full-universe run becomes seconds once warm. |
| `run_baseline.py` | Reproduces the shipped baseline (7,253 trades / 48.19% / +0.0305R on the 09-16 cache — the cache moves; the skill records the latest re-verification) and caches `enriched.pkl` + `baseline_trades.pkl`. |
| `sweep_entry.py`, `sweep_composition.py`, `sweep_regime.py` | Train-period (≤2021) sweeps of thresholds, gate composition, and a breadth regime filter. |
| `holdout.py` | The single held-out confirmation pass over the shortlist. |
| `gradient_check.py`, `extension_doseresponse.py` | Do the sweep *shapes* replicate out of sample? (Only `pullback_zone`'s did, and it still failed.) |
| `bootstrap_test.py` | Month-block bootstrap + Spearman rank correlation — the test that killed the pullback-tightness result. |
| `exit_lab.py`, `sweep_exits.py` | Exit policies on a **fixed** entry set (R-multiple targets, ATR trails, stop/target placement, max hold). `exit_lab.py` asserts it reproduces the shipped baseline before reporting anything. |
| `random_null.py` | **The most important one.** Runs randomly chosen bars through the same plan and exits. Showed the gate is *no better than random* (z=−2.25 vs the null's rep spread; clustered, the shipped `edge vs random` line puts it at −0.05R, CI incl. zero). |
| `indicator_families.py` | Seven structurally different entry families (breakout, momentum, MACD, bands, RSI cross, cross-sectional momentum) vs a period-matched random null. |
| `xsec_check.py` | Plateau + year-stability check on the one open lead, cross-sectional 12-1 momentum. |
| `gap_check.py` | Overnight gap from signal close to fill, gate vs random — the execution-drag hypothesis (real but only ~0.01R). |
| `final_checks.py` | The baseline's three error bars side by side, and the stop/target basis census that showed `ATR_STOP_MULT` is unreachable. |

`*.pkl` artifacts are rebuildable caches (gitignored). `run_baseline.py` writes
`enriched.pkl` + `baseline_trades.pkl`, which everything else reads — run it first.
Two scripts also need a sibling's output: `holdout.py` reads `breadth.pkl` (from
`sweep_regime.py`) and `bootstrap_test.py` reads `extension_trades.pkl` (from
`extension_doseresponse.py`). `memo.py` builds `trade_cache.pkl` on its own.

**Results:** no threshold or exit variant survived out of sample, and no indicator
family beat a random-entry null. The one unresolved lead is cross-sectional 12-1
momentum. See `.claude/skills/tuning-signals/SKILL.md` for the tables.

## Fundamental-screen study (2026-09-25) — `pit_*.py`

Tests the *other* half of the Buy rule, `fund_score >= 4`, which could not be
measured before because yfinance `.info` is today-only. Needs network once.

| File | What it does |
|---|---|
| `pit_fetch.py` | SEC XBRL `companyfacts` for the 503 names (~70 s; User-Agent carries a contact, <=8 req/s) + yfinance closes that are split- but not dividend-adjusted, for market cap. Writes `pit_facts.pkl`, `pit_prices.pkl`. |
| `pit_fundamentals.py` | One snapshot per ticker per filing date (~8 min), metrics rebuilt only from facts filed by then; `metrics_at` prices any `(ticker, date)` from the latest filing *strictly before* it. Writes `pit_snapshots.pkl`. |
| `pit_validate.py` | Today's snapshot vs the live `Fundamentals` sheet: per-test agreement 90-100%, score within 1 on 20/20 names, `>=4` agreement 95%. D/E runs low where Yahoo counts leases. |
| `pit_study.py` | A: monthly cross-section, `>=4` minus `<4`, sector-neutral, Newey-West. B: the gate's plan trades split by entry-date score, vs the random-entry null (5 reps). Output in `pit_study.log`. |

Run order: `SEC_USER_AGENT="<app> <email>" python pit_fetch.py` -> `pit_fundamentals.py` -> `pit_validate.py` -> `pit_study.py`.

**Result: the screen selects nothing.** Pre-declared primary tests, both failed:

- A. `>=4` minus `<4`, sector-neutral 3m: **-0.27% [-0.88, +0.34]**, halves -0.52% / +0.04%.
  Rank IC -0.007. No cutoff from `>=2` to `>=6` is positive. At 12m the `>=4` names
  *trail* by 2.7% (p=0.04, one of many tests — read as "not positive", not as an edge).
- B. Buy (`gate AND >=4`) **+0.028R** vs gate with `<4` +0.032R: difference
  **-0.004R [-0.111, +0.103]**; halves -0.066R / +0.062R. Against random entries on
  `>=4` names: -0.015R [-0.110, +0.080].
- Per test (sector-neutral 3m, pass minus fail): only revenue growth is positive in
  both halves (+0.68%, p=0.051); FCF>0 is *negative* (-1.77%, p=0.004); dividend
  yield negative (-0.79%, p=0.066).
- The live dividend-yield unit bug (yields <=1% pass the 1.5% test) changes none of this.

**Caveat that limits the conclusion:** the universe is *current* S&P 500 members, and
survivorship bias flatters exactly the names a quality screen rejects (past
negative-FCF / no-dividend firms that went on to succeed; the ones that failed are
absent). So this shows the screen doesn't rank *survivors*; it can't show whether it
avoids blow-ups that left the index. That needs point-in-time index membership.
Coverage: 475 of 502 names/month have a filing in the last 200 days; BRK-B excluded
(class A/B share counts break market cap); 60 names have partial SEC history
(IPOs, spin-offs, re-registered CIKs such as XOM, BLK) and are skipped while uncovered.
