"""Signal engine: technical posture + the fused Buy/Hold/Watch matrix.

The technical score is **registry-driven**: each component in
:data:`TECHNICAL_COMPONENTS` is a pure predicate ``(df) -> bool`` worth +1. The
max score, composite divisor, posture cutoffs and ``detail`` keys all derive from
the registry, so adding/removing a component is a one-line edit there.

Score contract:
  - fundamental score: 0-6 (from :mod:`stockanalysis.screener`)
  - technical score:   0-len(TECHNICAL_COMPONENTS) (default 7)
  - composite = 0.70*(fund/6) + 0.30*(tech/len) -> Buy >=0.60, Hold >=0.40, else Watch
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from .indicators import fit_regression_channel, find_support_resistance


# --- Individual scoring components --------------------------------------------
# Each predicate is pure and NaN/short-data robust. They assume a non-empty df
# (compute_technical_posture guards emptiness before calling them).

def _above_ema50(df: pd.DataFrame) -> bool:
    """Price above the 50-day EMA (trend)."""
    last = df.iloc[-1]
    close, ema50 = last.get("Close", np.nan), last.get("EMA50", np.nan)
    return bool(np.isfinite(close) and np.isfinite(ema50) and close > ema50)


def _rsi_ok(df: pd.DataFrame) -> bool:
    """RSI healthy: not overbought (<70), not deeply oversold (>35)."""
    rsi = df.iloc[-1].get("RSI", np.nan)
    return bool(np.isfinite(rsi) and 35 < rsi < 70)


def _macd_cross_up(df: pd.DataFrame, lookback: int = 5) -> bool:
    """Bullish MACD crossover (line crossed above signal) within ``lookback`` bars."""
    macd, sig = df.get("MACD"), df.get("MACD_SIG")
    if macd is None or sig is None or len(df) <= lookback:
        return False
    diff = (macd - sig).dropna()
    if len(diff) <= lookback:
        return False
    recent = diff.iloc[-lookback:]
    prev = diff.iloc[-lookback - 1:-1]
    return bool(((prev.values <= 0) & (recent.values > 0)).any())


def _trend_up(df: pd.DataFrame) -> bool:
    """Prevailing up-trend: positive close regression-channel slope."""
    channel = fit_regression_channel(df.get("Close"))
    return bool(channel is not None and channel["slope"] > 0)


def _ema50_up(df: pd.DataFrame) -> bool:
    """Rising 50-day EMA: positive regression slope on the EMA50 series."""
    channel = fit_regression_channel(df.get("EMA50"))
    return bool(channel is not None and channel["slope"] > 0)


def _vol_confirm(df: pd.DataFrame, obv_lookback: int = 20) -> bool:
    """Volume confirmation: OBV rising over the window, OR latest volume above its
    20-day average on an up day (close >= open)."""
    obv = df.get("OBV")
    if obv is not None:
        obv_s = obv.dropna()
        if len(obv_s) > obv_lookback and obv_s.iloc[-1] > obv_s.iloc[-obv_lookback]:
            return True
    last = df.iloc[-1]
    vol, vol_sma = last.get("Volume", np.nan), last.get("VOL_SMA20", np.nan)
    close, op = last.get("Close", np.nan), last.get("Open", np.nan)
    if all(np.isfinite(v) for v in (vol, vol_sma, close, op)):
        return bool(vol > vol_sma and close >= op)
    return False


def _near_lower_env(df: pd.DataFrame, frac: float = 0.25) -> bool:
    """Price hugging the lower EMA envelope (potential mean-reversion entry):
    normalized band position in the bottom ``frac`` of ``[ENV_DOWN, ENV_UP]``."""
    last = df.iloc[-1]
    close = last.get("Close", np.nan)
    up, down = last.get("ENV_UP", np.nan), last.get("ENV_DOWN", np.nan)
    rng = up - down
    if not all(np.isfinite(v) for v in (close, up, down)) or rng <= 0:
        return False
    return bool((close - down) / rng <= frac)


TechnicalComponent = tuple[str, Callable[[pd.DataFrame], bool]]

#: Single source of truth for the technical score. Add/remove a (name, predicate)
#: tuple and the max score, composite divisor, posture cutoffs and detail keys all
#: follow automatically.
TECHNICAL_COMPONENTS: list[TechnicalComponent] = [
    ("above_ema50", _above_ema50),
    ("rsi_ok", _rsi_ok),
    ("macd_cross_up", _macd_cross_up),
    ("trend_up", _trend_up),
    ("ema50_up", _ema50_up),
    ("vol_confirm", _vol_confirm),
    ("near_lower_env", _near_lower_env),
]

#: Default posture cutoff: Bullish once at least two-thirds of components fire.
DEFAULT_BULL_FRAC = 2 / 3


def _posture(score: int, max_score: int, bull_frac: float = DEFAULT_BULL_FRAC) -> str:
    """Map a score to Bullish/Neutral/Bearish, scaling with the component count:
    Bearish at 0, Bullish at ``score >= ceil(bull_frac * max_score)``, else Neutral."""
    if score <= 0:
        return "Bearish"
    if max_score > 0 and score >= math.ceil(bull_frac * max_score):
        return "Bullish"
    return "Neutral"


def compute_technical_posture(df: pd.DataFrame,
                              components: list[TechnicalComponent] | None = None,
                              bull_frac: float = DEFAULT_BULL_FRAC):
    """Assess technical posture from an indicator-enriched df.

    Runs each predicate in ``components`` (default :data:`TECHNICAL_COMPONENTS`),
    awarding +1 per truthy result. Returns ``(posture_label, tech_score, detail)``
    where ``tech_score`` is 0-len(components) and ``detail`` maps each component
    name to its bool plus an unscored ``nearest_level`` support/resistance context.
    Posture scales with the component count (see :func:`_posture`). Robust to
    NaN/short data; a predicate that raises is treated as False.
    """
    components = TECHNICAL_COMPONENTS if components is None else components
    detail = {name: False for name, _ in components}
    detail["nearest_level"] = None
    if df is None or df.empty:
        return "Bearish", 0, detail

    for name, fn in components:
        try:
            detail[name] = bool(fn(df))
        except Exception:
            detail[name] = False

    # Context (not scored): nearest support/resistance level to the last close.
    close = df.iloc[-1].get("Close", np.nan)
    sr = find_support_resistance(df)
    if sr and np.isfinite(close):
        detail["nearest_level"] = min(sr, key=lambda L: abs(L["level"] - close))

    score = sum(detail[name] for name, _ in components)
    posture = _posture(score, len(components), bull_frac)
    return posture, score, detail


def generate_signals(screened: pd.DataFrame, tech_data: dict,
                     buy_thr: float = 0.60, hold_thr: float = 0.40) -> pd.DataFrame:
    """Fuse fundamentals (weight 0.70) and technicals (weight 0.30) into a
    final Buy / Hold / Watch action per stock.

    Returns a tidy DataFrame: Ticker, Sector, Fundamental Score,
    Technical Posture, Final Action Signal (+ supporting numeric columns).
    """
    if screened is None or screened.empty:
        return pd.DataFrame()

    rows = []
    for ticker, row in screened.iterrows():
        f_score = int(row.get("Fundamental_Score", 0))           # 0–6
        df_t = tech_data.get(ticker)
        posture, t_score, detail = compute_technical_posture(df_t)  # t_score 0-len(components)

        # Weighted composite: fundamentals dominate (0.70) over technicals (0.30).
        composite = 0.70 * (f_score / 6.0) + 0.30 * (t_score / len(TECHNICAL_COMPONENTS))

        if composite >= buy_thr:
            action = "Buy"
        elif composite >= hold_thr:
            action = "Hold"
        else:
            action = "Watch"

        rows.append({
            "Ticker": ticker,
            "Sector": row.get("Sector", "Unknown"),
            "Fundamental Score": f_score,
            "Technical Posture": posture,
            "Tech Score": t_score,
            "Composite": round(composite, 3),
            "Final Action Signal": action,
        })

    result = pd.DataFrame(rows)
    # Order: actionable first (Buy>Hold>Watch), then by composite strength.
    action_rank = {"Buy": 0, "Hold": 1, "Watch": 2}
    result["_rank"] = result["Final Action Signal"].map(action_rank)
    result = (result.sort_values(["_rank", "Composite"], ascending=[True, False])
                    .drop(columns="_rank").reset_index(drop=True))
    return result
