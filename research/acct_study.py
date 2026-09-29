"""Do account-level risk rules help? A heat cap and a drawdown throttle (2026-09-28).

Offline (the research cache). Pre-declared before any result was looked at:

* **Account.** The trade plan's sizing: 1% of closed-trade equity risked per
  trade, 20% max position, no margin (``account.simulate_account`` defaults).
* **Rules.**
  - Heat cap: skip an entry that would put more than H% of equity at risk across
    open positions. With no margin and a 20% position cap the book rarely holds
    more than ~5% at risk, so H sweeps 2, 3, 4, 5% and the share of entries each
    cap actually blocks is printed (a cap that never binds tests nothing).
  - Drawdown throttle: halve the risk per trade while equity is D% below its
    peak, D in 5, 10, 15%.
* **Measure.** A fresh account on each half (2016-21 and 2022-26, split
  2022-01-01), so one regime can't carry the other. A rule *wins* a half when it
  **dominates** the no-rule account: a shallower worst drawdown **and** a higher
  CAGR. (Not CAGR / |drawdown|: that ratio flips meaning when CAGR is negative,
  and a half may lose money.) A rule that cuts both is a trade-off, and the same
  trade-off is available by risking less, printed alongside at 0.5%.
* **Ships only if** it wins **both halves**, on the gate's trades **and** on the
  ticker-matched random entries (5 reps) under the same rule — a risk-control
  rule should not depend on the entry signal — at every neighbouring setting
  (plateau, not peak).

Everything here is on today's S&P 500 list (survivorship: flattered), and the
equity is closed-trade, so drawdowns are floors.

**Post-hoc addition (after the first run):** which of the ~23 simultaneous
setups fit in the account turned out to move CAGR ~0.5 pts and the worst
drawdown ~2.5 pts between two orderings. So the same dominance test is also run
on the *median over 20 orderings* of same-day entries — labelled secondary; the
single-ordering test above stays the primary verdict. The null is 5 reps, i.e.
~5x the gate's signal density, so compare each series only with itself.

    python acct_study.py | tee acct_study.log      # ~6 min
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from stockanalysis import backtest as bt, cache, config  # noqa: E402
from stockanalysis.account import simulate_account, summarize  # noqa: E402

UNIVERSE = HERE.parent / "data" / "universe_sp500.csv"
SPLIT = pd.Timestamp("2022-01-01")
TRADES_PKL = HERE / "acct_trades.pkl"          # gitignored; delete to rebuild
SEEDS = 20
HEAT_CAPS = (0.02, 0.03, 0.04, 0.05)
THROTTLES = (0.05, 0.10, 0.15)


def halves(trades):
    first = [t for t in trades if pd.Timestamp(t.entry_date) < SPLIT]
    return first, [t for t in trades if pd.Timestamp(t.entry_date) >= SPLIT]


def run(trades, seeds: int = 1, **rule):
    """Per half: seed 0 when ``seeds == 1`` (primary), else the median CAGR and
    worst drawdown over ``seeds`` orderings (secondary)."""
    out = {}
    for label, part in zip(("first", "second"), halves(trades)):
        sims = [simulate_account(part, seed=k, **rule) for k in range(seeds)]
        out[label] = {"cagr": float(pd.Series([x["cagr"] for x in sims]).median()),
                      "dd": float(pd.Series([x["max_drawdown"] for x in sims]).median()),
                      "taken": sims[0]["n_taken"], "heat_skips": sims[0]["skipped"]["heat"],
                      "signals": sims[0]["n_signals"]}
    return out


def load_trades():
    if TRADES_PKL.exists():
        with open(TRADES_PKL, "rb") as f:
            return pickle.load(f)
    with cache.connect() as conn:
        prices = cache.load_universe(conn, sorted(config.load_watchlist_csv(UNIVERSE)))
    gate = bt.build_results_from_prices(prices, exits="plan", null_reps=0, split_at=SPLIT).trades
    null = bt.random_entry_trades(prices, gate, reps=5, seed=0,
                                  max_hold_bars=config.MAX_HOLD_BARS, cost_bps=10.0,
                                  slippage_mult=1.0)
    with open(TRADES_PKL, "wb") as f:
        pickle.dump((gate, null), f)
    return gate, null


def fmt(res) -> str:
    return "  ".join(f"{h}: CAGR {r['cagr']:+.1%}, worst DD {r['dd']:.1%}, "
                     f"took {r['taken']:,}" for h, r in res.items())


def dominates(rule, base) -> bool:
    return rule["dd"] > base["dd"] and rule["cagr"] > base["cagr"]


def main() -> None:
    t0 = time.time()
    gate, null = load_trades()
    print(f"gate {len(gate):,} trades, null {len(null):,} (5 reps)  [{time.time() - t0:.0f}s]\n")

    whole = summarize(gate, null, {}, seeds=SEEDS)
    print("Whole period, 1% risk (the CLI's --account-sim), and the drawdown odds of the")
    print("trades the gate's account took (realized per-trade returns, month-block bootstrap):")
    for label in ("gate", "null"):
        w = whole[label]
        print(f"  {label:4} CAGR {w['cagr']:+.1%}  worst DD {w['max_drawdown']:.1%}  "
              f"streak {w['longest_losing_streak']}  took {w['n_taken']:,} of {w['n_signals']:,}")
    sp = whole["spread"]
    print(f"  gate across {SEEDS} orderings (5th/median/95th): CAGR "
          + " / ".join(f"{v:+.1%}" for v in sp["cagr"]) + "  worst DD "
          + " / ".join(f"{v:.1%}" for v in sp["max_drawdown"]) + "  streak "
          + " / ".join(f"{v:.0f}" for v in sp["longest_losing_streak"]))
    print(whole["odds"].to_string(index=False, float_format=lambda v: f"{v:.3f}"), "\n")

    base = {"gate": run(gate), "null": run(null)}
    print("No rule (1% risk, 20% max position, no margin):")
    for s in ("gate", "null"):
        print(f"  {s:4} {fmt(base[s])}")
    print("Just risking less (0.5%, no rule) — the trade-off any rule must beat:")
    for s, trades in (("gate", gate), ("null", null)):
        print(f"  {s:4} {fmt(run(trades, risk_pct=0.005))}")

    verdicts = {}
    rules = ([("heat cap", f"{h:.0%}", {"heat_cap": h}) for h in HEAT_CAPS]
             + [("throttle", f"{d:.0%}", {"throttle_dd": d, "throttle_factor": 0.5}) for d in THROTTLES])
    for family, label, rule in rules:
        res = {s: run(trades, **rule) for s, trades in (("gate", gate), ("null", null))}
        better = {s: all(dominates(res[s][h], base[s][h]) for h in ("first", "second"))
                  for s in res}
        verdicts[(family, label)] = better["gate"] and better["null"]
        print(f"\n{family} {label}:")
        for s in ("gate", "null"):
            skips = sum(res[s][h]["heat_skips"] for h in res[s])
            sigs = sum(res[s][h]["signals"] for h in res[s])
            extra = f"  [heat blocked {skips / sigs:.1%} of signals]" if family == "heat cap" else ""
            print(f"  {s:4} {fmt(res[s])}  -> {'dominates in both halves' if better[s] else 'does not dominate in both'}{extra}")

    print("\nVERDICTS (passes = dominates the no-rule account in both halves, on gate AND random entries):")
    for (family, label), ok in verdicts.items():
        print(f"  {family:9} {label:4}: {'passes' if ok else 'fails'}")
    for family in ("heat cap", "throttle"):
        passes = [ok for (f, _), ok in verdicts.items() if f == family]
        print(f"  {family}: {'PLATEAU — ships' if all(passes) else 'does not ship'} "
              f"({sum(passes)} of {len(passes)} settings pass)")

    print(f"\nSECONDARY (post-hoc): the same test on the median of {SEEDS} orderings")
    base_m = {s: run(tr, seeds=SEEDS) for s, tr in (("gate", gate), ("null", null))}
    for family, label, rule in rules:
        res = {s: run(tr, seeds=SEEDS, **rule) for s, tr in (("gate", gate), ("null", null))}
        ok = all(dominates(res[s][h], base_m[s][h]) for s in res for h in ("first", "second"))
        print(f"  {family:9} {label:4}: {'passes' if ok else 'fails'}   gate {fmt(res['gate'])}")
    print(f"  (no rule, median)   gate {fmt(base_m['gate'])}")
    print(f"\n[{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
