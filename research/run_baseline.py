import pickle, time, sys
import pandas as pd
import tune_harness as th

t0 = time.time()
enriched = th.load_enriched()
print(f"loaded {len(enriched)} tickers in {time.time()-t0:.0f}s", flush=True)
with open("enriched.pkl", "wb") as fh:
    pickle.dump(enriched, fh)

t0 = time.time()
tr = th.run_variant(enriched)
print(f"simulated {len(tr)} trades in {time.time()-t0:.0f}s", flush=True)
tr.to_pickle("baseline_trades.pkl")

s, c = th.stats(tr), th.cluster_stats(tr)
print("\n=== BASELINE (my harness) ===")
print(f"n={s['n']}  win={s['win']:.3%}  exp={s['exp_r']:+.4f}R  "
      f"SE={s['se']:.4f}  95%CI=[{s['lo']:+.4f}, {s['hi']:+.4f}]")
print(f"monthly clusters={c['clusters']}  cluster SE={c['se_cluster']:.4f}  "
      f"CI=[{c['lo']:+.4f}, {c['hi']:+.4f}]")
print("\nexit mix:")
print(tr["reason"].value_counts())
print("\nby year:")
print(th.by_year(tr).to_string())
