"""Does skipping gate entries just before earnings help? (2026-09-27)

Offline after ``earn_fetch.py``. Pre-declared before any result was looked at:

* **Rule.** Blackout(K): skip an entry when a reaction day (the session a report
  gaps, see earn_fetch.py) falls in ``(signal day, signal day + K sessions]``.
  That includes the fill day, so a report the night of the signal is skipped.
* **Primary test.** K = 10, the live report's ``EARNINGS_WARN_DAYS``. The rule
  ships only if the filtered gate **beats the ticker-matched random-entry null,
  filtered the same way, in both halves** (split 2022-01-01) — the
  tuning-signals bar. Beating the unfiltered gate is not enough: that could
  only mean the filter drops bad *bars*, which a random entry would get too.
* **Plateau.** K in {3, 5, 10, 15, 20}; a lone good K is noise.
* **Diagnostics** (not tests): R of trades whose walk crossed a report vs not,
  and how over-represented report days are among ``stop_gap`` exits.

Scope: trades on names with earnings coverage, entered on or after the name's
first known reaction day and before its last (so "the next report" is known).
Gate and null are cut to the same scope. The cache is today's S&P 500, so every
number carries the survivorship caveat in the tuning-signals skill.

    python earn_study.py | tee earn_study.log      # ~7 min
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from stockanalysis import backtest as bt, cache, config, robustness  # noqa: E402

UNIVERSE = HERE.parent / "data" / "universe_sp500.csv"
SPLIT = "2022-01-01"
PRIMARY_K = 10
KS = (3, 5, 10, 15, 20)
NULL_REPS = 5


def load():
    with open(HERE / "earn_dates.pkl", "rb") as f:
        earn = {tk: np.array(d, dtype="datetime64[ns]") for tk, d in pickle.load(f).items()}
    with cache.connect() as conn:
        prices = cache.load_universe(conn, sorted(config.load_watchlist_csv(UNIVERSE)))
    return earn, prices


def in_scope(t, earn) -> bool:
    days = earn.get(t.ticker)
    if days is None or not len(days):
        return False
    ts = np.datetime64(pd.Timestamp(t.entry_date))
    return days[0] <= ts < days[-1]


def next_report(t, earn):
    days = earn[t.ticker]
    ts = np.datetime64(pd.Timestamp(t.entry_date))
    return pd.Timestamp(days[np.searchsorted(days, ts, side="right")])


def blacked_out(t, earn, k: int) -> bool:
    return next_report(t, earn) <= pd.Timestamp(t.entry_date) + pd.offsets.BDay(k)


def crossed_report(t, earn) -> bool:
    return next_report(t, earn) <= pd.Timestamp(t.exit_date)


def on_report_day(t, earn) -> bool:
    return np.datetime64(pd.Timestamp(t.exit_date)) in earn[t.ticker]


def fmt(d: dict) -> str:
    if not d or not np.isfinite(d.get("exp_r", np.nan)):
        return "n/a"
    return f"{d['exp_r']:+.3f}R [{d['ci_lo']:+.3f}, {d['ci_hi']:+.3f}]"


def section(label, gate, null):
    ev = robustness.evaluate(gate, null, SPLIT)
    print(f"  {label:<14} n={len(gate):>5}")
    for part in ("all", "first", "second"):
        s = ev[part]
        print(f"    {part:<6} gate {fmt(s['gate'])}   null {fmt(s['null'])}   "
              f"edge {fmt(s['edge'])}  {s['edge'].get('verdict', '')}")
    return ev


def main() -> None:
    t0 = time.time()
    earn, prices = load()
    print(f"{len(earn)} names with earnings dates; {len(prices)} cached names")

    r = bt.build_results_from_prices(prices, exits="plan", null_reps=0, split_at=SPLIT)
    null = bt.random_entry_trades(prices, r.trades, reps=NULL_REPS, seed=0,
                                  max_hold_bars=config.MAX_HOLD_BARS, cost_bps=10.0,
                                  slippage_mult=1.0)
    gate = [t for t in r.trades if in_scope(t, earn)]
    null = [t for t in null if in_scope(t, earn)]
    print(f"gate trades {len(r.trades)} -> {len(gate)} in scope; "
          f"null {len(null)} in scope ({NULL_REPS} reps)  [{time.time() - t0:.0f}s]\n")

    print("Baseline (no blackout):")
    section("unfiltered", gate, null)

    print(f"\nBlackout(K) — primary K={PRIMARY_K}; gate and null filtered alike:")
    results = {}
    for k in KS:
        g = [t for t in gate if not blacked_out(t, earn, k)]
        n = [t for t in null if not blacked_out(t, earn, k)]
        results[k] = section(f"K={k}{' *' if k == PRIMARY_K else ''}", g, n)

    prim = results[PRIMARY_K]
    ships = all(prim[p]["edge"].get("verdict") == "beats random entry" for p in ("first", "second"))
    print(f"\nPRIMARY (K={PRIMARY_K}): {'SHIPS' if ships else 'does not ship'} — "
          f"first half: {prim['first']['edge'].get('verdict')}; "
          f"second half: {prim['second']['edge'].get('verdict')}")

    print("\nDiagnostics:")
    for label, trades in (("gate", gate), ("null", null)):
        crossed = [t for t in trades if crossed_report(t, earn)]
        clear = [t for t in trades if not crossed_report(t, earn)]
        c1, c2 = robustness.cluster_expectancy(crossed), robustness.cluster_expectancy(clear)
        print(f"  {label}: walk crossed a report {len(crossed):>5} trades {fmt(c1)}   "
              f"did not {len(clear):>5} {fmt(c2)}")
        gaps = [t for t in trades if t.exit_reason == "stop_gap"]
        share = np.mean([on_report_day(t, earn) for t in gaps]) if gaps else np.nan
        base = np.mean([on_report_day(t, earn) for t in trades])
        print(f"  {label}: stop_gap exits on a report day {share:.1%} "
              f"(all exits: {base:.1%}; n stop_gap={len(gaps)})")
    print(f"\n[{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
