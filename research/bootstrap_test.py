"""Is the extension->expectancy relationship real out of sample?

Two tests, both resampling whole calendar months so correlated entries stay
together (the naive per-trade SE treats 7k clustered rows as 7k independent
draws, which is how a coin flip acquires a p-value):

 1. Spearman rank correlation between entry extension and realised R.
 2. Expectancy of the tight bucket (ext <= 0.25 ATR), block-bootstrapped.
"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(20260916)
tr = pd.read_pickle("extension_trades.pkl")
tr["ym"] = pd.to_datetime(tr["entry_date"]).dt.to_period("M")


def block_boot(df, stat, n=4000):
    months = df["ym"].unique()
    by = {m: g for m, g in df.groupby("ym")}
    out = np.empty(n)
    k = len(months)
    for i in range(n):
        pick = rng.choice(months, size=k, replace=True)
        s = pd.concat([by[m] for m in pick], ignore_index=True)
        out[i] = stat(s)
    return out


def spearman(s):
    """Rank correlation without scipy: Pearson on the ranks."""
    a = np.asarray(s["ext"].rank(), dtype=float).copy()
    b = np.asarray(s["r"].rank(), dtype=float).copy()
    a -= a.mean(); b -= b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else np.nan


def tight_exp(s):
    v = s.loc[s["ext"] <= 0.25, "r"]
    return v.mean() if len(v) else np.nan


for label, sub in (("TRAIN 2016-21", tr[tr["year"] <= 2021]),
                   ("TEST  2022-26", tr[tr["year"] > 2021]),
                   ("POOLED", tr)):
    rho = spearman(sub)
    b = block_boot(sub, spearman, 2000)
    p = 2 * min((b >= 0).mean(), (b <= 0).mean())
    lo, hi = np.percentile(b, [2.5, 97.5])
    print(f"{label}: Spearman(ext, R) = {rho:+.4f}  "
          f"block-boot 95% CI [{lo:+.4f}, {hi:+.4f}]  p={p:.3f}  n={len(sub)}")

print()
for label, sub in (("TRAIN 2016-21", tr[tr["year"] <= 2021]),
                   ("TEST  2022-26", tr[tr["year"] > 2021]),
                   ("POOLED", tr)):
    e = tight_exp(sub)
    b = block_boot(sub, tight_exp, 2000)
    b = b[np.isfinite(b)]
    lo, hi = np.percentile(b, [2.5, 97.5])
    p = 2 * min((b <= 0).mean(), (b >= 0).mean())
    n = int((sub["ext"] <= 0.25).sum())
    print(f"{label}: ext<=0.25 expectancy = {e:+.4f}R  "
          f"block-boot 95% CI [{lo:+.4f}, {hi:+.4f}]  p={p:.3f}  n={n}")
