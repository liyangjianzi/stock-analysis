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
      - dividendYield : also a PERCENT (e.g. 0.32 for 0.32%) -> /100

    ``watchlist`` supplies the sector label (falls back to the watchlist CSV).
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist

    pe          = _safe(info, "trailingPE")
    eps_growth  = _safe(info, "earningsGrowth")     # YoY, fractional
    rev_growth  = _safe(info, "revenueGrowth")      # YoY, fractional
    de_raw      = _safe(info, "debtToEquity")       # reported as percent
    div_raw     = _safe(info, "dividendYield")      # reported as percent
    fcf         = _safe(info, "freeCashflow")       # absolute currency amount

    # Normalise debt/equity from percent (85.3) to ratio (0.853)
    de_ratio = de_raw / 100.0 if np.isfinite(de_raw) else np.nan

    # dividendYield is a percent on both sides of 1% (AAPL 0.32, KO 2.41), so it
    # is always divided -- a ">1 means percent" guess read every sub-1% payer as
    # a 32%-style yield that cleared the screen. trailingAnnualDividendYield is
    # not a safe substitute: it divides a home-currency dividend by the US
    # price for ADRs (TSM comes out at 5.8% instead of 0.9%).
    div_yield = div_raw / 100.0 if np.isfinite(div_raw) else np.nan

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


# -----------------------------------------------------------------------------
# Broad-universe research fetching. Bars only, in bulk — deliberately separate
# from load_watchlist(), which also pulls .info per ticker (78% of a pipeline
# run's wall clock, and always *today's* values, so useless for history).
# -----------------------------------------------------------------------------

#: Wikipedia's S&P 500 constituent table. Current members only — a backtest over
#: this list still carries survivorship bias, just far less sector concentration
#: than a hand-picked watchlist. Documented rather than hidden.
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_universe() -> list[dict]:
    """Scrape current S&P 500 constituents as ``[{ticker, company, exchange, sector}]``.

    Tickers are normalised to Yahoo's convention (``BRK.B`` -> ``BRK-B``). Returns
    ``[]`` on any failure, logged — callers fall back to an existing CSV.
    """
    try:
        # Fetch via requests with an explicit UA: Wikipedia 403s pandas'/urllib's
        # default agent, so pd.read_html(url) cannot be used directly here.
        import io
        import requests
        resp = requests.get(SP500_URL, timeout=30, headers={
            "User-Agent": "stockanalysis/0.1 (research; contact via repo)"})
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:
        log.error("S&P 500 universe fetch failed (%s).", e)
        return []
    for t in tables:
        cols = {str(c).strip() for c in t.columns}
        if {"Symbol", "Security"} <= cols:
            sector = "GICS Sector" if "GICS Sector" in cols else "GICS  Sector"
            return [{
                "ticker": str(r["Symbol"]).strip().replace(".", "-"),
                "company": str(r["Security"]).strip(),
                "exchange": "SP500",
                "sector": str(r.get(sector, "Unknown")).strip(),
            } for _, r in t.iterrows() if str(r["Symbol"]).strip()]
    log.error("S&P 500 page had no recognisable constituent table.")
    return []


def _normalise_bars(df):
    """Coerce a yfinance frame to the OHLCV contract with a tz-naive index."""
    if df is None or df.empty or "Close" not in df:
        return None
    out = df[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in df]].copy()
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    return out.dropna(how="all")


def fetch_bulk_prices(tickers, period: str = "10y", chunk: int = 50) -> dict:
    """Download daily bars for many tickers at once: ``{ticker: OHLCV frame}``.

    Uses ``auto_adjust=True`` and a tz-naive index to match
    :func:`fetch_stock_data` exactly — a cache built on a different adjustment
    convention would silently feed the backtest prices the live pipeline never
    sees. A chunk that fails is logged and skipped, so one bad ticker cannot
    abort a 500-name refresh.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    out: dict = {}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        try:
            raw = yf.download(tickers=batch, period=period, interval="1d",
                              auto_adjust=True, group_by="ticker", threads=True,
                              progress=False)
        except Exception as e:
            log.warning("Bulk fetch failed for %d tickers (%s) — skipping chunk.",
                        len(batch), e)
            continue
        if raw is None or raw.empty:
            log.warning("Bulk fetch returned nothing for %d tickers.", len(batch))
            continue
        for tk in batch:
            try:
                df = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            bars = _normalise_bars(df)
            if bars is not None and not bars.empty:
                out[tk] = bars
        log.info("Bulk fetch: %d/%d tickers cached so far.", len(out), len(tickers))
    return out
