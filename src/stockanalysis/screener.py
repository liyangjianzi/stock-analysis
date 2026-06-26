"""Fundamental screener: score each stock 0–6 against six thresholds.

NaN-safe by design — comparisons against NaN yield ``False`` (a fail) rather
than raising, so missing data simply doesn't earn a point. Do not "fix" this by
dropping NaN rows; it would change the scoring semantics.
"""
from __future__ import annotations

import pandas as pd


def screen_fundamentals(
    df: pd.DataFrame,
    pe_max: float = 25.0,
    eps_growth_min: float = 0.10,
    rev_growth_min: float = 0.08,
    de_max: float = 1.0,
    div_yield_min: float = 0.015,
    fcf_min: float = 0.0,
) -> pd.DataFrame:
    """Score each stock 0–6 against the six fundamental thresholds.

    NaN-safe: any missing metric evaluates to False (a fail) rather than raising.
    Returns a copy of ``df`` with per-metric boolean pass columns + a
    ``Fundamental_Score`` column, sorted by score (desc) then P/E (asc).
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    # Each comparison on a NaN yields False automatically -> missing == fail.
    # We compute boolean pass flags per metric (note the direction of each test).
    out["Pass_PE"]      = out["PE"] < pe_max                  # lower is better
    out["Pass_EPS"]     = out["EPS_Growth"] > eps_growth_min  # higher is better
    out["Pass_Rev"]     = out["Rev_Growth"] > rev_growth_min  # higher is better
    out["Pass_DE"]      = out["Debt_Equity"] < de_max         # lower is better
    out["Pass_Div"]     = out["Div_Yield"] > div_yield_min    # higher is better
    out["Pass_FCF"]     = out["FCF"] > fcf_min                # positive cash flow

    pass_cols = ["Pass_PE", "Pass_EPS", "Pass_Rev", "Pass_DE", "Pass_Div", "Pass_FCF"]
    # Fill any residual NaN flags with False, then sum to a 0–6 integer score.
    out[pass_cols] = out[pass_cols].fillna(False).astype(bool)
    out["Fundamental_Score"] = out[pass_cols].sum(axis=1).astype(int)

    # Rank: best score first; break ties with the cheaper valuation (lower P/E).
    out = out.sort_values(
        by=["Fundamental_Score", "PE"], ascending=[False, True], na_position="last"
    )
    return out
