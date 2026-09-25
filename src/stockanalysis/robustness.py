"""Honest error bars for plan-based backtests.

A bare expectancy — "+0.03R over 7,253 trades" — reads as a small positive edge.
It isn't one until three questions are answered, and this module answers them:

* **How wide is the error bar, really?** Entries cluster in time across
  correlated names, so 7,253 trades are closer to ~118 monthly draws than 7,253
  independent ones. :func:`cluster_expectancy` computes the SE over calendar-month
  clusters; on the broad universe it is ~2.2x the naive per-trade SE, which is
  the difference between p=0.049 and p=0.39.
* **Does it hold in both halves?** :func:`evaluate` splits at one date and reports
  each half, plus the count of positive years. A rule that pays only in one
  regime shows up here.
* **Does it beat a random entry?** The trade plan's stop/target geometry has a
  positive expectancy on its own, so the bar is not zero — it is the same plan
  walked from randomly chosen bars (built by
  :func:`stockanalysis.backtest.random_entry_trades`). :func:`compare` reports the
  gate's edge over that null with a combined error bar and a plain verdict.

Pure numpy/pandas + stdlib ``math`` (the normal p-value is ``erfc``, so no scipy).
Trades are duck-typed: anything with ``r_multiple`` and ``entry_date``. Following
the package rule, too little data degrades to NaN rather than raising.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

#: Two-sided 95% normal quantile.
Z95 = 1.96


def _frame(trades) -> pd.DataFrame:
    df = pd.DataFrame({
        "r": [float(t.r_multiple) for t in trades],
        "date": pd.to_datetime([t.entry_date for t in trades]),
    }, columns=["r", "date"])
    return df[np.isfinite(df["r"].to_numpy(float))]


def _two_sided_p(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def cluster_expectancy(trades) -> dict:
    """Mean R with a **month-clustered** SE, 95% CI, t and two-sided p.

    ``se = sqrt(sum_i w_i^2 (m_i - mu)^2) * sqrt(k / (k - 1))`` over the ``k``
    calendar months, where ``m_i`` is month i's mean R and ``w_i`` its share of
    trades — the weighted mean of the month means is exactly the per-trade mean,
    so ``exp_r`` agrees with the unclustered figure; only the error bar changes.
    Fewer than two months leaves ``se``/CI/``t``/``p`` NaN.
    """
    nan = float("nan")
    df = _frame(trades)
    out = {"n": int(len(df)), "months": 0, "exp_r": nan, "se": nan,
           "ci_lo": nan, "ci_hi": nan, "t": nan, "p": nan}
    if df.empty:
        return out
    mu = float(df["r"].mean())
    g = df.groupby(df["date"].dt.to_period("M"))["r"]
    means, sizes = g.mean().to_numpy(float), g.size().to_numpy(float)
    k = len(means)
    out.update(months=int(k), exp_r=mu)
    if k < 2:
        return out
    w = sizes / sizes.sum()
    se = float(np.sqrt(((w ** 2) * (means - mu) ** 2).sum() * k / (k - 1)))
    out.update(se=se, ci_lo=mu - Z95 * se, ci_hi=mu + Z95 * se)
    if se > 0:
        out.update(t=mu / se, p=_two_sided_p(mu / se))
    return out


def compare(gate: dict, null: "dict | None") -> dict:
    """The gate's edge over the null: ``exp_r`` (gate minus null), combined ``se``,
    CI, ``p`` and a verdict — keyed like :func:`cluster_expectancy` so the three
    series tabulate alike.

    The two SEs are combined as if independent. Random bars are spread evenly over
    time while gate entries bunch up, so the covariance is small; if anything the
    independent form overstates the SE, which errs toward "not distinguishable".
    The verdict turns on whether the 95% CI excludes zero — never on the point
    estimate — and a NaN error bar is ``insufficient data``, not a pass.
    """
    if not gate or not null:
        return {}
    diff = gate["exp_r"] - null["exp_r"]
    se = math.sqrt(gate["se"] ** 2 + null["se"] ** 2)
    lo, hi = diff - Z95 * se, diff + Z95 * se
    if not (np.isfinite(se) and se > 0):
        verdict, p = "insufficient data", float("nan")
    else:
        p = _two_sided_p(diff / se)
        verdict = ("beats random entry" if lo > 0 else
                   "worse than random entry" if hi < 0 else
                   "not distinguishable from random entry")
    return {"exp_r": diff, "se": se, "ci_lo": lo, "ci_hi": hi, "p": p,
            "verdict": verdict}


def yearly_expectancy(trades) -> dict:
    """``{year: {"n", "exp_r"}}`` by entry year — regime robustness at a glance."""
    df = _frame(trades)
    if df.empty:
        return {}
    g = df.groupby(df["date"].dt.year)["r"]
    return {int(y): {"n": int(n), "exp_r": float(m)}
            for y, n, m in zip(g.size().index, g.size(), g.mean())}


def midpoint(dates) -> pd.Timestamp:
    """Calendar midpoint of ``dates``, floored to a day — the default split."""
    d = pd.DatetimeIndex(dates)
    return (d.min() + (d.max() - d.min()) / 2).floor("D")


def _section(gate, null) -> dict:
    g = cluster_expectancy(gate)
    n = cluster_expectancy(null) if null is not None else None
    return {"gate": g, "null": n, "edge": compare(g, n)}


def evaluate(trades, null_trades, split_at) -> dict:
    """Everything above, for the whole run and each side of ``split_at``.

    ``first`` is entries strictly before ``split_at``, ``second`` the rest; gate
    and null are cut at the same date so each half compares like with like.
    ``null_trades=None`` (null skipped) still reports the gate, with ``null``
    None and ``edge`` empty.
    """
    split_at = pd.Timestamp(split_at)

    def cut(ts, first):
        if ts is None:
            return None
        return [t for t in ts if (pd.Timestamp(t.entry_date) < split_at) == first]

    gate_y = yearly_expectancy(trades)
    null_y = yearly_expectancy(null_trades) if null_trades is not None else {}
    yearly = {y: {"n": d["n"], "gate_r": d["exp_r"],
                  "null_r": null_y.get(y, {}).get("exp_r", float("nan"))}
              for y, d in sorted(gate_y.items())}
    return {
        "split_at": split_at,
        "all": _section(trades, null_trades),
        "first": _section(cut(trades, True), cut(null_trades, True)),
        "second": _section(cut(trades, False), cut(null_trades, False)),
        "yearly": yearly,
        "years": len(yearly),
        "positive_years": sum(d["gate_r"] > 0 for d in yearly.values()),
        "negative_years": [y for y, d in yearly.items() if not d["gate_r"] > 0],
    }
