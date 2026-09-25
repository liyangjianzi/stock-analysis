"""Does a market-regime filter rescue the baseline gate?

Motivation: the baseline's losing years (2021-2023, 2026) are consecutive, which
is regime dependence rather than variance. Breadth is computed cross-sectionally
from the cached universe -- fraction of names trading above their own EMA200 on
that date -- which is causal (EMA200 uses only past bars), though it inherits the
universe's current-constituent bias like everything else here.
"""
import pickle, time
import numpy as np
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for, describe

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)
TRAIN_END = 2021


def build_breadth(enriched):
    """Series: fraction of the universe with Close > EMA200, per date."""
    above, total = {}, {}
    for tk, f in enriched.items():
        ok = (f["Close"] > f["EMA200"]) & f["EMA200"].notna()
        valid = f["EMA200"].notna()
        for d, a, v in zip(f.index, ok.to_numpy(), valid.to_numpy()):
            if v:
                above[d] = above.get(d, 0) + int(a)
                total[d] = total.get(d, 0) + 1
    s = pd.Series({d: above[d] / total[d] for d in total if total[d] >= 50})
    return s.sort_index()


t0 = time.time()
breadth = build_breadth(enriched)
breadth.to_pickle("breadth.pkl")
print(f"breadth built over {len(breadth)} dates in {time.time()-t0:.0f}s")
print(breadth.describe().round(3).to_string())
print()

base_entries = entries_for(enriched)
base = memo.trades(base_entries)
base["breadth"] = pd.to_datetime(base["entry_date"]).map(breadth)
base["year"] = pd.to_datetime(base["entry_date"]).dt.year
train = base[base["year"] <= TRAIN_END]

print("=== TRAIN: baseline entries filtered by breadth on the entry bar ===")
print(describe(train, "  no filter"))
for thr in [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
    print(describe(train[train["breadth"] >= thr], f"  breadth>={thr:.2f}"))

print("\n=== TRAIN: breadth decile of entry bar (is the relationship monotone?) ===")
q = pd.qcut(train["breadth"], 5, duplicates="drop")
g = train.groupby(q, observed=True)["r"]
print(pd.DataFrame({"n": g.size(), "exp_r": g.mean().round(4),
                    "win": g.apply(lambda s: (s > 0).mean()).round(3)}).to_string())
base.to_pickle("base_with_breadth.pkl")
