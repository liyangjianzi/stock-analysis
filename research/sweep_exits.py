"""Exit-policy sweep on the fixed shipped entry gate. Train and test reported
side by side; nothing is concluded from the train column alone."""
import pickle, time
import numpy as np
import pandas as pd
import tune_harness as th
from memo import entries_for
from exit_lab import run

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
ent = entries_for(enriched)


def line(label, tr):
    tr = tr.copy()
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    parts = [f"{label:<26}"]
    for k, sub in (("TR", tr[tr["year"] <= 2021]), ("TE", tr[tr["year"] > 2021])):
        r = sub["r"].to_numpy(float)
        c = th.cluster_stats(sub)
        parts.append(f"{k} n={len(r):>5} exp={r.mean():+.4f} "
                     f"win={(r>0).mean():5.1%}")
        if k == "TE":
            parts.append(f"clustCI=[{c['lo']:+.4f},{c['hi']:+.4f}]")
    hold = tr["bars"].mean()
    parts.append(f"hold={hold:4.1f}b")
    return "  ".join(parts)


print("=== BASELINE ===", flush=True)
print(line("plan (resistance target)", run(enriched, ent)), flush=True)

print("\n=== fixed R-multiple target (plan stop kept) ===", flush=True)
for k in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]:
    print(line(f"target = {k}R", run(enriched, ent, policy="rmult", r_mult=k)), flush=True)

print("\n=== trailing ATR stop, no target ===", flush=True)
for m in [1.0, 1.5, 2.0, 2.5, 3.0]:
    print(line(f"trail {m} ATR", run(enriched, ent, policy="trail", trail_atr=m)), flush=True)

print("\n=== stop width: fallback ATR multiple ===", flush=True)
for k in [1.0, 1.5, 2.0, 2.5, 3.0]:
    print(line(f"atr_stop_mult={k}",
               run(enriched, ent, plan_kw={"atr_stop_mult": k})), flush=True)

print("\n=== stop width: buffer under structure ===", flush=True)
for k in [0.0, 0.25, 0.5, 0.75, 1.0]:
    print(line(f"stop_buffer_atr={k}",
               run(enriched, ent, plan_kw={"stop_buffer_atr": k})), flush=True)

print("\n=== minimum target distance ===", flush=True)
for k in [0.5, 1.0, 1.5, 2.0, 3.0]:
    print(line(f"min_target_atr={k}",
               run(enriched, ent, plan_kw={"min_target_atr": k})), flush=True)

print("\n=== max holding period ===", flush=True)
for b in [10, 21, 42, 63, 126]:
    print(line(f"max_hold={b} bars",
               run(enriched, ent, max_hold_bars=b)), flush=True)
