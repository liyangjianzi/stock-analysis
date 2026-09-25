"""Which components belong in the gate? TRAIN period only (<=2021)."""
import pickle, time
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for, describe
from tune_harness import (v_trend_up, v_dip_deep, v_pullback_zone,
                          v_turn_confirm, v_vol_pattern)

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)
TRAIN_END = 2021

ALL = {"trend_up": v_trend_up, "dip_deep": v_dip_deep,
       "pullback_zone": v_pullback_zone, "turn_confirm": v_turn_confirm,
       "vol_pattern": v_vol_pattern}


def spec(gating_names):
    return {n: (fn, n in gating_names) for n, fn in ALL.items()}


BASE = ("trend_up", "pullback_zone", "turn_confirm")
VARIANTS = {
    "base (trend+pull+turn)": BASE,
    "+dip_deep": BASE + ("dip_deep",),
    "+vol_pattern": BASE + ("vol_pattern",),
    "+dip+vol (all five)": tuple(ALL),
    "-trend_up (pull+turn)": ("pullback_zone", "turn_confirm"),
    "-pullback_zone (trend+turn)": ("trend_up", "turn_confirm"),
    "-turn_confirm (trend+pull)": ("trend_up", "pullback_zone"),
    "dip+turn only": ("dip_deep", "turn_confirm"),
    "trend+dip+turn": ("trend_up", "dip_deep", "turn_confirm"),
}

print("=== TRAIN (2016-2021): gate composition ===", flush=True)
for label, names in VARIANTS.items():
    t0 = time.time()
    tr = memo.trades(entries_for(enriched, spec(names)))
    if not tr.empty:
        tr = tr[pd.to_datetime(tr["entry_date"]).dt.year <= TRAIN_END]
    print(describe(tr, "  " + label) + f"   [{time.time()-t0:.0f}s]", flush=True)
    memo.save()
print(f"cache size {len(memo.cache)}")
