"""The one lead worth a follow-up: cross-sectional 12-1 momentum.

It was the only family whose test expectancy matched its train expectancy. Two
questions only: is the decile cutoff a plateau or a peak, and is it stable
year by year? If it is a peak, it dies here like everything else.
"""
import pickle
import numpy as np
import pandas as pd

import tune_harness as th
from exit_lab import run as walk_all

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
for tk, f in enriched.items():
    f.attrs["tk"] = tk

mom = pd.DataFrame({tk: f["Close"].shift(21) / f["Close"].shift(252) - 1
                    for tk, f in enriched.items()})
rank = mom.rank(axis=1, pct=True)


def ent_for(cut):
    out = {}
    for tk, f in enriched.items():
        rk = rank[tk].reindex(f.index).to_numpy(float)
        m = (rk >= cut) & (f["Close"].to_numpy(float) > f["Open"].to_numpy(float))
        out[tk] = th.entry_positions(np.nan_to_num(m, nan=0).astype(bool))
    return out


print("=== plateau check: momentum rank cutoff ===")
print(f"{'cutoff':>8} {'TRAIN n':>8} {'exp':>8} | {'TEST n':>8} {'exp':>8} "
      f"{'clustered 95% CI':>22}")
store = {}
for cut in [0.70, 0.80, 0.85, 0.90, 0.95, 0.98]:
    tr = walk_all(enriched, ent_for(cut))
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    a, b = tr[tr["year"] <= 2021], tr[tr["year"] > 2021]
    c = th.cluster_stats(b)
    store[cut] = tr
    print(f"{cut:>8.2f} {len(a):>8} {a['r'].mean():>+8.4f} | {len(b):>8} "
          f"{b['r'].mean():>+8.4f} "
          f"{'[{:+.4f}, {:+.4f}]'.format(c['lo'], c['hi']):>22}", flush=True)

print("\n=== year by year, cutoff 0.90 ===")
tr = store[0.90]
g = tr.groupby("year")["r"]
t = pd.DataFrame({"n": g.size(), "exp_r": g.mean().round(4)})
t["sign"] = np.where(t["exp_r"] > 0, "+", "-")
print(t.to_string())
print(f"\npositive years: {(t['exp_r'] > 0).sum()} of {len(t)}")
c = th.cluster_stats(tr)
r = tr["r"].to_numpy(float)
print(f"pooled: n={len(r)}  exp={r.mean():+.4f}R  "
      f"clustCI=[{c['lo']:+.4f}, {c['hi']:+.4f}]")
