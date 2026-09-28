"""Stage-0 daily market overview — data gathering only (no printing/plotting).

Builds the raw data the morning briefing needs: index stats, the VIX risk
gauge, a rates / markets / economy panel, recent headlines, and a discovery scan
of candidate tickers.
:func:`daily_overview` returns a structured dict; rendering it (text or chart)
is the caller's job (see :mod:`stockanalysis.charts` for the index figure).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

from . import config
from .signals import compute_technical_posture

log = logging.getLogger(__name__)


def _fetch_daily(fetch_map: dict) -> dict:
    """~1y of daily OHLCV per ``name -> ticker``; names that fail are skipped."""
    result = {}
    for name, ticker in fetch_map.items():
        try:
            df = yf.download(ticker, period="1y", interval="1d",
                             auto_adjust=True, progress=False)
            if df is None or df.empty:
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            result[name] = df
        except Exception as e:
            log.warning("%s (%s): %s", name, ticker, e)
    return result


def fetch_index_data(indices: dict | None = None, vix_ticker: str | None = None,
                     lookback: int = config.OVERVIEW_LOOKBACK) -> dict:
    """Fetch ~1y of OHLCV for the major indices + VIX.

    Returns dict: name -> {"full": DataFrame, "chart": DataFrame(tail lookback)}.
    """
    indices = config.OVERVIEW_INDICES if indices is None else indices
    vix_ticker = config.VIX_TICKER if vix_ticker is None else vix_ticker
    frames = _fetch_daily({**indices, "VIX": vix_ticker})
    return {name: {"full": df, "chart": df.tail(lookback)} for name, df in frames.items()}


def fetch_macro_data() -> dict:
    """~1y of daily bars for :data:`config.MACRO_YIELDS` + :data:`config.MACRO_MARKETS`."""
    return _fetch_daily({**config.MACRO_YIELDS, **config.MACRO_MARKETS})


def _change(close: pd.Series, bars: int, in_bps: bool) -> float:
    if len(close) <= bars:
        return np.nan
    last, then = close.iloc[-1], close.iloc[-1 - bars]
    return (last - then) * 100 if in_bps else (last / then - 1) * 100


def macro_stats(frames: dict) -> dict:
    """Last value and 1-week / 1-month change per series, plus the curve slope.

    Yields change in **bps** (their levels are already percents, and are passed
    through unscaled); everything else in **%**. ``curve`` is 10Y minus 3M in
    bps with an ``inverted`` flag, or None if either leg is missing.
    """
    rates = []
    for name, df in frames.items():
        close = df["Close"].dropna() if isinstance(df, pd.DataFrame) and "Close" in df else None
        if close is None or close.empty:
            continue
        is_yield = name in config.MACRO_YIELDS
        rates.append({"Name": name, "Last": float(close.iloc[-1]),
                      "1W": _change(close, 5, is_yield), "1M": _change(close, 21, is_yield),
                      "Unit": "bps" if is_yield else "%"})
    last = {r["Name"]: r["Last"] for r in rates}
    curve = None
    if "10Y Treasury" in last and "3M T-Bill" in last:
        slope = (last["10Y Treasury"] - last["3M T-Bill"]) * 100
        curve = {"slope_bps": slope, "inverted": bool(slope < 0)}
    return {"rates": rates, "curve": curve}


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"


def _read_fred_series(series_id: str) -> pd.Series:
    df = pd.read_csv(FRED_CSV_URL.format(series_id), index_col=0, parse_dates=True)
    return pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()


def fetch_fred(series: dict | None = None) -> dict:
    """``name -> Series`` for each FRED series (default :data:`config.FRED_SERIES`),
    via FRED's keyless CSV endpoint. A series that fails is logged and skipped.

    This is the latest vintage — revised data, not what was known at the time —
    so it is context for a human, never an input to a backtest.
    """
    series = config.FRED_SERIES if series is None else series
    out = {}
    for name, series_id in series.items():
        try:
            s = _read_fred_series(series_id)
            if not s.empty:
                out[name] = s
        except Exception as e:
            log.warning("FRED %s (%s): %s", name, series_id, e)
    return out


def _level(s: pd.Series):
    then = s[:s.index[-1] - pd.DateOffset(months=3)]
    change = s.iloc[-1] - then.iloc[-1] if not then.empty else np.nan
    return s.iloc[-1], change, "3m chg", "%"


def _yoy(s: pd.Series):
    if len(s) < 14:
        return None
    now = (s.iloc[-1] / s.iloc[-13] - 1) * 100
    prior = (s.iloc[-2] / s.iloc[-14] - 1) * 100
    return now, now - prior, "vs prior month", "% y/y"


def _monthly_change(s: pd.Series):
    if len(s) < 3:
        return None
    now, prior = s.iloc[-1] - s.iloc[-2], s.iloc[-2] - s.iloc[-3]
    return now, now - prior, "vs prior month", "k"


#: How each FRED series is read: a level (rates), year-over-year (a price
#: index), or the monthly change (a stock, like payroll employment).
_FRED_TRANSFORMS = {"Fed Funds": _level, "CPI": _yoy,
                    "Unemployment": _level, "Payrolls": _monthly_change}


def fred_summary(series: dict) -> list[dict]:
    """One row per FRED series: ``Indicator``, ``Value``, ``Unit``, ``Change``
    (with its ``Change Label``) and ``As of`` — the latest observation's date,
    so a monthly release's lag is visible. Series too short to transform are
    skipped."""
    rows = []
    for name, s in series.items():
        transform = _FRED_TRANSFORMS.get(name, _level)
        result = transform(s) if len(s) else None
        if result is None:
            continue
        value, change, label, unit = result
        rows.append({"Indicator": name, "Value": float(value), "Unit": unit,
                     "Change": float(change), "Change Label": label,
                     "As of": s.index[-1].date().isoformat()})
    return rows


def index_stats(df) -> dict:
    """Compute day/week/YTD % change, RSI, and EMA50 trend for an index DataFrame."""
    close = df["Close"].dropna()
    if len(close) < 6:
        return {}
    day_chg = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
    week_chg = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
    year = datetime.now().year
    ytd_ref = close.asof(pd.Timestamp(year, 1, 1))
    ytd_chg = ((close.iloc[-1] - ytd_ref) / ytd_ref * 100
               if pd.notna(ytd_ref) and ytd_ref > 0 else np.nan)
    rsi_val = np.nan
    if len(close) >= 14:
        rsi_val = RSIIndicator(close, window=14).rsi().iloc[-1]
    ema50 = np.nan
    if len(close) >= 50:
        ema50 = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
    if pd.notna(ema50):
        trend = "Above EMA50" if close.iloc[-1] > ema50 else "Below EMA50"
    else:
        trend = "N/A"
    return {
        "Last": close.iloc[-1],
        "Day %": day_chg,
        "Week %": week_chg,
        "YTD %": ytd_chg,
        "RSI": rsi_val,
        "Trend": trend,
    }


def scan_candidates(universe: dict, watchlist: dict | None = None,
                    news_hours: int = 48, earnings_days: int = 14,
                    top_n: int = 5) -> pd.DataFrame:
    """Score a candidate universe on news mentions + earnings proximity.

    Tickers already in ``watchlist`` are skipped. Returns the top N rows sorted
    by Discovery_Score descending.
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist
    cutoff_ts = (datetime.now(timezone.utc) - pd.Timedelta(hours=news_hours)).timestamp()
    rows = []
    for ticker, sector in universe.items():
        if ticker in watchlist:
            continue
        try:
            tk = yf.Ticker(ticker)
            news = tk.news or []
            recent = sum(
                1 for n in news
                if n.get("providerPublishTime", 0) >= cutoff_ts
            )
            days_to_earn = np.nan
            try:
                cal = tk.calendar
                if cal is not None:
                    if isinstance(cal, pd.DataFrame) and not cal.empty:
                        if "Earnings Date" in cal.index:
                            earn_date = pd.to_datetime(cal.loc["Earnings Date"].iloc[0]).tz_localize(None)
                            days_to_earn = max(0, (earn_date - pd.Timestamp.now()).days)
                    elif isinstance(cal, dict):
                        key = next((k for k in cal if "Earnings" in str(k)), None)
                        if key and cal[key]:
                            earn_date = pd.to_datetime(cal[key][0]).tz_localize(None)
                            days_to_earn = max(0, (earn_date - pd.Timestamp.now()).days)
            except Exception:
                pass
            rows.append({
                "Ticker": ticker, "Sector": sector,
                "News_Count": recent, "Days_To_Earnings": days_to_earn,
            })
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["Ticker", "Sector", "News_Count", "Days_To_Earnings", "Discovery_Score"])
    df = pd.DataFrame(rows)
    df["News_Score"] = df["News_Count"].apply(lambda x: min(x / 5.0, 1.0))

    def _earn_score(d):
        if pd.isna(d) or d < 0:
            return 0.0
        return 1.0 if d < 7 else (0.5 if d <= earnings_days else 0.0)

    df["Earnings_Score"] = df["Days_To_Earnings"].apply(_earn_score)
    df["Discovery_Score"] = 0.60 * df["News_Score"] + 0.40 * df["Earnings_Score"]
    df = df.drop(columns=["News_Score", "Earnings_Score"])
    return df.sort_values("Discovery_Score", ascending=False).reset_index(drop=True).head(top_n)


def recent_headlines(tickers, hours: int = 48, limit: int = 10) -> list:
    """Return de-duplicated recent headlines across ``tickers``.

    Each item is a dict: {published, title, publisher, url}.
    """
    cutoff_ts = (datetime.now(timezone.utc) - pd.Timedelta(hours=hours)).timestamp()
    seen_urls, headlines = set(), []
    for ticker in tickers:
        try:
            for item in (yf.Ticker(ticker).news or []):
                url = item.get("link", "")
                ts = item.get("providerPublishTime", 0)
                if url and url not in seen_urls and ts >= cutoff_ts:
                    seen_urls.add(url)
                    pub = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                    headlines.append({
                        "ts": ts, "published": pub,
                        "title": item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "url": url,
                    })
        except Exception:
            continue
    headlines.sort(key=lambda h: h["ts"], reverse=True)
    return headlines[:limit]


def _vix_label(vix_now: float) -> str:
    return ("LOW" if vix_now < 15 else
            "MODERATE" if vix_now < 25 else
            "ELEVATED" if vix_now < 35 else "HIGH")


def daily_overview(watchlist: dict | None = None, signal_matrix=None,
                   tech: dict | None = None, top_n: int = 5) -> dict:
    """Gather the daily market overview as structured data (no side effects).

    Returns a dict with keys: ``index_data`` (raw frames for charting),
    ``indices`` (per-index stats list), ``vix`` ({value, label} or None),
    ``macro`` (``rates``/``curve`` from :func:`macro_stats` + ``economy`` from
    :func:`fred_summary`), ``headlines`` (list), ``candidates`` (DataFrame), and ``action_plan``
    (breakdown + top buys / discoveries). Pass ``signal_matrix``/``tech`` from a
    completed pipeline run for an enriched action plan.
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist

    index_data = fetch_index_data()
    candidates_df = scan_candidates(config.CANDIDATE_UNIVERSE, watchlist=watchlist, top_n=top_n)

    # Per-index stats.
    indices = []
    for name, df in index_data.items():
        if name == "VIX":
            continue
        s = index_stats(df["full"] if isinstance(df, dict) else df)
        if s:
            s["Index"] = name
            indices.append(s)

    # VIX risk gauge.
    vix = None
    vix_entry = index_data.get("VIX", {})
    vix_df = vix_entry.get("chart") if isinstance(vix_entry, dict) else vix_entry
    if vix_df is not None and not vix_df.empty:
        vix_now = float(vix_df["Close"].dropna().iloc[-1])
        vix = {"value": vix_now, "label": _vix_label(vix_now)}

    macro = {**macro_stats(fetch_macro_data()), "economy": fred_summary(fetch_fred())}

    # Headlines across indices + top discovered tickers.
    news_tickers = list(config.OVERVIEW_INDICES.values())
    if not candidates_df.empty:
        news_tickers += candidates_df["Ticker"].head(5).tolist()
    headlines = recent_headlines(news_tickers)

    # Action plan.
    action_plan: dict = {}
    if signal_matrix is not None and not signal_matrix.empty:
        sm = signal_matrix.reset_index() if "Ticker" not in signal_matrix.columns else signal_matrix
        action_plan["breakdown"] = dict(sm["Final Action Signal"].value_counts())
        top_buys = (sm[sm["Final Action Signal"] == "Buy"]
                    .nlargest(3, "Composite")[["Ticker", "Composite"]])
        action_plan["top_buys"] = top_buys.to_dict("records")
    elif not candidates_df.empty:
        discoveries = []
        for _, row in candidates_df.head(3).iterrows():
            posture = None
            if tech is not None and row["Ticker"] in tech:
                try:
                    posture, _, _ = compute_technical_posture(tech[row["Ticker"]])
                except Exception:
                    posture = None
            discoveries.append({
                "Ticker": row["Ticker"], "Sector": row["Sector"],
                "Discovery_Score": row["Discovery_Score"], "Posture": posture,
            })
        action_plan["discoveries"] = discoveries

    return {
        "index_data": index_data,
        "indices": indices,
        "vix": vix,
        "macro": macro,
        "headlines": headlines,
        "candidates": candidates_df,
        "action_plan": action_plan,
    }
