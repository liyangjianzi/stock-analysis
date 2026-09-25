"""Is the gate's fill systematically worse than a random bar's?

The gate fires on a bar that closed above the prior high; the fill is the NEXT
open. If that open is systematically gapped up relative to a random bar's, the
rule is paying an execution tax on every entry -- which would be a mechanical
drag, independent of whatever the indicator is measuring.
"""
import pickle
import numpy as np
import pandas as pd

import tune_harness as th
from memo import entries_for

rng = np.random.default_rng(5)
with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)

ent = entries_for(enriched)


def gaps(pairs):
    out = []
    for tk, i in pairs:
        f = enriched[tk]
        c = f["Close"].to_numpy(float); o = f["Open"].to_numpy(float)
        a = f["ATR14"].to_numpy(float)
        if i + 1 >= len(f) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        out.append({"gap_pct": o[i + 1] / c[i] - 1,
                    "gap_atr": (o[i + 1] - c[i]) / a[i]})
    return pd.DataFrame(out)


real = [(tk, i) for tk, pos in ent.items() for i in pos]
elig = {tk: (th.MIN_BARS, len(f) - 2) for tk, f in enriched.items()}
tks = list(enriched)
rand = [(tk, int(rng.integers(*elig[tk]))) for tk in rng.choice(tks, 40000)]

g, r = gaps(real), gaps(rand)
print("=== overnight gap from signal close to the fill (next open) ===")
for name, d in (("gate entries", g), ("random bars", r)):
    print(f"  {name:<14} n={len(d):>6}  mean gap = {d['gap_pct'].mean():+.4%}  "
          f"median {d['gap_pct'].median():+.4%}  in ATRs: {d['gap_atr'].mean():+.4f}")
diff = g["gap_pct"].mean() - r["gap_pct"].mean()
se = np.sqrt(g["gap_pct"].var(ddof=1)/len(g) + r["gap_pct"].var(ddof=1)/len(r))
print(f"\n  gate minus random = {diff:+.4%}  (SE {se:.4%}, t={diff/se:.1f})")
print(f"  in ATR terms      = {g['gap_atr'].mean() - r['gap_atr'].mean():+.4f} ATR")

# what that costs in R: risk per share is ~ (entry - stop); express drag in R
print("\n=== what the extra gap costs, expressed in R ===")
rows = []
from stockanalysis.tradeplan import build_trade_plan
for tk, i in [(t, j) for t, j in real[:2500] if j + 1 < len(enriched[t])]:
    f = enriched[tk]
    p = build_trade_plan(f.iloc[: i + 1])
    if not np.isfinite(p["stop"]):
        continue
    risk = p["entry"] - p["stop"]
    o = f["Open"].to_numpy(float)[i + 1]
    if risk > 0 and np.isfinite(o):
        rows.append((o - p["entry"]) / risk)
print(f"  gate: fill-vs-plan-entry slippage = {np.mean(rows):+.4f} R "
      f"(n={len(rows)} sampled)")
