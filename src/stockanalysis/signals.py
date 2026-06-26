"""Signal engine: technical posture + the fused Buy/Hold/Watch matrix.

Score contract (keep in sync with the divisors below):
  - fundamental score: 0–6 (from :mod:`stockanalysis.screener`)
  - technical score:   0–5 (from :func:`compute_technical_posture`)
  - composite = 0.70*(fund/6) + 0.30*(tech/5) -> Buy >=0.60, Hold >=0.40, else Watch
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import fit_regression_channel, find_support_resistance


def compute_technical_posture(df: pd.DataFrame, crossover_lookback: int = 5,
                              obv_lookback: int = 20):
    """Assess technical posture from an indicator-enriched df.

    Returns ``(posture_label, tech_score, detail_dict)`` where tech_score is 0–5:
      +1 price above EMA50, +1 RSI in a healthy zone (35–70),
      +1 recent bullish MACD crossover (line crossed above signal),
      +1 up-trend (positive regression-channel slope),
      +1 volume confirmation (OBV rising over the lookback, or last volume above
         its 20-day average on an up day).
    Support/resistance context is stashed in detail['nearest_level'] but not scored.
    Posture: >=3 Bullish, 1–2 Neutral, 0 Bearish. Robust to NaN/short data.
    """
    detail = {"above_ema50": False, "rsi_ok": False, "macd_cross_up": False,
              "trend_up": False, "vol_confirm": False, "nearest_level": None}
    if df is None or df.empty:
        return "Bearish", 0, detail

    last = df.iloc[-1]
    close = last.get("Close", np.nan)

    # 1) Trend: price above the 50-day EMA.
    ema50 = last.get("EMA50", np.nan)
    if np.isfinite(close) and np.isfinite(ema50):
        detail["above_ema50"] = bool(close > ema50)

    # 2) Momentum: RSI healthy — not overbought (<70) and not deeply oversold (>35).
    rsi = last.get("RSI", np.nan)
    if np.isfinite(rsi):
        detail["rsi_ok"] = bool(35 < rsi < 70)

    # 3) Recent bullish MACD crossover: MACD was <= signal and is now > signal
    #    at any point within the lookback window.
    macd, sig = df.get("MACD"), df.get("MACD_SIG")
    if macd is not None and sig is not None and len(df) > crossover_lookback:
        diff = (macd - sig).dropna()
        if len(diff) > crossover_lookback:
            recent = diff.iloc[-crossover_lookback:]
            prev = diff.iloc[-crossover_lookback - 1:-1]
            # crossover where sign flips from <=0 to >0 across consecutive bars
            crossed = ((prev.values <= 0) & (recent.values > 0)).any()
            detail["macd_cross_up"] = bool(crossed)

    # 4) Trend channel: a positive regression slope marks a prevailing up-trend.
    channel = fit_regression_channel(df.get("Close"))
    if channel is not None:
        detail["trend_up"] = bool(channel["slope"] > 0)

    # 5) Volume confirmation: OBV rising over the window, OR the latest volume is
    #    above its 20-day average on an up day (close >= open).
    obv = df.get("OBV")
    if obv is not None:
        obv_s = obv.dropna()
        if len(obv_s) > obv_lookback:
            detail["vol_confirm"] = bool(obv_s.iloc[-1] > obv_s.iloc[-obv_lookback])
    if not detail["vol_confirm"]:
        vol = last.get("Volume", np.nan)
        vol_sma = last.get("VOL_SMA20", np.nan)
        op = last.get("Open", np.nan)
        if all(np.isfinite(v) for v in (vol, vol_sma, close, op)):
            detail["vol_confirm"] = bool(vol > vol_sma and close >= op)

    # Context (not scored): nearest support/resistance level to the last close.
    sr = find_support_resistance(df)
    if sr and np.isfinite(close):
        detail["nearest_level"] = min(sr, key=lambda L: abs(L["level"] - close))

    scored = ["above_ema50", "rsi_ok", "macd_cross_up", "trend_up", "vol_confirm"]
    tech_score = int(sum(bool(detail[k]) for k in scored))  # 0–5
    posture = "Bullish" if tech_score >= 3 else ("Neutral" if tech_score >= 1 else "Bearish")
    return posture, tech_score, detail


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
        posture, t_score, detail = compute_technical_posture(df_t)  # t_score 0–5

        # Weighted composite: fundamentals dominate (0.70) over technicals (0.30).
        composite = 0.70 * (f_score / 6.0) + 0.30 * (t_score / 5.0)

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
