"""Rebuild the six screen metrics as they were knowable on any past date.

Reads ``pit_facts.pkl`` + ``pit_prices.pkl`` (from pit_fetch.py). The unit of
work is a **filing snapshot**: fundamentals only change when a 10-K/10-Q is
filed, so each ticker gets one row per distinct filing date, computed from
facts filed on or before it. :func:`metrics_at` then joins any (ticker, date)
query to the latest snapshot filed *strictly before* that date and prices it
with that day's close -- no fact is used before the day after it was filed.

Metric definitions (approximating the yfinance ``.info`` fields the live
pipeline reads -- quarterly YoY growth, trailing-twelve-month flows, mrq balance
sheet):

  PE           market cap / TTM net income           (NaN when NI <= 0, as Yahoo)
  EPS_Growth   latest quarter's net income vs the same quarter a year earlier
               (net income, not EPS: per-share history is split-contaminated)
  Rev_Growth   latest quarter's revenue vs the same quarter a year earlier
  Debt_Equity  total debt / stockholders' equity      (NaN when equity <= 0)
  Div_Yield    TTM common dividends paid / market cap  (a fraction, 0.015 = 1.5%)
  FCF          TTM operating cash flow - TTM capex

Market cap = split-adjusted close x cover-page share count x every split since
the share count's date, i.e. price and share count on the same basis.
"""
from __future__ import annotations

import pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from pit_fetch import FACTS_PKL, PRICES_PKL, TAGS

HERE = Path(__file__).resolve().parent
#: The six screen metrics metrics_at produces, and screen_fundamentals' pass flags.
COLS = ["PE", "EPS_Growth", "Rev_Growth", "Debt_Equity", "Div_Yield", "FCF"]
PASSES = ["Pass_PE", "Pass_EPS", "Pass_Rev", "Pass_DE", "Pass_Div", "Pass_FCF"]
SNAP_PKL = HERE / "pit_snapshots.pkl"
SNAP_FROM = pd.Timestamp("2015-06-01")   # enough lead-in for 2016 formation dates
TOL = pd.Timedelta(days=10)              # 52/53-week fiscal calendars drift a few days


# --- PIT views ---------------------------------------------------------------

def _pit(df: pd.DataFrame | None, asof: pd.Timestamp, key) -> pd.DataFrame | None:
    """Rows filed on/before ``asof``, one per period: the latest restatement."""
    if df is None or df.empty:
        return None
    d = df[df["filed"] <= asof]
    if d.empty:
        return None
    return d.sort_values("filed").drop_duplicates(key, keep="last")


def _family(facts: dict, tags: list[str], asof, key) -> pd.DataFrame | None:
    """Merge a tag family into one series: per period, the highest-priority tag."""
    parts = []
    for prio, tag in enumerate(tags):
        d = _pit(facts.get(tag), asof, key)
        if d is not None:
            parts.append(d.assign(prio=prio))
    if not parts:
        return None
    d = pd.concat(parts).sort_values("prio").drop_duplicates(key, keep="first")
    if "start" in d:
        d = d.assign(dur=(d["end"] - d["start"]).dt.days)
    return d.reset_index(drop=True)


# --- flow arithmetic (start/end rows: quarters, YTDs, fiscal years) ------------

def _row(d, end, lo, hi, start=None):
    m = (abs(d["end"] - end) <= TOL) & d["dur"].between(lo, hi)
    if start is not None:
        m &= abs(d["start"] - start) <= TOL
    hit = d[m]
    return None if hit.empty else hit.iloc[-1]


def _quarter(d, end) -> float:
    """Three-month value ending ``end``; Q4 is derived as FY - 9M YTD."""
    r = _row(d, end, 80, 100)
    if r is not None:
        return float(r["val"])
    fy = _row(d, end, 350, 380)
    if fy is not None:
        nine = _row(d, end - pd.Timedelta(days=91), 260, 285, start=fy["start"])
        if nine is not None:
            return float(fy["val"] - nine["val"])
    return np.nan


def _ttm(d, end) -> float:
    """Trailing twelve months ending ``end``: FY, or FY + YTD - prior-year YTD."""
    fy = _row(d, end, 350, 380)
    if fy is not None:
        return float(fy["val"])
    ytd = d[(abs(d["end"] - end) <= TOL) & d["dur"].between(80, 285)]
    for _, y in ytd.sort_values("dur", ascending=False).iterrows():
        prev_fy = _row(d, y["start"] - pd.Timedelta(days=1), 350, 380)
        prev_ytd = _row(d, end - pd.Timedelta(days=364), y["dur"] - 10, y["dur"] + 10)
        if prev_fy is not None and prev_ytd is not None:
            return float(prev_fy["val"] + y["val"] - prev_ytd["val"])
    return np.nan


def _latest_end(d) -> pd.Timestamp | None:
    return None if d is None or d.empty else d["end"].max()


def _yoy(d) -> float:
    if d is None:
        return np.nan
    end = _latest_end(d)
    now, prev = _quarter(d, end), _quarter(d, end - pd.Timedelta(days=364))
    return (now - prev) / prev if np.isfinite(now) and np.isfinite(prev) and prev > 0 else np.nan


def _flow_ttm(d, end=None) -> float:
    if d is None:
        return np.nan
    return _ttm(d, _latest_end(d) if end is None else end)


# --- instants ----------------------------------------------------------------

def _instant(facts, tag, asof, end) -> float:
    d = _pit(facts.get(tag), asof, ["end"])
    if d is None:
        return np.nan
    hit = d[abs(d["end"] - end) <= pd.Timedelta(days=3)]
    return np.nan if hit.empty else float(hit.iloc[-1]["val"])


def total_debt(facts, asof, end) -> tuple[float, bool]:
    """Long-term debt incl. current maturities + short-term borrowings at ``end``.

    Returns ``(debt, tagged)`` -- ``tagged`` False means no debt tag at all, which
    is read as zero debt (a debt-free filer has nothing to tag).
    """
    first = lambda fam: next((v for v in (_instant(facts, t, asof, end) for t in TAGS[fam])
                              if np.isfinite(v)), np.nan)
    ltd, nc, cur, stb, dc = (first(f) for f in ("LTD", "LTD_NC", "LTD_C", "STB", "DEBT_C"))
    if not np.isfinite([ltd, nc, cur, stb, dc]).any():
        return 0.0, False
    z = np.nan_to_num
    if np.isfinite(ltd):                     # LongTermDebt already includes current maturities
        return ltd + z(stb), True
    if np.isfinite(dc):                      # DebtCurrent already includes short-term borrowings
        return z(nc) + dc, True
    return z(nc) + z(cur) + z(stb), True


def _shares(facts, asof) -> tuple[float, pd.Timestamp | None]:
    """Cover-page shares outstanding (summed across share classes) and its date."""
    for tag in (*TAGS["SHARES_DEI"], *TAGS["SHARES_GAAP"]):
        df = facts.get(tag)
        if df is None or df.empty:
            continue
        d = df[df["filed"] <= asof]
        if d.empty:
            continue
        last_filed = d["filed"].max()
        d = d[d["filed"] == last_filed]
        end = d["end"].max()
        return float(d[d["end"] == end].drop_duplicates("val")["val"].sum()), end
    # Fallback: latest quarter's diluted weighted-average share count.
    q = _family(facts, TAGS["SHARES_WAVG"], asof, ["start", "end"])
    if q is not None:
        q = q[q["dur"].between(80, 100)]
        if not q.empty:
            r = q.sort_values("end").iloc[-1]
            return float(r["val"]), r["end"]
    return np.nan, None


# --- snapshots ---------------------------------------------------------------

def snapshot(facts: dict, asof: pd.Timestamp) -> dict:
    fam = lambda k: _family(facts, TAGS[k], asof, ["start", "end"])
    ni, rev = fam("NI"), fam("REV")
    ocf, capex, div = fam("OCF"), fam("CAPEX"), fam("DIV")

    eq_d = _family(facts, TAGS["EQ"], asof, ["end"])
    bs_end = _latest_end(eq_d)
    equity = float(eq_d[eq_d["end"] == bs_end].iloc[-1]["val"]) if bs_end is not None else np.nan
    debt, tagged = total_debt(facts, asof, bs_end) if bs_end is not None else (np.nan, False)

    ocf_ttm = _flow_ttm(ocf)
    capex_end = _latest_end(ocf)
    capex_ttm = _flow_ttm(capex, capex_end) if capex_end is not None else np.nan
    div_ttm = _flow_ttm(div) if div is not None else 0.0
    # A dividend tag that stopped being filed means the dividend stopped.
    if div is not None and ni is not None and _latest_end(div) < _latest_end(ni) - pd.Timedelta(days=200):
        div_ttm = 0.0
    shares, shares_end = _shares(facts, asof)
    return {
        "ni_end": _latest_end(ni), "ni_ttm": _flow_ttm(ni),
        "eps_growth": _yoy(ni), "rev_growth": _yoy(rev),
        "equity": equity, "debt": debt, "debt_tagged": tagged,
        "fcf": ocf_ttm - (capex_ttm if np.isfinite(capex_ttm) else 0.0),
        "div_ttm": div_ttm,
        "shares": shares, "shares_end": shares_end,
    }


def _ticker_snapshots(facts: dict) -> pd.DataFrame | None:
    filed = sorted({f for df in facts.values() for f in df["filed"] if f >= SNAP_FROM})
    rows = [{"filed": f, **snapshot(facts, f)} for f in filed]
    return pd.DataFrame(rows).sort_values("filed").reset_index(drop=True) if rows else None


def build_snapshots(facts_all: dict) -> dict[str, pd.DataFrame]:
    """One snapshot frame per ticker. Tickers are independent, so they fan out
    over processes (~8 min serial, ~2 min in parallel)."""
    items = sorted(facts_all.items())
    out = {}
    with ProcessPoolExecutor() as ex:
        frames = ex.map(_ticker_snapshots, (f for _, f in items), chunksize=4)
        for i, ((tk, _), frame) in enumerate(zip(items, frames), 1):
            if frame is not None:
                out[tk] = frame
            if i % 50 == 0:
                print(f"  snapshots {i}/{len(items)}")
    return out


# --- pricing a query set -------------------------------------------------------

def _split_factor_after(splits: pd.Series, when: pd.Timestamp | None) -> float:
    if when is None or splits is None or splits.empty:
        return 1.0
    s = splits[(splits.index > when) & (splits > 0)]
    return float(s.prod()) if not s.empty else 1.0


def metrics_at(queries: pd.DataFrame, snaps: dict, prices: dict) -> pd.DataFrame:
    """Price the latest snapshot filed strictly before each ``(Ticker, date)``.

    Returns ``queries`` plus the six screen columns (PE, EPS_Growth, Rev_Growth,
    Debt_Equity, Div_Yield, FCF) and ``mcap``. Unknown tickers come back NaN.
    """
    out = []
    for tk, q in queries.groupby("Ticker", sort=False):
        q = q.sort_values("date")
        s, px = snaps.get(tk), prices.get(tk)
        if s is None or px is None:
            out.append(q)
            continue
        j = pd.merge_asof(q, s, left_on="date", right_on="filed",
                          allow_exact_matches=False, direction="backward")
        close = px["Close"].reindex(j["date"], method="ffill").to_numpy(float)
        splits = px["Stock Splits"][px["Stock Splits"] > 0]
        # Split-adjusted close x shares as reported x every split since the count's date.
        mult = np.array([_split_factor_after(splits, e if pd.notna(e) else None)
                         for e in j["shares_end"]])
        mcap = close * j["shares"].to_numpy(float) * mult
        ni, eq = j["ni_ttm"].to_numpy(float), j["equity"].to_numpy(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            j["mcap"] = mcap
            j["PE"] = np.where(ni > 0, mcap / ni, np.nan)
            j["Debt_Equity"] = np.where(eq > 0, j["debt"].to_numpy(float) / eq, np.nan)
            j["Div_Yield"] = j["div_ttm"].to_numpy(float) / mcap
        j["EPS_Growth"], j["Rev_Growth"], j["FCF"] = j["eps_growth"], j["rev_growth"], j["fcf"]
        out.append(j)
    return pd.concat(out, ignore_index=True)


def load():
    prices = pickle.loads(PRICES_PKL.read_bytes())
    if SNAP_PKL.exists():
        snaps = pickle.loads(SNAP_PKL.read_bytes())
    else:
        snaps = build_snapshots(pickle.loads(FACTS_PKL.read_bytes()))
        SNAP_PKL.write_bytes(pickle.dumps(snaps))
    return snaps, prices


if __name__ == "__main__":
    snaps, prices = load()
    print(f"snapshots for {len(snaps)} tickers, "
          f"{sum(len(v) for v in snaps.values())} filing dates")
