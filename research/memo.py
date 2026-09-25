"""Memoized trade evaluation.

The exit walk depends only on the *entry bar*, never on which gate rule selected
it, so a trade outcome computed once can be reused by every variant that picks
the same bar. That turns a 66s full-universe variant into a few seconds once the
cache is warm, which is what makes plateau sweeps affordable.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from stockanalysis.backtest import simulate_planned_trades
import tune_harness as th

CACHE_PATH = Path(__file__).resolve().parent / "trade_cache.pkl"


class TradeMemo:
    def __init__(self, enriched, max_hold_bars=63, cost_bps=10.0):
        self.enriched = enriched
        self.max_hold_bars = max_hold_bars
        self.cost_bps = cost_bps
        self.cache: dict = {}
        if CACHE_PATH.exists():
            with open(CACHE_PATH, "rb") as fh:
                self.cache = pickle.load(fh)

    def save(self):
        with open(CACHE_PATH, "wb") as fh:
            pickle.dump(self.cache, fh)

    def _fill(self, tk, positions):
        """Simulate any entry bars for ``tk`` not already cached."""
        f = self.enriched[tk]
        missing = [i for i in positions if (tk, i) not in self.cache]
        if not missing:
            return
        dates = [f.index[i] for i in missing]
        got = {}
        for t in simulate_planned_trades(f, dates, ticker=tk,
                                         max_hold_bars=self.max_hold_bars,
                                         cost_bps=self.cost_bps):
            got[t.entry_date] = t
        for i, d in zip(missing, dates):
            t = got.get(d)
            self.cache[(tk, i)] = None if t is None else {
                "ticker": tk, "entry_date": d, "r": t.r_multiple,
                "reason": t.exit_reason, "bars": t.bars_held,
                "rr_planned": (t.target - t.entry) / (t.entry - t.stop),
            }

    def trades(self, entries: dict) -> pd.DataFrame:
        """``{ticker: [entry bar positions]}`` -> tidy trade frame."""
        rows = []
        for tk, pos in entries.items():
            if not pos:
                continue
            self._fill(tk, pos)
            rows += [self.cache[(tk, i)] for i in pos
                     if self.cache.get((tk, i)) is not None]
        return pd.DataFrame(rows)


def entries_for(enriched, spec=None, params=None) -> dict:
    """``{ticker: [entry bar positions]}`` for one gate specification."""
    return {tk: th.entry_positions(th.gate_mask(f, spec, params))
            for tk, f in enriched.items()}


def describe(trades, label=""):
    s, c = th.stats(trades), th.cluster_stats(trades)
    if s["n"] == 0:
        return f"{label:<34} n=0"
    return (f"{label:<34} n={s['n']:>5}  win={s['win']:6.2%}  "
            f"exp={s['exp_r']:+.4f}R  CI=[{s['lo']:+.4f},{s['hi']:+.4f}]  "
            f"clustCI=[{c['lo']:+.4f},{c['hi']:+.4f}]")
