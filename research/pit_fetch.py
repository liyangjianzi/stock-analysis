"""Fetch point-in-time fundamentals (SEC XBRL companyfacts) + unadjusted prices.

Scratch data layer for the fundamental-screen study (see pit_study.py). Two
artifacts, both gitignored pickles:

* ``pit_facts.pkl``  -- {ticker: {tag: DataFrame(start, end, val, filed, form)}}
  for the handful of us-gaap/dei tags the six screen metrics need. Every row is
  one *filing's* report of one period, stamped with the date it was filed, so a
  metric can be rebuilt exactly as it was knowable on any past date.
* ``pit_prices.pkl`` -- yfinance ``Close`` (split-adjusted, NOT dividend-adjusted)
  and ``Stock Splits`` per ticker. Market cap needs the price as it traded, which
  the research cache (auto_adjust=True) cannot give.

SEC's fair-access policy wants a contact in the User-Agent and <=10 req/s. The
contact is read from the environment so it never lands in the repo:

    SEC_USER_AGENT="StockAnalysis research you@example.com" python pit_fetch.py
"""
from __future__ import annotations

import gzip
import json
import os
import pickle
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from stockanalysis import config  # noqa: E402

UA = os.environ.get("SEC_USER_AGENT", "")
UNIVERSE = HERE.parent / "data" / "universe_sp500.csv"
FACTS_PKL = HERE / "pit_facts.pkl"
PRICES_PKL = HERE / "pit_prices.pkl"
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}

# Tag families, highest priority first. pit_fundamentals merges each family into
# one series per period, so a company that switched tags (e.g. SalesRevenueNet ->
# RevenueFromContract... under ASC 606) still has a continuous history.
TAGS = {
    "NI": ["NetIncomeLoss", "ProfitLoss",
           "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "REV": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
            "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating"],
    "OCF": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "CAPEX": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements"],
    "DIV": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
            "PaymentsOfOrdinaryDividends"],
    "EQ": ["StockholdersEquity",
           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    # Debt pieces are summed, not prioritized -- see pit_fundamentals.total_debt.
    "LTD": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "LTD_NC": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent"],
    "LTD_C": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "STD": ["ShortTermBorrowings", "CommercialPaper", "DebtCurrent"],
    "SHARES_DEI": ["EntityCommonStockSharesOutstanding"],
    "SHARES_GAAP": ["CommonStockSharesOutstanding"],
    # Multi-class filers (META, ABNB, LEN, ...) tag the cover-page count per class,
    # which companyfacts drops; the income statement's diluted average survives.
    "SHARES_WAVG": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}


class _RateLimit:
    def __init__(self, per_sec: float):
        self.gap, self.lock, self.next = 1.0 / per_sec, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


_limit = _RateLimit(8)


def _get(url: str) -> bytes:
    for attempt in range(4):
        _limit.wait()
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return gzip.decompress(body) if r.headers.get("Content-Encoding") == "gzip" else body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {url}")


def cik_map() -> dict[str, int]:
    data = json.loads(_get("https://www.sec.gov/files/company_tickers.json"))
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def _extract(facts: dict) -> dict[str, pd.DataFrame]:
    out = {}
    pools = {**facts.get("facts", {}).get("us-gaap", {}),
             **facts.get("facts", {}).get("dei", {})}
    for tags in TAGS.values():
        for tag in tags:
            node = pools.get(tag)
            if not node:
                continue
            for unit, rows in node["units"].items():
                if unit not in ("USD", "shares"):
                    continue
                df = pd.DataFrame(rows)
                df = df[df["form"].isin(FORMS)] if "form" in df else df
                if df.empty:
                    continue
                cols = [c for c in ("start", "end", "val", "filed", "form", "fp", "accn")
                        if c in df]
                df = df[cols].copy()
                for c in ("start", "end", "filed"):
                    if c in df:
                        df[c] = pd.to_datetime(df[c])
                out[tag] = df.reset_index(drop=True)
    return out


def fetch_facts(tickers: list[str]) -> dict:
    ciks = cik_map()
    done = pickle.loads(FACTS_PKL.read_bytes()) if FACTS_PKL.exists() else {}
    todo = [t for t in tickers if t not in done]
    missing = [t for t in todo if t.replace(".", "-").upper() not in ciks]
    todo = [t for t in todo if t not in missing]
    print(f"companyfacts: {len(done)} cached, {len(todo)} to fetch, "
          f"{len(missing)} with no CIK: {missing}")

    def one(tk):
        cik = ciks[tk.replace(".", "-").upper()]
        try:
            raw = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            return tk, _extract(json.loads(raw))
        except Exception as e:                       # noqa: BLE001 -- report, don't die
            print(f"  {tk}: {e}")
            return tk, None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (tk, facts) in enumerate(ex.map(one, todo), 1):
            if facts is not None:
                done[tk] = facts
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}  {time.time() - t0:.0f}s")
                FACTS_PKL.write_bytes(pickle.dumps(done))
    FACTS_PKL.write_bytes(pickle.dumps(done))
    return done


def fetch_prices(tickers: list[str]) -> dict:
    import yfinance as yf
    raw = yf.download(tickers, start="2014-01-01", auto_adjust=False, actions=True,
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for tk in tickers:
        try:
            df = raw[tk][["Close", "Stock Splits"]].dropna(subset=["Close"])
        except KeyError:
            continue
        if not df.empty:
            df.index = pd.to_datetime(df.index).tz_localize(None)
            out[tk] = df
    PRICES_PKL.write_bytes(pickle.dumps(out))
    return out


if __name__ == "__main__":
    if "@" not in UA:
        sys.exit('set SEC_USER_AGENT="<app name> <contact email>" -- SEC refuses '
                 'anonymous clients (company_tickers.json returns 403)')
    tickers = list(config.load_watchlist_csv(UNIVERSE))
    facts = fetch_facts(tickers)
    print(f"facts for {len(facts)}/{len(tickers)} tickers")
    prices = fetch_prices(tickers)
    print(f"prices for {len(prices)}/{len(tickers)} tickers")
