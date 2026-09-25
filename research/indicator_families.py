"""Do *different kinds* of indicator help, as opposed to different thresholds?

Each gate below is a standalone entry rule from a different family -- breakout,
momentum, oscillator cross, band mean-reversion, cross-sectional relative
strength -- run through the identical trade plan and exit walk. Train 2016-21,
confirm 2022-26. The question is not which one wins on train; it is whether ANY
family produces a different out-of-sample answer than the shipped gate.
"""
import pickle
import numpy as np
import pandas as pd

import tune_harness as th
from memo import entries_for
from exit_lab import run as walk_all

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
for tk, f in enriched.items():
    f.attrs["tk"] = tk

# --- cross-sectional 12-1 momentum rank, computed causally per date ----------
mom = {}
for tk, f in enriched.items():
    c = f["Close"]
    mom[tk] = (c.shift(21) / c.shift(252) - 1)
mom = pd.DataFrame(mom)
mom_rank = mom.rank(axis=1, pct=True)      # 1.0 = strongest in the universe


def m_donchian(f, tk):
    """Breakout: close above the prior 20-bar high."""
    return f["Close"].to_numpy(float) > f["High"].shift(1).rolling(20).max().to_numpy(float)


def m_52w(f, tk):
    """Momentum: within 2% of the 252-bar closing high, and up today."""
    hi = f["Close"].rolling(252).max().to_numpy(float)
    c = f["Close"].to_numpy(float)
    return (c >= 0.98 * hi) & (c > f["Open"].to_numpy(float))


def m_macd(f, tk):
    """Oscillator cross: MACD crosses up through its signal line."""
    h = f["MACD_HIST"].to_numpy(float)
    hp = f["MACD_HIST"].shift(1).to_numpy(float)
    return (hp < 0) & (h > 0)


def m_band(f, tk):
    """Band mean-reversion: close 2 causal sigma below EMA20, then an up bar."""
    c = f["Close"]
    z = (c - f["EMA20"]) / c.rolling(20).std()
    zp = z.shift(1).to_numpy(float)
    return (zp < -2.0) & (c.to_numpy(float) > f["Open"].to_numpy(float))


def m_rsi_cross(f, tk):
    """Classic RSI(14) recovery: crosses up through 30."""
    r = f["RSI"].to_numpy(float)
    rp = f["RSI"].shift(1).to_numpy(float)
    return (rp < 30) & (r >= 30)


def m_xsec(f, tk):
    """Cross-sectional relative strength: top-decile 12-1 momentum, up bar."""
    rk = mom_rank[tk].reindex(f.index).to_numpy(float)
    return (rk >= 0.90) & (f["Close"].to_numpy(float) > f["Open"].to_numpy(float))


def m_trend_pullback(f, tk):
    """Long-term trend + oscillator pullback (a different pairing, same family)."""
    c = f["Close"].to_numpy(float)
    return (c > f["EMA200"].to_numpy(float)) & (f["RSI"].to_numpy(float) < 40)


FAMILIES = {
    "shipped gate": None,
    "Donchian-20 breakout": m_donchian,
    "52w-high momentum": m_52w,
    "MACD cross up": m_macd,
    "2-sigma band reversion": m_band,
    "RSI(14) cross up 30": m_rsi_cross,
    "x-sec 12-1 top decile": m_xsec,
    "EMA200 + RSI<40": m_trend_pullback,
}


def entries_from(mask_fn):
    out = {}
    for tk, f in enriched.items():
        m = np.nan_to_num(mask_fn(f, tk), nan=0).astype(bool)
        out[tk] = th.entry_positions(m)
    return out


def report(name, tr):
    tr = tr.copy()
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    a, b = tr[tr["year"] <= 2021], tr[tr["year"] > 2021]
    c = th.cluster_stats(b)
    ci = "[{:+.4f}, {:+.4f}]".format(c["lo"], c["hi"])
    print(f"{name:<24} {len(a):>8} {a['r'].mean():>+8.4f} | "
          f"{len(b):>8} {b['r'].mean():>+8.4f} {ci:>24}", flush=True)


print(f"{'family':<24} {'TRAIN n':>8} {'exp':>8} | {'TEST n':>8} {'exp':>8} "
      f"{'clustered 95% CI':>24}", flush=True)

# period-split random null: the bar every family must actually clear
rng = np.random.default_rng(3)
elig = {tk: (th.MIN_BARS, len(f) - 2) for tk, f in enriched.items()}
tks = list(enriched)
for rep in range(3):
    pairs = [(tk, int(rng.integers(*elig[tk])))
             for tk in rng.choice(tks, 20000)]
    report(f"** RANDOM null #{rep+1}", walk_all(enriched, {tk: [] for tk in tks} |
           {tk: [i for t, i in pairs if t == tk] for tk in set(t for t, _ in pairs)}))
print("-" * 80, flush=True)
for name, fn in FAMILIES.items():
    ent = entries_for(enriched) if fn is None else entries_from(fn)
    tr = walk_all(enriched, ent)
    if tr.empty:
        print(f"{name:<24} no entries", flush=True)
        continue
    report(name, tr)
