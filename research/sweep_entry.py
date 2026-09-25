"""Plateau sweeps of the entry-gate thresholds, TRAIN period only (<=2021).

Per the tuning-signals skill: every axis is swept across >=5 neighbouring values
so a winner with losing neighbours is visible as a peak (noise) rather than
reported as an edge. The held-out period is not touched here.
"""
import pickle, time
import pandas as pd
import tune_harness as th
from memo import TradeMemo, entries_for, describe

with open("enriched.pkl", "rb") as fh:
    enriched = pickle.load(fh)
memo = TradeMemo(enriched)
TRAIN_END = 2021


def train_only(tr):
    if tr.empty:
        return tr
    return tr[pd.to_datetime(tr["entry_date"]).dt.year <= TRAIN_END]


def price(spec=None, params=None):
    return train_only(memo.trades(entries_for(enriched, spec, params)))


t0 = time.time()
base = price()
print("=== TRAIN BASELINE (2016-2021) ===")
print(describe(base, "baseline"))
print()

print("=== AXIS 1: trend_up EMA50 gain over 20 bars (base 2%) ===")
for g in [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.07]:
    tr = price(params={"trend_up": {"ema_gain": g}})
    print(describe(tr, f"  ema_gain={g:.2f}" + ("  <-base" if g == 0.02 else "")))
    memo.save()

print("\n=== AXIS 2: trend_up lookback bars (base 20) ===")
for b in [10, 15, 20, 30, 40, 60]:
    tr = price(params={"trend_up": {"gain_bars": b}})
    print(describe(tr, f"  gain_bars={b}" + ("  <-base" if b == 20 else "")))
    memo.save()

print("\n=== AXIS 3: pullback_zone max (Close-EMA50)/ATR (base 1.0) ===")
for a in [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]:
    tr = price(params={"pullback_zone": {"max_atr": a}})
    print(describe(tr, f"  max_atr={a:.2f}" + ("  <-base" if a == 1.0 else "")))
    memo.save()

print(f"\nelapsed {time.time()-t0:.0f}s, cache size {len(memo.cache)}")
memo.save()
