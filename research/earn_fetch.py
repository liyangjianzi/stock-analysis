"""Fetch every universe name's earnings dates (past and scheduled) from Yahoo.

Scratch data layer for the earnings-blackout study (earn_study.py). One
gitignored pickle:

* ``earn_dates.pkl`` -- {ticker: sorted list of *reaction days*}. A reaction day
  is the first session whose open can price the report: the report's own date
  when it lands before noon New York time (pre-market), else the next business
  day (after the close). This is the day a gap appears.

Yahoo's ``get_earnings_dates(limit=60)`` reaches back ~15 years, which covers
the 10y research cache. The dates are the *actual* report dates. Companies
announce them weeks ahead, so a blackout on them is close to point-in-time,
but not exactly: a date that moved after it was announced is recorded where it
landed. earn_study.py treats that as a small lookahead, not a zero one.

    python earn_fetch.py            # ~2-4 min, network
"""
from __future__ import annotations

import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from stockanalysis import config  # noqa: E402

UNIVERSE = HERE.parent / "data" / "universe_sp500.csv"
EARN_PKL = HERE / "earn_dates.pkl"


def reaction_days(frame: pd.DataFrame) -> list[pd.Timestamp]:
    """Report timestamps -> the session each one gaps (see module docstring)."""
    if frame is None or frame.empty:
        return []
    idx = frame.index
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York")
    days = [ts.normalize().tz_localize(None) if ts.hour < 12
            else (ts.normalize() + pd.offsets.BDay(1)).tz_localize(None)
            for ts in idx]
    return sorted(set(days))


def fetch_one(ticker: str, tries: int = 3):
    for attempt in range(tries):
        try:
            return ticker, reaction_days(yf.Ticker(ticker).get_earnings_dates(limit=60))
        except Exception as e:                        # rate limit / transient
            if attempt == tries - 1:
                print(f"  {ticker}: failed ({e})")
                return ticker, []
            time.sleep(2 * (attempt + 1))


def main() -> None:
    tickers = sorted(config.load_watchlist_csv(UNIVERSE))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = dict(pool.map(fetch_one, tickers))
    covered = {tk: d for tk, d in out.items() if d}
    print(f"{len(covered)}/{len(tickers)} tickers with earnings dates "
          f"({time.time() - t0:.0f}s)")
    firsts = pd.Series({tk: d[0] for tk, d in covered.items()})
    print(f"  earliest reaction day per ticker: median {firsts.median().date()}, "
          f"latest {firsts.max().date()}")
    with open(EARN_PKL, "wb") as f:
        pickle.dump(covered, f)
    print(f"wrote {EARN_PKL.name}")


if __name__ == "__main__":
    main()
