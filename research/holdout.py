"""The one held-out confirmation pass: TRAIN (<=2021) vs TEST (2022-2026).

Shortlist chosen from the train sweeps only. Reported with both the naive SE and
the monthly-cluster SE, because 7k trades are not 7k independent draws.
"""
import pickle
import numpy as np
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for
from tune_harness import (v_trend_up, v_dip_deep, v_pullback_zone,
                          v_turn_confirm, v_vol_pattern)

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)
breadth = pd.read_pickle("breadth.pkl")

ALL = {"trend_up": v_trend_up, "dip_deep": v_dip_deep,
       "pullback_zone": v_pullback_zone, "turn_confirm": v_turn_confirm,
       "vol_pattern": v_vol_pattern}
def spec(g):
    return {n: (fn, n in g) for n, fn in ALL.items()}

BASE = ("trend_up", "pullback_zone", "turn_confirm")

SHORTLIST = {
    "A baseline":                    dict(spec=spec(BASE)),
    "B baseline + breadth<0.72":     dict(spec=spec(BASE), breadth_max=0.72),
    "C ema_gain=0 (no trend gain)":  dict(spec=spec(BASE),
                                          params={"trend_up": {"ema_gain": 0.0}}),
    "D pullback max_atr=0.5":        dict(spec=spec(BASE),
                                          params={"pullback_zone": {"max_atr": 0.5}}),
    "E +dip_deep gating":            dict(spec=spec(BASE + ("dip_deep",))),
    "F -trend_up (pull+turn)":       dict(spec=spec(("pullback_zone", "turn_confirm"))),
    "H dip+turn only":               dict(spec=spec(("dip_deep", "turn_confirm"))),
    "G F + breadth<0.72":            dict(spec=spec(("pullback_zone", "turn_confirm")),
                                          breadth_max=0.72),
}


def ci(r):
    n = len(r)
    if n < 2:
        return dict(n=n, exp=np.nan, lo=np.nan, hi=np.nan, win=np.nan)
    se = r.std(ddof=1) / np.sqrt(n)
    return dict(n=n, exp=r.mean(), lo=r.mean()-1.96*se, hi=r.mean()+1.96*se,
                win=(r > 0).mean())


def clus(t):
    c = th.cluster_stats(t)
    return c["lo"], c["hi"], c["clusters"]


rows = []
for label, cfg in SHORTLIST.items():
    tr = memo.trades(entries_for(enriched, cfg["spec"], cfg.get("params")))
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    if "breadth_max" in cfg:
        b = pd.to_datetime(tr["entry_date"]).map(breadth)
        tr = tr[b < cfg["breadth_max"]]
    for split, sub in (("TRAIN 16-21", tr[tr["year"] <= 2021]),
                       ("TEST  22-26", tr[tr["year"] > 2021])):
        s = ci(sub["r"].to_numpy(float))
        lo, hi, k = clus(sub)
        rows.append({"variant": label, "split": split, **s,
                     "clo": lo, "chi": hi, "months": k})
    memo.save()

out = pd.DataFrame(rows)
out.to_pickle("holdout_results.pkl")
pd.set_option("display.width", 200)
print("=== HELD-OUT CONFIRMATION ===")
for label in SHORTLIST:
    d = out[out["variant"] == label]
    print(f"\n{label}")
    for _, r in d.iterrows():
        print(f"  {r['split']}  n={r['n']:>6}  win={r['win']:6.2%}  "
              f"exp={r['exp']:+.4f}R  CI=[{r['lo']:+.4f},{r['hi']:+.4f}]  "
              f"clustCI=[{r['clo']:+.4f},{r['chi']:+.4f}]")
