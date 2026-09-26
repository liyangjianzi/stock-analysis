"""Fetch point-in-time S&P 500 membership + total-return prices for every name.

Scratch data layer for the 12-1 momentum study (mom_study.py). Two gitignored
pickles:

* ``mom_membership.pkl`` -- {month_end: set(tickers)} from fja05680/sp500, a
  public reconstruction of daily S&P 500 membership since 1996 (tickers as of
  the date, e.g. ANTM before it became ELV).
* ``mom_prices.pkl``     -- daily auto-adjusted (dividends + splits, i.e. total
  return) closes from yfinance for every ticker that was a member at any month
  end since START, plus RSP -- the equal-weight S&P 500 ETF, which holds the real
  index including the names Yahoo has since lost, and so measures how far the
  rebuilt universe drifts from it.

Yahoo keeps only ~26% of the names that left the index since 2006 (bankrupt,
acquired and renamed tickers are mostly gone); mom_study reports the missing
share month by month rather than pretending the universe is complete.

    python mom_fetch.py            # ~3-5 min, network
"""
from __future__ import annotations

import io
import pickle
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
MEMBERS_PKL = HERE / "mom_membership.pkl"
PRICES_PKL = HERE / "mom_prices.pkl"
MEMBERS_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
               "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")
START = pd.Timestamp("2005-01-01")       # 12 months of lead-in before 2006
BENCHMARK_ETF = "RSP"


def yahoo(ticker: str) -> str:
    return ticker.replace(".", "-")


def fetch_membership() -> dict[pd.Timestamp, frozenset]:
    with urllib.request.urlopen(MEMBERS_URL, timeout=60) as r:
        raw = pd.read_csv(io.BytesIO(r.read()), parse_dates=["date"])
    raw = raw.sort_values("date").set_index("date")["tickers"]
    months = pd.date_range(START, raw.index.max(), freq="ME")
    out = {}
    for m in months:
        snap = raw[raw.index <= m]
        if not snap.empty:
            out[m] = frozenset(t.strip() for t in snap.iloc[-1].split(","))
    MEMBERS_PKL.write_bytes(pickle.dumps(out))
    return out


def fetch_prices(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    ys = sorted({yahoo(t) for t in tickers} | {BENCHMARK_ETF})
    raw = yf.download(ys, start=START - pd.DateOffset(months=13), auto_adjust=True,
                      progress=False, threads=True, group_by="ticker")
    closes = {}
    for y in ys:
        try:
            c = raw[y]["Close"].dropna()
        except KeyError:
            continue
        if not c.empty:
            closes[y] = c
    px = pd.DataFrame(closes)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    PRICES_PKL.write_bytes(pickle.dumps(px))
    return px


if __name__ == "__main__":
    members = fetch_membership()
    ever = sorted(set().union(*members.values()))
    print(f"membership: {len(members)} month-ends "
          f"{min(members).date()}..{max(members).date()}, {len(ever)} distinct tickers")
    px = fetch_prices(ever)
    print(f"prices: {px.shape[1]} of {len(ever) + 1} tickers returned data "
          f"({px.index.min().date()}..{px.index.max().date()})")
