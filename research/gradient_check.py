"""Does the shape of each sweep replicate out of sample?

A single winning value is noise; a monotone gradient that survives the held-out
period is the only threshold evidence worth acting on.
"""
import pickle
import numpy as np
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)


def both(spec=None, params=None):
    tr = memo.trades(entries_for(enriched, spec, params))
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    out = {}
    for k, sub in (("train", tr[tr["year"] <= 2021]), ("test", tr[tr["year"] > 2021])):
        r = sub["r"].to_numpy(float)
        se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
        c = th.cluster_stats(sub)
        out[k] = (len(r), r.mean(), se, c["se_cluster"])
    return out


def show(title, axis, values, mk):
    print(f"\n=== {title} ===")
    print(f"{axis:>12} | {'TRAIN n':>8} {'exp':>8} | {'TEST n':>8} {'exp':>8} "
          f"{'95% CI (clustered)':>26}")
    for v in values:
        d = both(**mk(v))
        tn, te, _, _ = d["train"]
        sn, se_, _, cse = d["test"]
        lo, hi = se_ - 1.96 * cse, se_ + 1.96 * cse
        print(f"{v:>12} | {tn:>8} {te:>+8.4f} | {sn:>8} {se_:>+8.4f} "
              f"{f'[{lo:+.4f}, {hi:+.4f}]':>26}")
        memo.save()


show("pullback_zone: max (Close-EMA50)/ATR14", "max_atr",
     [0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
     lambda v: dict(params={"pullback_zone": {"max_atr": v}}))

show("trend_up: required EMA50 gain over 20 bars", "ema_gain",
     [0.0, 0.01, 0.02, 0.03, 0.04, 0.05],
     lambda v: dict(params={"trend_up": {"ema_gain": v}}))

show("trend_up: gain lookback bars", "gain_bars",
     [10, 15, 20, 30, 40, 60],
     lambda v: dict(params={"trend_up": {"gain_bars": v}}))
