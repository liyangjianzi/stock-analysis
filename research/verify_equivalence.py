"""Prove the vectorized gate equals the shipped per-bar replay, bar for bar.

If this fails, every variant number produced by tune_harness.py is void.
"""
import numpy as np
import pandas as pd

from stockanalysis import cache
from stockanalysis.backtest import posture_timeline, entry_events
from stockanalysis.indicators import add_indicators
import tune_harness as th

TICKERS = ["AAPL", "JPM", "XOM", "NEE", "ABNB", "AMD", "PG", "T", "WMT", "CAT"]

conn = cache.connect()
prices = cache.load_universe(conn, TICKERS)
conn.close()

bad = 0
for tk, hist in prices.items():
    tl = posture_timeline(hist, mode="gate", fast=True)
    shipped = tl["gate"].to_numpy(bool)
    f = add_indicators(hist)
    mine = th.gate_mask(f)[th.MIN_BARS:]
    if len(shipped) != len(mine) or not np.array_equal(shipped, mine):
        bad += 1
        d = np.flatnonzero(shipped[:len(mine)] != mine[:len(shipped)])
        print(f"MISMATCH {tk}: {len(d)} bars differ, first at {d[:5]}")
        continue
    # entry dates too
    ship_dates = [pd.Timestamp(x) for x in entry_events(
        pd.DataFrame({"label": tl["gate"].map({True: "Entry", False: "Flat"})}),
        ("Entry",))]
    mine_dates = [f.index[i] for i in th.entry_positions(th.gate_mask(f))]
    if ship_dates != mine_dates:
        bad += 1
        print(f"ENTRY MISMATCH {tk}: shipped {len(ship_dates)} vs mine {len(mine_dates)}")
        continue
    print(f"OK {tk}: {len(shipped)} bars, {shipped.sum()} gate bars, "
          f"{len(mine_dates)} entries")

print("\nRESULT:", "ALL MATCH" if bad == 0 else f"{bad} TICKERS MISMATCH")
