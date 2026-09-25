"""Cross-check the two findings that survive: the baseline's real error bar,
and whether ATR_STOP_MULT is reachable at all."""
import pickle
import numpy as np
import pandas as pd
import tune_harness as th
from memo import entries_for
from stockanalysis.tradeplan import build_trade_plan

rng = np.random.default_rng(7)
base = pd.read_pickle("baseline_trades.pkl")
base["ym"] = pd.to_datetime(base["entry_date"]).dt.to_period("M")

# --- 1. baseline expectancy: naive vs clustered vs month-block bootstrap ------
r = base["r"].to_numpy(float)
se_naive = r.std(ddof=1) / np.sqrt(len(r))
c = th.cluster_stats(base)
by = {m: g["r"].to_numpy(float) for m, g in base.groupby("ym")}
months = list(by)
boot = np.array([np.concatenate([by[m] for m in rng.choice(months, len(months), True)]).mean()
                 for _ in range(4000)])
lo, hi = np.percentile(boot, [2.5, 97.5])
p_boot = 2 * min((boot <= 0).mean(), (boot >= 0).mean())

print("=== baseline expectancy, three error bars ===")
print(f"  point estimate              {r.mean():+.4f}R   (n={len(r)}, {len(months)} months)")
print(f"  naive per-trade SE          {se_naive:.4f}  95% CI "
      f"[{r.mean()-1.96*se_naive:+.4f}, {r.mean()+1.96*se_naive:+.4f}]  "
      f"t={r.mean()/se_naive:.2f}")
print(f"  cluster-robust (monthly)    {c['se_cluster']:.4f}  95% CI "
      f"[{c['lo']:+.4f}, {c['hi']:+.4f}]  t={r.mean()/c['se_cluster']:.2f}")
print(f"  month-block bootstrap             95% CI [{lo:+.4f}, {hi:+.4f}]  p={p_boot:.3f}")

# --- 2. is the ATR fallback stop ever used? ----------------------------------
with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
ent = entries_for(enriched)
basis = {}
rr = []
for tk, pos in ent.items():
    f = enriched[tk]
    for i in pos:
        p = build_trade_plan(f.iloc[: i + 1])
        basis[p["stop_basis"]] = basis.get(p["stop_basis"], 0) + 1
        if np.isfinite(p["rr"]):
            rr.append((p["rr"], p["target_basis"]))
print("\n=== stop basis over all 7,253 baseline entries ===")
for k, v in sorted(basis.items(), key=lambda kv: -kv[1]):
    print(f"  {str(k):<12} {v:>6}  ({v/sum(basis.values()):.2%})")
tb = {}
for _, b in rr:
    tb[b] = tb.get(b, 0) + 1
print("\n=== target basis ===")
for k, v in sorted(tb.items(), key=lambda kv: -kv[1]):
    print(f"  {str(k):<12} {v:>6}  ({v/sum(tb.values()):.2%})")
print(f"\nplanned R:R (from the plan's own entry): median "
      f"{np.median([x for x, _ in rr]):.2f}, "
      f"pct below 1.0 = {np.mean([x < 1.0 for x, _ in rr]):.1%}")
