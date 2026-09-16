"""Trade plan: turn a signal into an executable order — stop, target, size.

A signal that says "Buy AAPL" is not a trade; "buy at 168, stop 158, target 192,
2.4R, 140 shares" is. This module is the pure arithmetic that closes that gap. It
reads only what :func:`stockanalysis.indicators.add_indicators` already wrote
(``ATR14``) plus the on-demand ``find_support_resistance`` overlay, so it adds no
fetches to a run.

Placement rules (all configurable, defaults in :mod:`stockanalysis.config`):

* **Stop — structure first, ATR floor.** The nearest support *below* the entry,
  offset by a small ATR buffer so a routine wick through the level doesn't stop
  you out. Levels closer than ``min_stop_atr`` are skipped as noise. When nothing
  qualifies, fall back to ``entry - k*ATR14``.
* **Target — resistance first, 2R fallback.** The nearest resistance *above* the
  entry and at least ``min_target_atr`` away; if the chart offers none,
  ``entry + 2*risk``.
* **Size — fixed fractional, weight-capped.** Risk a fixed fraction of the
  account on the distance to the stop, then cap the notional so no single name
  exceeds ``max_weight`` of the account.

Entry is the **last close**. Fills are assumed at the next open, matching the
convention in :func:`stockanalysis.backtest.forward_returns`.

Following the package-wide rule, every failure path degrades to NaN rather than
raising: missing/zero ATR, too little history, or a degenerate frame all yield an
empty plan (see :func:`empty_plan`).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config
from .indicators import find_support_resistance, value_at

#: Plan key -> signal-matrix column name. Every key a plan carries appears here,
#: so :func:`empty_plan` and this map are the whole contract — there is no third
#: list to keep in sync. Risk-per-share is ``Entry - Stop`` and the thin-R:R flag
#: is re-derived by the renderer from its own ``min_rr``, so neither is a key.
MATRIX_COLUMNS = {
    "entry": "Entry",
    "stop": "Stop",
    "stop_basis": "Stop Basis",
    "target": "Target",
    "target_basis": "Target Basis",
    "rr": "R:R",
    "shares": "Shares",
    "risk_amount": "Risk $",
}

#: Widen the S/R search well past the default 6. ``find_support_resistance``
#: ranks by touch count and then truncates, so the *nearest* level — the one a
#: stop belongs under — can be dropped in favour of a stronger, farther one.
_SR_MAX_LEVELS = 20


def empty_plan() -> dict:
    """A plan with no levels: NaN prices and zero size.

    Returned whenever the inputs can't support a plan, so callers can merge the
    dict into a row unconditionally without branching.
    """
    return {
        "entry": np.nan, "stop": np.nan, "stop_basis": None,
        "target": np.nan, "target_basis": None, "rr": np.nan,
        "shares": 0, "risk_amount": 0.0,
    }


def build_trade_plan(df: pd.DataFrame, *,
                     account_size: float = config.DEFAULT_ACCOUNT_SIZE,
                     risk_pct: float = config.DEFAULT_RISK_PCT,
                     max_weight: float = config.DEFAULT_MAX_WEIGHT,
                     atr_stop_mult: float = config.ATR_STOP_MULT,
                     stop_buffer_atr: float = config.STOP_BUFFER_ATR,
                     min_stop_atr: float = config.MIN_STOP_ATR,
                     min_target_atr: float = config.MIN_TARGET_ATR) -> dict:
    """Build an executable plan from an indicator-enriched OHLCV frame.

    Parameters
    ----------
    df : frame carrying ``Close`` and ``ATR14`` (i.e. post-``add_indicators``).
    account_size : total account equity the sizing is measured against.
    risk_pct : fraction of the account risked on the stop distance (0.01 = 1%).
    max_weight : cap on one position's notional as a fraction of the account.
    atr_stop_mult : ATR multiple for the stop when no support sits below price.
    stop_buffer_atr : ATR multiple placed *below* a structural support level.
    min_stop_atr : how far below entry a support must sit to be used as a stop;
        nearer levels are skipped for the next one down.
    min_target_atr : how far above entry a resistance must sit to be used as a
        target; nearer levels are skipped for the next one up.

    Returns a dict keyed by :data:`MATRIX_COLUMNS`. Never raises — degenerate
    input yields :func:`empty_plan`.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return empty_plan()

    entry = value_at(df, "Close")
    atr = value_at(df, "ATR14")
    # ATR anchors both the fallback stop and the structural buffer, and it is the
    # denominator of the sizing, so a missing or zero ATR has no usable plan.
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(atr) or atr <= 0:
        return empty_plan()

    levels = find_support_resistance(df, max_levels=_SR_MAX_LEVELS)
    # Nearest-first, so the first level clearing the distance floor wins.
    supports = sorted((L["level"] for L in levels if L["level"] < entry), reverse=True)
    resistances = sorted(L["level"] for L in levels if L["level"] > entry)

    # --- Stop: nearest *usable* support below, buffered; else an ATR multiple. ---
    # A level closer than min_stop_atr is inside the noise and gets skipped for
    # the next one down: price frequently sits right on a level, and a stop a
    # fraction of a percent away is taken out by an ordinary day's range.
    support = next((L for L in supports if L <= entry - min_stop_atr * atr), None)
    stop, stop_basis = ((support - stop_buffer_atr * atr, "structure")
                        if support is not None
                        else (entry - atr_stop_mult * atr, "atr"))
    risk_per_share = entry - stop

    # --- Target: nearest *usable* resistance above; else a flat 2R. ---
    # Same floor in the other direction — overhead supply 0.2% away is not a
    # target, it's noise, and quoting it produces a meaningless R:R.
    resistance = next((L for L in resistances if L >= entry + min_target_atr * atr), None)
    target, target_basis = ((resistance, "structure") if resistance is not None
                            else (entry + 2.0 * risk_per_share, "2R"))

    rr = (target - entry) / risk_per_share

    # --- Size: risk budget, then the concentration cap. ---
    by_risk = math.floor(account_size * risk_pct / risk_per_share)
    by_weight = math.floor(account_size * max_weight / entry)
    shares = min(by_risk, by_weight)

    return {
        "entry": entry,
        "stop": stop,
        "stop_basis": stop_basis,
        "target": target,
        "target_basis": target_basis,
        "rr": rr,
        "shares": shares,
        "risk_amount": shares * risk_per_share,
    }
