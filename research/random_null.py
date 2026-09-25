"""What does the entry signal actually contribute?

Compares the shipped gate against random entries run through the *same* trade
plan and exit walk. Two nulls, because they answer different questions:

  A. date-matched  -- keep each real entry's date, pick a random ticker.
     Regime mix is held exactly constant, so this isolates *stock selection*.
  B. ticker-matched -- keep the ticker, pick a random bar from its history.
     Isolates *timing* (it does not control for regime).
  C. unconditional -- random ticker and random bar.

If the gate's expectancy sits inside the null distribution, the entry rule is
not the binding constraint and swapping indicators cannot fix it.
"""
import pickle
import numpy as np
import pandas as pd

import tune_harness as th
from memo import entries_for
from exit_lab import walk

rng = np.random.default_rng(11)
REPS = 12

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
for tk, f in enriched.items():
    f.attrs["tk"] = tk

ent = entries_for(enriched)
real = [(tk, i) for tk, pos in ent.items() for i in pos]
print(f"real entries: {len(real)}", flush=True)

# pools: for each date, which (ticker, bar) are eligible (past warmup, has a next bar)
tickers = list(enriched)
pos_of = {tk: {ts: i for i, ts in enumerate(f.index)} for tk, f in enriched.items()}
eligible = {tk: (th.MIN_BARS, len(f) - 2) for tk, f in enriched.items()}
by_date = {}
for tk, f in enriched.items():
    lof, hif = eligible[tk]
    for i in range(lof, hif + 1):
        by_date.setdefault(f.index[i], []).append((tk, i))
dates = {tk: f.index for tk, f in enriched.items()}


def run(pairs):
    out = []
    for tk, i in pairs:
        t = walk(enriched[tk], i)
        if t:
            out.append(t)
    return pd.DataFrame(out)


def summarize(df):
    r = df["r"].to_numpy(float)
    return r.mean(), (r > 0).mean(), len(r)


gate = run(real)
g_exp, g_win, g_n = summarize(gate)
c = th.cluster_stats(gate)
print(f"\nGATE           n={g_n:>5}  exp={g_exp:+.4f}R  win={g_win:6.2%}  "
      f"clustCI=[{c['lo']:+.4f}, {c['hi']:+.4f}]", flush=True)

real_dates = [enriched[tk].index[i] for tk, i in real]

for name, sampler in [
    ("A date-matched", lambda: [by_date[d][rng.integers(len(by_date[d]))]
                                for d in real_dates if d in by_date]),
    ("B ticker-matched", lambda: [(tk, int(rng.integers(*eligible[tk])))
                                  for tk, _ in real]),
    ("C unconditional", lambda: [(tk, int(rng.integers(*eligible[tk])))
                                 for tk in rng.choice(tickers, len(real))]),
]:
    exps, wins = [], []
    for _ in range(REPS):
        d = run(sampler())
        e, w, _ = summarize(d)
        exps.append(e); wins.append(w)
    exps = np.array(exps)
    lo, hi = exps.min(), exps.max()
    z = (g_exp - exps.mean()) / exps.std(ddof=1)
    print(f"{name:<16} null exp={exps.mean():+.4f}R "
          f"(sd {exps.std(ddof=1):.4f}, range [{lo:+.4f},{hi:+.4f}], {REPS} reps)  "
          f"win={np.mean(wins):6.2%}   gate is z={z:+.2f} vs this null", flush=True)
