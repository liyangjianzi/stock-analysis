"""Does 12-1 momentum beat an equal-weight S&P 500? (monthly portfolio, 2026-09-25)

The one open lead from the 2026-09-16 pass: cross-sectional 12-1 momentum was
the only entry family whose 2022-26 expectancy held up (+0.065R -> +0.061R).
That was measured trade-by-trade through the plan's stops, on *today's* 503
names. This measures the signal as the strategy it actually is -- a monthly-
rebalanced portfolio -- on point-in-time membership, over 2006-2026, of which
2006-2015 was never looked at by the earlier pass.

Pre-declared (set before any result was seen):

  Primary   12-1 momentum, top 10% of the month's members, equal weight, held one
            month; minus the equal-weight average of all members. Net of 10 bps
            per side on turnover. Newey-West SE on the monthly excess series.
  Pass bar  full-period 95% CI excludes zero, AND positive in each of 2006-15,
            2016-21 and 2022-26, AND the neighbouring cells of the grid (top
            5/20%, 6-1 and 9-1 lookbacks) have the same sign.

Universe: members at month-end t (fja05680 reconstruction) that Yahoo still
prices at t and 12 months earlier. Yahoo lost most names that left the index;
the missing share is printed per year, and RSP -- the real equal-weight ETF --
measures how far the rebuilt benchmark drifts from the true one.

    python mom_study.py            # needs mom_fetch.py first; seconds
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from mom_fetch import BENCHMARK_ETF, MEMBERS_PKL, PRICES_PKL, yahoo

HERE = Path(__file__).resolve().parent
COST_BPS = 10.0
PERIODS = (("2006-15", "2006-01-01", "2016-01-01"),
           ("2016-21", "2016-01-01", "2022-01-01"),
           ("2022-26", "2022-01-01", "2027-01-01"))
PRIMARY = (12, 1, 0.10)
GRID_TOP = (0.05, 0.10, 0.20, 0.30)
GRID_LOOK = ((6, 1), (9, 1), (12, 1), (12, 0))


# --- stats -------------------------------------------------------------------

def nw(x, lags: int = 3) -> dict:
    x = pd.Series(x, dtype=float).dropna().to_numpy()
    T = len(x)
    if T < 6:
        return dict(mean=np.nan, se=np.nan, lo=np.nan, hi=np.nan, p=np.nan, T=T, ir=np.nan)
    mu, e = x.mean(), x - x.mean()
    var = e @ e / T
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / T
    se = math.sqrt(var / T)
    p = math.erfc(abs(mu / se) / math.sqrt(2)) if se > 0 else np.nan
    sd = x.std(ddof=1)
    return dict(mean=mu, se=se, lo=mu - 1.96 * se, hi=mu + 1.96 * se, p=p, T=T,
                ir=mu / sd * math.sqrt(12) if sd > 0 else np.nan)


def ann(r: dict) -> str:
    """Monthly stats shown annualized (x12), as % a year."""
    return (f"{r['mean'] * 1200:+6.2f}%/yr [{r['lo'] * 1200:+6.2f}, {r['hi'] * 1200:+6.2f}] "
            f"p={r['p']:.3f} IR={r['ir']:+.2f} T={r['T']}")


def drawdown(excess: pd.Series) -> float:
    wealth = (1 + excess.fillna(0)).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


# --- panel -------------------------------------------------------------------

def monthly(px: pd.DataFrame):
    """Month-end closes, plus whether each ticker traded in the month's last week."""
    close = px.resample("ME").last()
    bar_day = pd.DataFrame(np.where(px.notna(), px.index.values[:, None],
                                    np.datetime64("NaT", "ns")),
                           index=px.index, columns=px.columns)
    last_day = bar_day.resample("ME").last()
    month_end = pd.Series(close.index, index=close.index)
    active = last_day.ge(month_end - pd.Timedelta(days=7), axis=0)
    return close, active


def forward_returns(close: pd.DataFrame, active: pd.DataFrame) -> pd.DataFrame:
    """r[t] = return from month-end t to t+1. A name active at t with no bar in
    t+1 (delisted on the turn of the month) is held as cash (0); one that stopped
    mid-month is marked at its last close -- Yahoo has no delisting returns."""
    nxt = close.shift(-1)
    r = nxt / close - 1
    gone = active & nxt.isna()
    gone.iloc[-1] = False                     # the last month has no t+1 at all
    return r.mask(gone, 0.0)


def eligible_sets(members: dict, close: pd.DataFrame, active: pd.DataFrame,
                  look: int, universe: str, today: frozenset) -> dict:
    out = {}
    for t in close.index:
        if t not in members:
            continue
        names = members[t] if universe == "pit" else today
        cols = [yahoo(n) for n in names if yahoo(n) in close.columns]
        i = close.index.get_loc(t)
        if i < look:
            continue
        ok = [c for c in cols if active.at[t, c]
              and np.isfinite(close.iat[i - look, close.columns.get_loc(c)])]
        out[t] = ok
    return out


def run(close, fwd, elig, look: int, skip: int, top: float, bottom: bool = False):
    """Monthly (portfolio, benchmark, turnover) series for one grid cell."""
    sig = close.shift(skip) / close.shift(look) - 1
    rows, prev = [], None
    for t, names in elig.items():
        s = sig.loc[t, names].dropna()
        r = fwd.loc[t, s.index]
        if len(s) < 50 or r.isna().all():
            continue
        rk = s.rank(pct=True)
        pick = rk[rk <= top].index if bottom else rk[rk > 1 - top].index
        port = r[pick].mean()
        bench = r.mean()
        turnover = 1.0 if prev is None else 1 - len(set(pick) & prev) / max(len(pick), 1)
        prev = set(pick)
        rows.append((t, port, bench, turnover, len(pick), len(s)))
    df = pd.DataFrame(rows, columns=["t", "port", "bench", "turnover", "n_pick", "n_univ"]
                      ).set_index("t")
    df["cost"] = 2 * df["turnover"] * COST_BPS / 1e4
    df["excess_gross"] = df["port"] - df["bench"]
    df["excess"] = df["excess_gross"] - df["cost"]
    return df


def by_period(s: pd.Series) -> dict:
    out = {"all": nw(s)}
    for name, lo, hi in PERIODS:
        out[name] = nw(s[(s.index >= lo) & (s.index < hi)])
    return out


# --- report ------------------------------------------------------------------

def main():
    members = pickle.loads(MEMBERS_PKL.read_bytes())
    px = pickle.loads(PRICES_PKL.read_bytes())
    rsp = px.pop(BENCHMARK_ETF) if BENCHMARK_ETF in px else None
    close, active = monthly(px)
    fwd = forward_returns(close, active)
    # Guard against bad prints: a month outside [-95%, +300%] is a data error here.
    bad = (fwd < -0.95) | (fwd > 3.0)
    print(f"bad-print months masked: {int(bad.sum().sum())}")
    fwd = fwd.mask(bad)
    today = members[max(members)]

    elig = {u: eligible_sets(members, close, active, 12, u, today) for u in ("pit", "today")}
    first = pd.Timestamp("2006-01-31")
    elig = {u: {t: v for t, v in e.items() if t >= first} for u, e in elig.items()}

    print("\n=== coverage: members priced at t with 12 months of history ===")
    cov = pd.DataFrame({t: (len(members[t]), len(elig["pit"][t])) for t in elig["pit"]},
                       index=["members", "priced"]).T
    cov["missing"] = 1 - cov["priced"] / cov["members"]
    print(cov.groupby(cov.index.year)["missing"].mean().map("{:.0%}".format).to_string())

    L, S, TOP = PRIMARY
    res = {u: run(close, fwd, elig[u], L, S, TOP) for u in ("pit", "today")}

    print("\n=== benchmark check: rebuilt equal-weight vs RSP (the real one) ===")
    if rsp is not None:
        rsp_m = rsp.resample("ME").last().pct_change().shift(-1)
        for u in ("pit", "today"):
            b = res[u]["bench"]
            d = (b - rsp_m.reindex(b.index)).dropna()
            print(f"  {u:5s}: rebuilt minus RSP {nw(d)['mean'] * 1200:+.2f}%/yr "
                  f"(RSP charges ~0.2-0.4%/yr), tracking error {d.std() * math.sqrt(12):.2%}, "
                  f"corr {b.corr(rsp_m.reindex(b.index)):.3f}")
            for name, lo, hi in PERIODS:
                dd = d[(d.index >= lo) & (d.index < hi)]
                print(f"         {name}: {dd.mean() * 1200:+.2f}%/yr")

    for u, title in (("pit", "POINT-IN-TIME membership (primary)"),
                     ("today", "today's constituents (what the lead used)")):
        df = res[u]
        print(f"\n=== {title}: 12-1, top 10%, EW, monthly ===")
        print(f"  avg names held {df['n_pick'].mean():.0f} of {df['n_univ'].mean():.0f}; "
              f"one-way turnover {df['turnover'].iloc[1:].mean():.0%}/month; "
              f"cost {df['cost'].mean() * 1200:.2f}%/yr")
        g, n = by_period(df["excess_gross"]), by_period(df["excess"])
        for k in n:
            print(f"  {k:8s} gross {g[k]['mean'] * 1200:+6.2f}%/yr | net {ann(n[k])}")
        yr = df["excess"].groupby(df.index.year).sum()
        print(f"  positive years (net): {(yr > 0).sum()} of {len(yr)}: "
              + " ".join(f"{y}:{v * 100:+.1f}" for y, v in yr.items()))
        print(f"  max drawdown of excess (net): {drawdown(df['excess']):.1%}")
        worst = df["excess"].nsmallest(5)
        print("  worst months (net excess): " + ", ".join(
            f"{t:%Y-%m} {v:+.1%}" for t, v in worst.items()))

    print("\n=== grid (point-in-time, net excess %/yr: all | 2006-15 | 2016-21 | 2022-26) ===")
    for look, skip in GRID_LOOK:
        for top in GRID_TOP:
            df = run(close, fwd, elig["pit"], look, skip, top)
            p = by_period(df["excess"])
            mark = "  <- primary" if (look, skip, top) == PRIMARY else ""
            print(f"  {look:2d}-{skip} top {top:4.0%}: {ann(p['all'])} | "
                  + " | ".join(f"{p[k]['mean'] * 1200:+6.2f}" for k, *_ in PERIODS) + mark)

    print("\n=== secondary: top 10% minus bottom 10% (point-in-time, 12-1, net of both legs) ===")
    top_df = res["pit"]
    bot_df = run(close, fwd, elig["pit"], L, S, TOP, bottom=True)
    ls = (top_df["port"] - bot_df["port"] - top_df["cost"] - bot_df["cost"]).dropna()
    for k, r in by_period(ls).items():
        print(f"  {k:8s} {ann(r)}")

    res["pit"].to_pickle(HERE / "mom_primary.pkl")


if __name__ == "__main__":
    main()
