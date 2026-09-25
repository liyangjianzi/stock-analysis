"""Exit-side experiments on a FIXED entry set (the shipped gate).

The skill's red flag is changing entries and exits in the same comparison, so
every run here holds the baseline gate constant and varies only how the position
is closed. ``policy="plan"`` with default kwargs must reproduce the shipped
backtest bar for bar -- that equivalence is asserted before anything else runs.
"""
from __future__ import annotations

import pickle
import numpy as np
import pandas as pd

from stockanalysis.indicators import add_indicators
from stockanalysis.tradeplan import build_trade_plan
import tune_harness as th
from memo import entries_for


def walk(f, i, *, policy="plan", plan_kw=None, r_mult=None, trail_atr=None,
         max_hold_bars=63, cost_bps=10.0):
    """Walk one entry at bar ``i`` to its exit. Mirrors backtest.simulate_planned_trades
    (stop checked first within a bar; gaps fill at the open), with the target
    policy swapped out."""
    o = f["Open"].to_numpy(float); h = f["High"].to_numpy(float)
    lo = f["Low"].to_numpy(float); c = f["Close"].to_numpy(float)
    atr = f["ATR14"].to_numpy(float)
    n = len(f)
    if i + 1 >= n:
        return None
    plan = build_trade_plan(f.iloc[: i + 1], **(plan_kw or {}))
    stop, target = plan["stop"], plan["target"]
    if not np.isfinite(stop):
        return None
    cost = cost_bps / 10_000.0
    entry = o[i + 1] * (1 + cost)
    risk = entry - stop
    if not np.isfinite(entry) or risk <= 0:
        return None
    if policy == "rmult":
        target = entry + r_mult * risk
    elif policy == "trail":
        target = np.inf                       # exit only via the trailing stop
    if not np.isfinite(target) and policy != "trail":
        return None

    cur_stop = stop
    peak = entry
    exit_px = reason = at = None
    last = min(i + max_hold_bars, n - 1)
    for j in range(i + 1, last + 1):
        if o[j] <= cur_stop:
            exit_px, reason = o[j], "stop_gap"
        elif lo[j] <= cur_stop:
            exit_px, reason = cur_stop, "stop"
        elif np.isfinite(target) and o[j] >= target:
            exit_px, reason = o[j], "target_gap"
        elif np.isfinite(target) and h[j] >= target:
            exit_px, reason = target, "target"
        if reason:
            at = j
            break
        if policy == "trail" and np.isfinite(atr[j]):
            peak = max(peak, c[j])
            cur_stop = max(cur_stop, peak - trail_atr * atr[j])
    if reason is None:
        at, exit_px, reason = last, c[last], "time"
    net = exit_px * (1 - cost)
    return {"ticker": f.attrs.get("tk", ""), "entry_date": f.index[i],
            "r": (net - entry) / risk, "reason": reason, "bars": at - i}


def run(enriched, entries, **kw):
    rows = []
    for tk, pos in entries.items():
        f = enriched[tk]
        f.attrs["tk"] = tk
        for i in pos:
            t = walk(f, i, **kw)
            if t:
                rows.append(t)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys, time
    with open("enriched.pkl", "rb") as fh:
        enriched = pickle.load(fh)
    ent = entries_for(enriched)

    t0 = time.time()
    base = run(enriched, ent)
    s = th.stats(base)
    print(f"=== equivalence check (policy=plan, defaults) [{time.time()-t0:.0f}s] ===")
    print(f"n={s['n']} (shipped 7253)  win={s['win']:.3%} (shipped 48.187%)  "
          f"exp={s['exp_r']:+.4f}R (shipped +0.0305)")
    ok = s["n"] == 7253 and abs(s["exp_r"] - 0.03053) < 1e-3
    print("MATCH" if ok else "*** MISMATCH -- exit results below are void ***")
    if not ok:
        sys.exit(1)
