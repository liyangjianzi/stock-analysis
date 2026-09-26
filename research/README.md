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
