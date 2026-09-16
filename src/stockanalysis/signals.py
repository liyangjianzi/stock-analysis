"""Signal engine: technical posture + the fused Buy/Hold/Watch matrix.

The technical score is **registry-driven**: each component in
:data:`TECHNICAL_COMPONENTS` is a pure predicate ``(df) -> bool`` worth +1. The
max score, composite divisor, posture cutoffs and ``detail`` keys all derive from
the registry, so adding/removing a component is a one-line edit there.

The default registry is a pullback/reversal pattern (uptrend, deep oversold dip,
shallow pullback, a confirming green bar, and a volume pickup) rather than a set
of independent bullish confirmations.

Score contract:
  - fundamental score: 0-6 (from :mod:`stockanalysis.screener`)
  - technical score:   0-len(TECHNICAL_COMPONENTS) (default 5)
  - composite = 0.70*(fund/6) + 0.30*(tech/len) -- a **ranking** key only

Action contract (see :func:`decide_action`): fundamentals and technicals answer
two different questions and are tested separately, not averaged. Fundamentals say
*what is worth owning*; the technical gate says *whether today is the day*::

    Buy   = fund_score >= fund_min AND every *gating* component fires
    Hold  = fund_score >= fund_min but the gate does not
    Watch = fund_score <  fund_min

The composite deliberately does **not** decide the action. Averaging a
conjunctive pattern into a weighted score hands out partial credit for half a
setup, which let a name with perfect fundamentals and no technical confirmation
at all (0.70*(6/6) = 0.70) clear a 0.60 "Buy" bar.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, NamedTuple

import numpy as np
import pandas as pd

from . import config
from .indicators import find_support_resistance, value_at as _at
from .tradeplan import MATRIX_COLUMNS, build_trade_plan, empty_plan

log = logging.getLogger(__name__)


# --- Individual scoring components --------------------------------------------
# Each predicate is pure and NaN/short-data robust. They assume a non-empty df
# (compute_technical_posture guards emptiness before calling them).

def _trend_up(df: pd.DataFrame) -> bool:
    """Uptrend intact and accelerating: Close > EMA50, and EMA50 has risen at
    least 2% over the last 20 bars: (EMA50 - EMA50[20]) / EMA50[20] >= 2%."""
    close, ema50 = _at(df, "Close"), _at(df, "EMA50")
    ema50_20ago = _at(df, "EMA50", 20)
    if not all(np.isfinite(v) for v in (close, ema50, ema50_20ago)) or ema50_20ago == 0:
        return False
    return bool(close > ema50 and (ema50 - ema50_20ago) / ema50_20ago >= 0.02)


def _dip_deep(df: pd.DataFrame) -> bool:
    """Yesterday's 3-day RSI showed a deep oversold dip: RSI(3)[1] < 25."""
    rsi3_prev = _at(df, "RSI3", 1)
    return bool(np.isfinite(rsi3_prev) and rsi3_prev < 25)


def _pullback_zone(df: pd.DataFrame) -> bool:
    """Price sits within one ATR of EMA50: (Close - EMA50) / ATR14 <= 1.0."""
    close, ema50, atr14 = _at(df, "Close"), _at(df, "EMA50"), _at(df, "ATR14")
    if not all(np.isfinite(v) for v in (close, ema50, atr14)) or atr14 == 0:
        return False
    return bool((close - ema50) / atr14 <= 1.0)


def _turn_confirm(df: pd.DataFrame) -> bool:
    """Today confirms a turn: Close > High[1] AND Close > Open."""
    close, high_prev, open_ = _at(df, "Close"), _at(df, "High", 1), _at(df, "Open")
    if not all(np.isfinite(v) for v in (close, high_prev, open_)):
        return False
    return bool(close > high_prev and close > open_)


def _vol_pattern(df: pd.DataFrame) -> bool:
    """A quiet spell followed by a pickup: SMA(Volume,5)[1] < VOL_SMA20 AND
    Volume >= 1.2 * VOL_SMA20."""
    vol_sma5_prev = _at(df, "VOL_SMA5", 1)
    vol_sma20, volume = _at(df, "VOL_SMA20"), _at(df, "Volume")
    if not all(np.isfinite(v) for v in (vol_sma5_prev, vol_sma20, volume)):
        return False
    return bool(vol_sma5_prev < vol_sma20 and volume >= 1.2 * vol_sma20)


class TechnicalComponent(NamedTuple):
    """One scored predicate in the registry.

    ``gating`` is what makes a component *required* for a Buy rather than merely
    scored. Keeping it here — instead of in a parallel tuple of names — means a
    custom registry passed as ``components=`` gates on its own entries, so the
    gate and the registry cannot silently disagree.
    """
    name: str
    predicate: Callable[[pd.DataFrame], bool]
    gating: bool = False


def _components(components=None) -> list[TechnicalComponent]:
    """Normalize a registry, accepting plain ``(name, predicate)`` tuples so an
    ad-hoc caller needn't import :class:`TechnicalComponent` (such entries are
    non-gating). ``None`` means the default registry."""
    if components is None:
        return TECHNICAL_COMPONENTS
    return [c if isinstance(c, TechnicalComponent) else TechnicalComponent(*c)
            for c in components]

#: Single source of truth for the technical score. Add/remove a (name, predicate)
#: tuple and the max score, composite divisor, posture cutoffs and detail keys all
#: follow automatically.
#: Single source of truth for the technical score AND the entry gate. Three
#: components are ``gating`` — trend (context), pullback zone (location) and the
#: confirming bar (trigger); ``dip_deep`` and ``vol_pattern`` stay scored but
#: non-blocking, since requiring them on top of the other three makes a Buy
#: near-unreachable.
TECHNICAL_COMPONENTS: list[TechnicalComponent] = [
    TechnicalComponent("trend_up", _trend_up, gating=True),
    TechnicalComponent("dip_deep", _dip_deep),
    TechnicalComponent("pullback_zone", _pullback_zone, gating=True),
    TechnicalComponent("turn_confirm", _turn_confirm, gating=True),
    TechnicalComponent("vol_pattern", _vol_pattern),
]

#: The gating component names, *derived* from the registry above — a read-only
#: convenience for callers and docs, never a second place to edit.
GATE_COMPONENTS: tuple[str, ...] = tuple(
    c.name for c in TECHNICAL_COMPONENTS if c.gating)

#: Minimum fundamental score (of 6) for a name to be considered ownable at all.
DEFAULT_FUND_MIN = 4

# Posture cutoffs, calibrated against the score's *measured* distribution rather
# than assumed to be symmetric. Point-in-time replay over 10,523 ticker-bars
# (the 21-name watchlist, 3y) gives:
#
#     0/5  4.9% | 1/5 50.6% | 2/5 35.1% | 3/5  8.5% | 4/5 0.8% | 5/5 0.1%
#
# The registry is a *conjunctive* pullback pattern, so the score piles up at 1-2
# (85.7% of bars) and the top of the range is nearly unreachable. The original
# mirrored 1/3 and 2/3 fractions assumed the roughly symmetric spread you get
# from independent confirmations; applied to this distribution they labelled
# 55.5% of all bars "Bearish" and 0.9% "Bullish" — a label that fires on the
# majority of days carries no information, and one that fires twice a year per
# name isn't a posture, it's an event.
#
# Recalibrated so both tail labels mean something: Bearish only when *nothing*
# fires (4.9%), Bullish once the setup is materially assembled (9.4%). Neutral
# is deliberately broad (85.8%) because that is the honest answer on most days —
# the label cannot carry more information than the underlying score does.
#
# Both still derive from len(components), so a resized registry rescales them.

#: Bullish at ``score >= ceil(bull_frac * N)``; 0.55 -> ceil(2.75)=3 for N=5.
DEFAULT_BULL_FRAC = 0.55

#: Bearish at ``score <= floor(bear_frac * N)``; 0.0 -> 0, i.e. no component fired.
DEFAULT_BEAR_FRAC = 0.0


def _posture(score: int, max_score: int, bull_frac: float = DEFAULT_BULL_FRAC,
             bear_frac: float = DEFAULT_BEAR_FRAC) -> str:
    """Map a score to Bullish/Neutral/Bearish, scaling with the component count:
    Bearish at ``score <= floor(bear_frac * max_score)``, Bullish at
    ``score >= ceil(bull_frac * max_score)``, else Neutral.

    Both cutoffs derive from the registry size, so adding or removing a component
    rescales them together. Bearish is checked first, so on a degenerate registry
    where the bands would overlap the weaker label wins.
    """
    if max_score <= 0:
        return "Bearish"
    if score <= math.floor(bear_frac * max_score):
        return "Bearish"
    if score >= math.ceil(bull_frac * max_score):
        return "Bullish"
    return "Neutral"


def compute_technical_posture(df: pd.DataFrame,
                              components: list[TechnicalComponent] | None = None,
                              bull_frac: float = DEFAULT_BULL_FRAC,
                              bear_frac: float = DEFAULT_BEAR_FRAC):
    """Assess technical posture from an indicator-enriched df.

    Runs each predicate in ``components`` (default :data:`TECHNICAL_COMPONENTS`),
    awarding +1 per truthy result. Returns ``(posture_label, tech_score, detail)``
    where ``tech_score`` is 0-len(components) and ``detail`` maps each component
    name to its bool plus an unscored ``nearest_level`` support/resistance context.
    Posture scales with the component count (see :func:`_posture`). Robust to
    NaN/short data; a predicate that raises is treated as False.
    """
    components = _components(components)
    detail = {c.name: False for c in components}
    detail["nearest_level"] = None
    if df is None or df.empty:
        return "Bearish", 0, detail

    for c in components:
        try:
            detail[c.name] = bool(c.predicate(df))
        except Exception:
            detail[c.name] = False

    # Context (not scored): nearest support/resistance level to the last close.
    close = df.iloc[-1].get("Close", np.nan)
    sr = find_support_resistance(df)
    if sr and np.isfinite(close):
        detail["nearest_level"] = min(sr, key=lambda L: abs(L["level"] - close))

    score = sum(detail[c.name] for c in components)
    posture = _posture(score, len(components), bull_frac, bear_frac)
    return posture, score, detail


#: Shared bare-hex palette (no leading ``#``) for the ``Final Action Signal``/
#: ``Technical Posture`` labels this module produces. Single source of truth
#: for every renderer of those labels: ``outputs/excel.py`` wraps these in
#: ``openpyxl.PatternFill``, ``report.py`` prefixes ``#`` for inline CSS.
ACTION_COLORS = {"Buy": "B7E1CD", "Hold": "FCE8B2", "Watch": "D9D9D9"}
POSTURE_COLORS = {"Bullish": "B7E1CD", "Neutral": "FCE8B2", "Bearish": "F4C7C3"}


def decide_action(fund_score: int, detail: dict, *,
                  fund_min: int = DEFAULT_FUND_MIN, components=None) -> str:
    """Decide Buy / Hold / Watch from a fundamental score and a posture ``detail``.

    Two independent tests, never averaged (see the module docstring): quality
    (``fund_score >= fund_min``) gets a name onto the list, and the technical
    gate — every ``gating`` component in ``components`` firing — decides whether
    today is the entry. Quality without the gate is **Hold** (own-worthy, waiting
    for the setup); failing quality is **Watch** however good the chart looks.

    ``components`` must be the same registry the ``detail`` was computed with, so
    a custom registry gates on its own entries.
    """
    if fund_score < fund_min:
        return "Watch"
    gating = (c.name for c in _components(components) if c.gating)
    return "Buy" if all(detail.get(name) for name in gating) else "Hold"


#: Weights for the *ranking* key only — never an action threshold. See
#: :func:`decide_action` for what actually decides Buy/Hold/Watch.
RANK_WEIGHTS = (0.70, 0.30)   # fundamental, technical


def rank_score(fund_score: int, tech_score: int, *, components=None,
               weights: tuple[float, float] = RANK_WEIGHTS) -> float:
    """The matrix's sort key: how *interesting* a name is, not whether to buy it.

    Named so the ``Composite`` column's role is explicit in code and not only in
    the docs — it orders the table, drives the heatmaps and picks ``top_tickers``,
    and it is deliberately not compared against any action threshold.
    """
    w_fund, w_tech = weights
    return w_fund * (fund_score / 6.0) + w_tech * (tech_score / len(_components(components)))


def generate_signals(screened: pd.DataFrame, tech_data: dict, *,
                     fund_min: int = DEFAULT_FUND_MIN,
                     components=None,
                     account_size: float = config.DEFAULT_ACCOUNT_SIZE,
                     risk_pct: float = config.DEFAULT_RISK_PCT,
                     max_weight: float = config.DEFAULT_MAX_WEIGHT) -> pd.DataFrame:
    """Score every screened stock and attach an executable trade plan.

    The action comes from :func:`decide_action` (quality test + technical gate);
    ``Composite`` is carried as the ranking key only. Each row also gets the
    :mod:`stockanalysis.tradeplan` columns — Entry / Stop / Target / R:R /
    Shares / Risk $ — sized against ``account_size`` and ``risk_pct``.

    Returns a tidy DataFrame ranked Buy > Hold > Watch, then by Composite.
    """
    if screened is None or screened.empty:
        return pd.DataFrame()

    rows = []
    for ticker, row in screened.iterrows():
        f_score = int(row.get("Fundamental_Score", 0))           # 0–6
        df_t = tech_data.get(ticker)
        posture, t_score, detail = compute_technical_posture(df_t, components)

        composite = rank_score(f_score, t_score, components=components)
        action = decide_action(f_score, detail, fund_min=fund_min,
                               components=components)

        # Watch names are not candidates for entry, so they carry no plan; this
        # also skips the S/R fit for every name that failed the quality test.
        plan = (build_trade_plan(df_t, account_size=account_size, risk_pct=risk_pct,
                                 max_weight=max_weight)
                if action != "Watch" else empty_plan())

        rows.append({
            "Ticker": ticker,
            "Sector": row.get("Sector", "Unknown"),
            "Fundamental Score": f_score,
            "Technical Posture": posture,
            "Tech Score": t_score,
            "Composite": round(composite, 3),
            "Final Action Signal": action,
            **{col: plan[key] for key, col in MATRIX_COLUMNS.items()},
        })

    result = pd.DataFrame(rows)
    # Order: actionable first (Buy>Hold>Watch), then by composite strength.
    action_rank = {"Buy": 0, "Hold": 1, "Watch": 2}
    result["_rank"] = result["Final Action Signal"].map(action_rank)
    result = (result.sort_values(["_rank", "Composite"], ascending=[True, False])
                    .drop(columns="_rank").reset_index(drop=True))
    return result


def top_tickers(signal_matrix: pd.DataFrame, n: int | None = None) -> list[str]:
    """The strongest ``n`` tickers of a ranked signal matrix (all when ``n`` is None).

    :func:`generate_signals` returns its rows pre-ranked (Buy > Hold > Watch,
    then Composite descending), so the head of that frame *is* the top-pick
    list — this helper makes that contract explicit for callers who only want a
    subset (charts, profiles, a dashboard's "top 5"). Returns ``[]`` for an
    empty matrix, which carries no columns to index.
    """
    if signal_matrix is None or signal_matrix.empty:
        return []
    rows = signal_matrix if n is None else signal_matrix.head(n)
    return rows["Ticker"].tolist()
