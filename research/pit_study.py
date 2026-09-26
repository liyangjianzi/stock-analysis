"""Does the fundamental screen predict anything? (point-in-time study, 2026-09-25)

The live Buy rule is ``fund_score >= DEFAULT_FUND_MIN (4) AND gate``. The gate
half has been measured to death; this measures the other half, which could not
be tested before because yfinance ``.info`` is today-only. Metrics come from SEC
filings as filed (pit_fundamentals), scored by the package's own
``screen_fundamentals`` so the thresholds are the shipped ones.

Pre-declared primary tests (everything else is shape/secondary):

  A. Cross-section: at each month-end, 3-month forward return of score>=4 minus
     score<4, **sector-neutral**. Newey-West SE over the monthly series. Must be
     positive with a CI excluding zero in BOTH halves (split 2022-01-01).
  B. The gate's plan trades split by the entry-date score: does Buy = gate AND
     score>=4 beat the gate alone, and beat random entries on score>=4 names?

Coverage rule: a name-month is scored only if the name has a filing in the last
200 days. A missing *history* (IPO, spin-off, or a re-registered CIK such as XOM
or BLK) is a data artifact, not a failed test; a missing *metric* within a
covered name still fails, exactly as in the live screen.

    python pit_study.py            # needs pit_fetch.py + pit_fundamentals.py first
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from pit_fundamentals import COLS, PASSES, load, metrics_at
from stats import by_period, nw, window
from stockanalysis import backtest as bt, cache, config, robustness
from stockanalysis.screener import screen_fundamentals

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNIVERSE = ROOT / "data" / "universe_sp500.csv"
SPLIT = pd.Timestamp("2022-01-01")
HALVES = (("first", None, SPLIT), ("second", SPLIT, None))
STALE = pd.Timedelta(days=200)
HORIZONS = (1, 3, 6, 12)
NULL_REPS = 5
# Class A trades ~1,500x class B, so summing the classes' share counts breaks
# market cap (P/E comes out ~0.005). Every other multi-class name trades near par.
EXCLUDE = {"BRK-B"}


# --- stats -------------------------------------------------------------------

def fmt(r: dict, pct=True) -> str:
    s = 100 if pct else 1
    u = "%" if pct else "R"
    return (f"{r['mean'] * s:+6.2f}{u}  [{r['lo'] * s:+6.2f}, {r['hi'] * s:+6.2f}]  "
            f"p={r['p']:.3f}  T={r['T']}")


# --- data --------------------------------------------------------------------

def covered(m: pd.DataFrame) -> pd.Series:
    """A name-date is scored only if it has a filing in the last STALE days."""
    return m["filed"].notna() & ((m["date"] - m["filed"]) <= STALE)


def score(df: pd.DataFrame, live_div_bug: bool = False) -> pd.DataFrame:
    x = df[COLS].copy()
    if live_div_bug:
        # What the live pipeline sees: Yahoo returns percent, the loader divides only
        # values > 1, so any yield at or under 1% is read 100x too large.
        y = x["Div_Yield"]
        x["Div_Yield"] = np.where(y > 0.01, y, y * 100)
    s = screen_fundamentals(x)
    return s[PASSES + ["Fundamental_Score"]]


def monthly_panel(prices_adj: dict, snaps, prices_raw, sec) -> pd.DataFrame:
    closes = pd.DataFrame({t: d["Close"] for t, d in prices_adj.items()})
    last = closes.index.max()
    month = closes.index.to_period("M")
    complete = month < last.to_period("M")          # drop the in-progress month
    closes = closes[complete]
    me_dates = closes.groupby(closes.index.to_period("M")).apply(lambda g: g.index.max())
    me = closes.groupby(closes.index.to_period("M")).last()
    me.index = pd.DatetimeIndex(me_dates.values)
    fwd = {h: (me.shift(-h) / me - 1) for h in HORIZONS}

    q = me.stack().rename("close").reset_index()
    q.columns = ["date", "Ticker", "close"]
    m = metrics_at(q[["Ticker", "date"]], snaps, prices_raw)
    m = m.merge(q, on=["Ticker", "date"])
    m["covered"] = covered(m)
    m = pd.concat([m, score(m)], axis=1)
    m["score_live"] = score(m, live_div_bug=True)["Fundamental_Score"]
    for h in HORIZONS:
        f = fwd[h].stack().rename(f"r{h}").reset_index()
        f.columns = ["date", "Ticker", f"r{h}"]
        m = m.merge(f, on=["date", "Ticker"], how="left")
    m["sector"] = m["Ticker"].map(sec).fillna("Unknown")
    return m


# --- test A: cross-section ------------------------------------------------------

def spread_series(m: pd.DataFrame, h: int, flag: pd.Series, neutral: bool) -> pd.Series:
    """Monthly mean(excess | flag) - mean(excess | ~flag)."""
    r = m[f"r{h}"]
    base = m.groupby(["date", "sector"])[f"r{h}"].transform("mean") if neutral \
        else m.groupby("date")[f"r{h}"].transform("mean")
    ex = (r - base).rename("ex")
    d = pd.DataFrame({"date": m["date"], "ex": ex, "flag": flag.to_numpy()}).dropna()
    g = d.groupby(["date", "flag"])["ex"].mean().unstack()
    return (g.get(True) - g.get(False)).dropna()


def test_a(m: pd.DataFrame):
    c = m[m["covered"] & m["r1"].notna()]
    print(f"\n=== A. Cross-section: {c['date'].nunique()} month-ends "
          f"{c['date'].min().date()}..{c['date'].max().date()}, "
          f"{c.groupby('date').size().mean():.0f} names/month covered "
          f"(of {m.groupby('date').size().mean():.0f})")
    print("metric coverage among covered name-months: " + ", ".join(
        f"{k} {np.isfinite(c[k].to_numpy(float)).mean():.0%}" for k in COLS))
    print("score distribution: " + " ".join(
        f"{s}:{v:.1%}" for s, v in c["Fundamental_Score"].value_counts(normalize=True).sort_index().items()))
    print(f"share scoring >=4: {(c['Fundamental_Score'] >= 4).mean():.1%}  "
          f"(live-bug variant: {(c['score_live'] >= 4).mean():.1%})")

    print("\n-- PRIMARY: score>=4 minus <4, sector-neutral, 3m forward --")
    s = spread_series(c, 3, c["Fundamental_Score"] >= 4, neutral=True)
    for k, r in by_period(s, HALVES, 3).items():
        print(f"  {k:6s} {fmt(r)}")

    print("\n-- score>=4 minus <4 by horizon (raw | sector-neutral | ex-Financials neutral) --")
    xf = c[c["sector"] != "Financials"]
    for h in HORIZONS:
        cc = c[c[f"r{h}"].notna()]
        xx = xf[xf[f"r{h}"].notna()]
        for label, frame, neu in (("raw", cc, False), ("neutral", cc, True), ("exFin", xx, True)):
            hv = by_period(spread_series(frame, h, frame["Fundamental_Score"] >= 4, neu), HALVES, h)
            print(f"  {h:2d}m {label:8s} all {fmt(hv['all'])} | "
                  f"1st {hv['first']['mean']*100:+.2f}% p={hv['first']['p']:.2f} | "
                  f"2nd {hv['second']['mean']*100:+.2f}% p={hv['second']['p']:.2f}")

    print("\n-- cutoff shape (sector-neutral 3m, score>=k minus <k) --")
    for k in (2, 3, 4, 5, 6):
        hv = by_period(spread_series(c, 3, c["Fundamental_Score"] >= k, True), HALVES, 3)
        share = (c["Fundamental_Score"] >= k).mean()
        print(f"  >={k} ({share:5.1%} of names)  all {fmt(hv['all'])} | "
              f"1st {hv['first']['mean']*100:+.2f}% | 2nd {hv['second']['mean']*100:+.2f}%")

    print("\n-- per score level: mean sector-neutral 3m excess --")
    ex = c["r3"] - c.groupby(["date", "sector"])["r3"].transform("mean")
    lv = pd.DataFrame({"date": c["date"], "s": c["Fundamental_Score"], "ex": ex}).dropna()
    g = lv.groupby(["date", "s"])["ex"].mean().unstack()
    for s_ in g.columns:
        print(f"  score {s_}: {fmt(nw(g[s_], 3))}")

    print("\n-- rank IC: Spearman(score, 3m return) per month, sector-demeaned --")
    ics = []
    for d, grp in lv.groupby("date"):
        if grp["s"].nunique() > 1 and len(grp) > 20:
            ics.append((d, grp["s"].rank().corr(grp["ex"].rank())))
    ic = pd.Series(dict(ics))
    for k, r in by_period(ic, HALVES, 3).items():
        print(f"  {k:6s} IC {r['mean']:+.4f} [{r['lo']:+.4f}, {r['hi']:+.4f}] p={r['p']:.3f}")

    print("\n-- each test alone: pass minus fail, sector-neutral 3m --")
    for p in PASSES:
        hv = by_period(spread_series(c, 3, c[p], True), HALVES, 3)
        print(f"  {p:9s} pass {c[p].mean():5.1%}  all {fmt(hv['all'])} | "
              f"1st {hv['first']['mean']*100:+.2f}% | 2nd {hv['second']['mean']*100:+.2f}%")

    print("\n-- live-bug screen (yields <=1% read as >1.5%): >=4 minus <4, neutral 3m --")
    hv = by_period(spread_series(c, 3, c["score_live"] >= 4, True), HALVES, 3)
    for k, r in hv.items():
        print(f"  {k:6s} {fmt(r)}")

    print("\n-- by year: >=4 minus <4, sector-neutral 3m (formation year) --")
    s3 = spread_series(c, 3, c["Fundamental_Score"] >= 4, True)
    print("  " + "  ".join(f"{y}:{v*100:+.2f}%" for y, v in s3.groupby(s3.index.year).mean().items()))


# --- test B: the gate's trades ----------------------------------------------------

def _trades_from_sheet(path: Path) -> list:
    t = pd.read_excel(path, sheet_name="Planned Trades")
    return [SimpleNamespace(ticker=tk, entry_date=pd.Timestamp(d), r_multiple=float(r))
            for tk, d, r in zip(t["Ticker"], t["Entry Date"], t["R"])]


def _scored(trades, snaps, prices_raw) -> pd.DataFrame:
    q = pd.DataFrame({"Ticker": [t.ticker for t in trades],
                      "date": pd.to_datetime([t.entry_date for t in trades]),
                      "R": [t.r_multiple for t in trades]})
    m = metrics_at(q, snaps, prices_raw)
    m["covered"] = covered(m)
    return pd.concat([m, score(m)], axis=1)


def _stats(df: pd.DataFrame) -> dict:
    ts = [SimpleNamespace(r_multiple=r, entry_date=d) for r, d in zip(df["R"], df["date"])]
    return robustness.cluster_expectancy(ts)


def _fmt_r(c: dict) -> str:
    return (f"n={c['n']:5d}  {c['exp_r']:+.3f}R  [{c['ci_lo']:+.3f}, {c['ci_hi']:+.3f}]  "
            f"p={c['p']:.3f}")


def test_b(prices_adj, snaps, prices_raw):
    sheets = sorted((ROOT / "output" / "backtest").glob("*/backtest.xlsx"))
    sheet = next(p for p in reversed(sheets)
                 if "Planned Trades" in pd.ExcelFile(p).sheet_names)
    trades = [t for t in _trades_from_sheet(sheet) if t.ticker not in EXCLUDE]
    g = _scored(trades, snaps, prices_raw)
    print(f"\n=== B. Gate plan trades from {sheet.relative_to(ROOT)}: {len(g)} trades, "
          f"{g['covered'].mean():.0%} with point-in-time fundamentals")
    g = g[g["covered"]]

    # The CLI backtest's walk (max_hold 3m, 10 bps) is simulate_planned_trades' default.
    null_trades = bt.random_entry_trades(prices_adj, trades, reps=NULL_REPS, seed=0)
    n = _scored(null_trades, snaps, prices_raw)
    n = n[n["covered"]]
    print(f"random-entry null: {NULL_REPS} reps, {len(n)} covered trades")

    rows = (("gate, any score        ", "gate"), ("gate, score>=4  (=Buy) ", "buy"),
            ("gate, score<4          ", "gate<4"), ("null, any score        ", "null"),
            ("null, score>=4         ", "null>=4"))
    edges = (("Buy - gate<4           ", "buy", "gate<4"),
             ("Buy - null>=4          ", "buy", "null>=4"),
             ("null>=4 - null<4       ", "null>=4", "null<4"))
    for label, lo, hi in (("all", None, None), *HALVES):
        gg, nn = g[window(g["date"], lo, hi)], n[window(n["date"], lo, hi)]
        st = {k: _stats(d) for k, d in (
            ("gate", gg), ("buy", gg[gg["Fundamental_Score"] >= 4]),
            ("gate<4", gg[gg["Fundamental_Score"] < 4]), ("null", nn),
            ("null>=4", nn[nn["Fundamental_Score"] >= 4]),
            ("null<4", nn[nn["Fundamental_Score"] < 4]))}
        print(f"\n-- {label} --")
        for text, k in rows:
            print(f"  {text} {_fmt_r(st[k])}")
        for text, a, b in edges:
            e = robustness.compare(st[a], st[b])
            print(f"  {text} {e['exp_r']:+.3f}R [{e['ci_lo']:+.3f}, {e['ci_hi']:+.3f}] p={e['p']:.3f}")

    print("\n-- gate trades by score (all periods) --")
    for s_ in sorted(g["Fundamental_Score"].unique()):
        print(f"  score {s_}: {_fmt_r(_stats(g[g['Fundamental_Score'] == s_]))}")


if __name__ == "__main__":
    sectors = config.load_watchlist_csv(UNIVERSE)
    tickers = [t for t in sectors if t not in EXCLUDE]
    with cache.connect() as conn:
        prices_adj = cache.load_universe(conn, tickers)
    snaps, prices_raw = load()
    m = monthly_panel(prices_adj, snaps, prices_raw, sectors)
    m.to_pickle(HERE / "pit_panel.pkl")
    test_a(m)
    test_b(prices_adj, snaps, prices_raw)
