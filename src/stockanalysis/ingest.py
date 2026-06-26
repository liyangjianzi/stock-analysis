"""Data ingestion from Yahoo Finance (via ``yfinance``).

Every fetch degrades gracefully: missing data yields ``None``/``np.nan`` and a
logged warning rather than an exception, so a single bad ticker never aborts a
run. ``load_watchlist`` is the batch driver the pipeline calls.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yfinance as yf

from . import config

log = logging.getLogger(__name__)


def fetch_stock_data(ticker: str, period: str = config.HISTORY_PERIOD):
    """Fetch daily OHLCV history + the fundamentals ``.info`` dict for one ticker.

    Returns ``(history_df, info_dict)``. On any failure or empty data, returns
    ``(None, None)`` and logs a warning so the caller can skip gracefully.
    """
    try:
        tk = yf.Ticker(ticker)
        # auto_adjust=True gives split/dividend-adjusted OHLC (cleaner for TA)
        hist = tk.history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            log.warning("%s: no price history returned — skipping.", ticker)
            return None, None
        # Normalise index to tz-naive dates for consistent plotting/joins
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        # .info can be flaky; tolerate failure and fall back to empty dict
        try:
            info = tk.info or {}
        except Exception as e:
            log.warning("%s: .info unavailable (%s). Proceeding with prices only.", ticker, e)
            info = {}
        return hist, info
    except Exception as e:
        log.error("%s: fetch failed (%s) — skipping.", ticker, e)
        return None, None


def _safe(info: dict, key: str):
    """Return a numeric value from ``info`` or ``np.nan`` if missing/non-numeric."""
    val = info.get(key, None) if isinstance(info, dict) else None
    try:
        if val is None:
            return np.nan
        val = float(val)
        return val if np.isfinite(val) else np.nan
    except (TypeError, ValueError):
        return np.nan


def fetch_fundamentals(ticker: str, info: dict, watchlist: dict | None = None) -> dict:
    """Extract a normalised set of foundational metrics from a yfinance info dict.

    All values are floats or NaN (never raises). Units are normalised to
    fractions where relevant so thresholds compare cleanly:
      - earningsGrowth / revenueGrowth : already fractional (0.10 == 10%)
      - debtToEquity : yfinance reports as a PERCENT (e.g. 85.3) -> /100
      - dividendYield : usually fractional; defensively normalise >1 values

    ``watchlist`` supplies the sector label (falls back to the watchlist CSV).
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist

    pe          = _safe(info, "trailingPE")
    eps_growth  = _safe(info, "earningsGrowth")     # YoY, fractional
    rev_growth  = _safe(info, "revenueGrowth")      # YoY, fractional
    de_raw      = _safe(info, "debtToEquity")       # reported as percent
    div_yield   = _safe(info, "dividendYield")
    fcf         = _safe(info, "freeCashflow")       # absolute currency amount

    # Normalise debt/equity from percent (85.3) to ratio (0.853)
    de_ratio = de_raw / 100.0 if np.isfinite(de_raw) else np.nan

    # Some yfinance versions return dividendYield as a percent (e.g. 1.6 for 1.6%).
    # If the value looks like a percent (>1), convert to a fraction.
    if np.isfinite(div_yield) and div_yield > 1:
        div_yield = div_yield / 100.0

    return {
        "Ticker": ticker,
        "Sector": watchlist.get(ticker, info.get("sector", "Unknown")),
        "Price": _safe(info, "currentPrice"),
        "PE": pe,
        "EPS_Growth": eps_growth,
        "Rev_Growth": rev_growth,
        "Debt_Equity": de_ratio,
        "Div_Yield": div_yield,
        "FCF": fcf,
    }


def fetch_profile(ticker: str) -> dict:
    """Fetch extended yfinance fields for a deep fundamental profile.

    Returns a flat dict. Numeric values are floats or np.nan; strings are str
    or '' on missing. Reuses :func:`_safe`. Never raises.
    """
    _EMPTY = {
        "shortName": "", "longBusinessSummary": "", "sector": "",
        "industry": "", "country": "", "numberOfEmployees": np.nan,
        "grossMargins": np.nan, "operatingMargins": np.nan,
        "profitMargins": np.nan, "returnOnAssets": np.nan,
        "returnOnEquity": np.nan, "earningsGrowth": np.nan,
        "revenueGrowth": np.nan, "currentRatio": np.nan,
        "quickRatio": np.nan, "totalCash": np.nan,
        "priceToBook": np.nan, "priceToSalesTrailing12Months": np.nan,
        "pegRatio": np.nan, "enterpriseToEbitda": np.nan,
        "heldPercentInsiders": np.nan, "heldPercentInstitutions": np.nan,
        "shortPercentOfFloat": np.nan,
    }
    try:
        info = yf.Ticker(ticker).info or {}
        result = dict(_EMPTY)
        # String fields
        for key in ("longBusinessSummary", "sector", "industry", "country"):
            val = info.get(key, "")
            result[key] = str(val) if val else ""
        result["shortName"] = (
            info.get("shortName") or info.get("longName") or ticker
        ).upper()
        # Numeric fields — all fractional in yfinance, no normalization needed
        for key in (
            "numberOfEmployees", "grossMargins", "operatingMargins",
            "profitMargins", "returnOnAssets", "returnOnEquity",
            "earningsGrowth", "revenueGrowth", "currentRatio", "quickRatio",
            "totalCash", "priceToBook", "priceToSalesTrailing12Months",
            "pegRatio", "enterpriseToEbitda", "heldPercentInsiders",
            "heldPercentInstitutions", "shortPercentOfFloat",
        ):
            result[key] = _safe(info, key)
        return result
    except Exception:
        return dict(_EMPTY)


def load_watchlist(watchlist: dict | None = None, period: str = config.HISTORY_PERIOD):
    """Fetch prices + fundamentals for every ticker in ``watchlist``.

    Returns ``(prices, fundamentals_df)`` where:
      - ``prices`` : dict ticker -> OHLCV DataFrame (only successful fetches)
      - ``fundamentals_df`` : DataFrame of normalised metrics, indexed by ticker
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist

    prices: dict[str, pd.DataFrame] = {}
    records: list[dict] = []

    log.info("Fetching data for %d tickers (hits Yahoo Finance once per ticker)...", len(watchlist))
    for tk in watchlist:
        hist, info = fetch_stock_data(tk, period=period)
        if hist is None:
            continue  # already logged inside fetch_stock_data
        prices[tk] = hist
        records.append(fetch_fundamentals(tk, info, watchlist=watchlist))
        log.info("  %-8s %4d rows (%s -> %s)", tk, len(hist),
                 hist.index.min().date(), hist.index.max().date())

    fundamentals_df = pd.DataFrame(records)
    if not fundamentals_df.empty:
        fundamentals_df = fundamentals_df.set_index("Ticker")

    log.info("Ingestion complete: %d/%d tickers with price history.", len(prices), len(watchlist))
    return prices, fundamentals_df
