"""Dose-response: expectancy vs how extended price is above EMA50 at entry.

Uses one wide entry set (max_atr=2.0) and buckets it, rather than re-running the
gate per threshold -- so train and test see the same construction and the shape
is read off a single curve instead of six separate fits.
"""
import pickle
import numpy as np
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)

ent = entries_for(enriched, params={"pullback_zone": {"max_atr": 2.0}})
tr = memo.trades(ent)
memo.save()

# attach the entry-bar extension, (Close - EMA50) / ATR14
ext = []
for tk, pos in ent.items():
    f = enriched[tk]
    if not pos:
        continue
    e = ((f["Close"] - f["EMA50"]) / f["ATR14"]).to_numpy(float)
    for i in pos:
        ext.append({"ticker": tk, "entry_date": f.index[i], "ext": e[i]})
ext = pd.DataFrame(ext)
tr = tr.merge(ext, on=["ticker", "entry_date"], how="left")
tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
tr["period"] = np.where(tr["year"] <= 2021, "TRAIN", "TEST")

bins = [0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
tr["bucket"] = pd.cut(tr["ext"], bins)

print("=== expectancy by entry extension above EMA50 (in ATRs) ===")
print(f"{'bucket':>14} | {'TRAIN n':>8} {'exp':>8} {'win':>7} | "
      f"{'TEST n':>8} {'exp':>8} {'win':>7} | {'planned R:R':>11}")
for b in tr["bucket"].cat.categories:
    row = f"{str(b):>14} |"
    for p in ("TRAIN", "TEST"):
        s = tr[(tr["bucket"] == b) & (tr["period"] == p)]["r"]
        row += f" {len(s):>8} {s.mean():>+8.4f} {(s > 0).mean():>6.1%} |"
    rr = tr[tr["bucket"] == b]["rr_planned"].median()
    print(row + f" {rr:>11.2f}")

print("\n=== pooled (all years), with clustered CI ===")
for b in tr["bucket"].cat.categories:
    s = tr[tr["bucket"] == b]
    c = th.cluster_stats(s)
    r = s["r"].to_numpy(float)
    print(f"{str(b):>14}  n={len(s):>6}  exp={r.mean():+.4f}R  "
          f"clustCI=[{c['lo']:+.4f}, {c['hi']:+.4f}]")
tr.to_pickle("extension_trades.pkl")
