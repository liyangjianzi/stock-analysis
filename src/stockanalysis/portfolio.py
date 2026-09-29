"""Household portfolio risk from real holdings — **display only**.

Everything here takes already-fetched data (the :mod:`stockanalysis.holdings`
frame, price histories, a History series) and returns plain dicts / frames, so
it runs offline in tests and headless in a job. Nothing feeds
:func:`stockanalysis.signals.decide_action`: this measures what is *held*, not
what to trade.

Conventions:

* **One currency.** Every weight and amount is in CAD, cash included; USD
  prices are converted with a USD/CAD series before returns are taken, so
  volatility, VaR and stress losses carry the currency risk too.
* **Current weights, historical prices.** Market risk and stress tests replay
  *today's* portfolio through past prices. That answers "what would this
  portfolio have done", not "what did the account do" — :func:`account_drawdown`
  answers that one, from the sheet's own History.
* **Gaps are reported, not filled silently.** A holding without enough price
  history is listed (``short_history``, ``missing``) rather than dropped.

Following the package rule, thin data degrades to NaN / empty results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .account import curve_stats
from .holdings import listing_currency
from .indicators import value_at
from .tradeplan import build_trade_plan

TRADING_DAYS = 252


def classify(holdings: pd.DataFrame, info: dict) -> pd.DataFrame:
    """A copy of ``holdings`` with ``bucket``: ``kind``, except that a
    cash-equivalent fund (``info[yahoo]["cash_equivalent"]``, e.g. a money-market
    ETF) is ``"cash"``. ``kind`` itself is left alone — the fund still isn't
    *spendable* cash until it's sold, which :func:`funding` needs to know."""
    h = holdings.copy()
    cash_like = h["yahoo"].map(lambda y: bool((info.get(y) or {}).get("cash_equivalent"))
                               if isinstance(y, str) else False)
    h["bucket"] = np.where((h["kind"] == "stock") & cash_like.astype(bool), "cash", h["kind"])
    return h


def combined(h: pd.DataFrame, info: dict) -> pd.DataFrame:
    """One row per holding across accounts, from a :func:`classify` frame:
    stocks by Yahoo symbol, cash by currency, unpriced holdings by ticker.
    Columns: name, label, kind, sector, currency, shares, value_cad, accounts."""
    cols = ["name", "label", "kind", "sector", "currency", "shares", "value_cad", "accounts"]
    if h.empty:
        return pd.DataFrame(columns=cols)
    name = np.select([h["bucket"] == "stock", h["bucket"] == "cash"],
                     [h["yahoo"], "Cash (" + h["currency"] + ")"], h["ticker"])
    g = (h.assign(name=name).groupby(["name", "bucket"], as_index=False)
         .agg(value_cad=("value_cad", "sum"), shares=("shares", "sum"),
              currency=("currency", "first"), accounts=("account", "nunique"))
         .rename(columns={"bucket": "kind"}))
    stock = g["kind"] == "stock"
    g.loc[~stock, "shares"] = np.nan
    g["label"] = [(info.get(n) or {}).get("name") or n if k else n for n, k in zip(g["name"], stock)]
    g["sector"] = np.select([stock, g["kind"] == "cash"],
                            [g["name"].map(lambda n: (info.get(n) or {}).get("sector") or "Unknown"),
                             "Cash"], "Other (unpriced)")
    return g[cols]


def _weights(frame: pd.DataFrame, by: str, total: float) -> pd.DataFrame:
    out = frame.groupby(by, as_index=False)["value_cad"].sum()
    out["weight"] = out["value_cad"] / total if total else np.nan
    return out.sort_values("value_cad", ascending=False, ignore_index=True)


def exposure(holdings: pd.DataFrame, info: dict, *,
             max_position: float = config.MAX_POSITION_WEIGHT,
             max_sector: float = config.MAX_SECTOR_WEIGHT) -> dict:
    """Where the money is: by holding (across accounts), account, sector and
    currency, plus cash share, top-5 concentration and limit ``flags``.

    ``info`` maps a Yahoo symbol to ``{"sector", "name", "cash_equivalent"}``.
    """
    h = classify(holdings, info)
    rows = combined(h, info)
    total = float(rows["value_cad"].sum())
    rows["weight"] = rows["value_cad"] / total if total else np.nan
    rows = rows.sort_values("value_cad", ascending=False, ignore_index=True)

    accounts = _weights(h, "account", total)
    cash_by_account = h[h["bucket"] == "cash"].groupby("account")["value_cad"].sum()
    accounts["cash_cad"] = accounts["account"].map(cash_by_account).fillna(0.0)

    sectors = _weights(rows, "sector", total)
    held = rows[rows["kind"] != "cash"]
    flags = [f"{r.name} is {r.weight:.1%} of the portfolio (limit {max_position:.0%})"
             for r in held.itertuples() if r.weight > max_position]
    flags += [f"{r.sector} is {r.weight:.1%} of the portfolio (sector limit {max_sector:.0%})"
              for r in sectors.itertuples() if r.sector != "Cash" and r.weight > max_sector]
    return {
        "total_cad": total,
        "holdings": rows,
        "accounts": accounts,
        "sectors": sectors,
        "currencies": _weights(h, "currency", total),
        "cash_weight": float(rows.loc[rows["kind"] == "cash", "weight"].sum()),
        "top5_weight": float(held["weight"].nlargest(5).sum()),
        "flags": flags,
    }


def market_risk(weights: pd.Series, rets: pd.DataFrame, bench: pd.DataFrame, *,
                total_cad: float, corr_names=None, lookback: int = config.RISK_LOOKBACK_BARS,
                min_coverage: float = 0.9) -> dict:
    """Volatility, beta, historical VaR / CVaR and each holding's share of risk.

    ``weights`` are fractions of the whole household — CAD cash is simply
    absent (flat), so it dampens every figure the way it does in the account;
    ``rets`` and ``bench`` are daily returns in CAD. ``avg_corr`` averages over
    ``corr_names`` (default: every weighted name), so a caller can leave an FX
    line out of "correlation between holdings". Over the last ``lookback`` days, a holding missing
    more than ``1 - min_coverage`` of them is listed in ``short_history`` and its
    missing days count as flat. VaR / CVaR are positive loss fractions at 95%:
    1-day, and 21-day from overlapping compounded windows.
    """
    names = [n for n in weights.index if n in rets.columns]
    w = weights[names].astype(float)
    win = rets[names].tail(lookback)
    nan = float("nan")
    out = {"vol_ann": nan, "var_1d": nan, "cvar_1d": nan, "var_21d": nan, "cvar_21d": nan,
           "beta": {}, "avg_corr": nan, "short_history": [], "n_days": 0,
           "contributions": pd.DataFrame(columns=["name", "weight", "risk_share"])}
    if win.empty or not names:
        return out
    coverage = win.notna().mean()
    port = win.fillna(0.0) @ w
    q = port.quantile(0.05)
    r21 = np.expm1(np.log1p(port).rolling(21).sum()).dropna()
    q21 = r21.quantile(0.05) if not r21.empty else nan
    out.update(
        vol_ann=float(port.std() * np.sqrt(TRADING_DAYS)),
        var_1d=float(-q), cvar_1d=float(-port[port <= q].mean()),
        var_21d=float(-q21), cvar_21d=float(-r21[r21 <= q21].mean()) if not r21.empty else nan,
        short_history=[n for n in names if coverage[n] < min_coverage],
        n_days=int(len(port)), start=port.index[0], end=port.index[-1],
    )
    for key in ("var_1d", "cvar_1d", "var_21d", "cvar_21d"):
        out[f"{key}_cad"] = out[key] * total_cad
    for col in bench.columns:
        pair = pd.concat([port, bench[col]], axis=1, join="inner").dropna()
        if len(pair) > 20 and pair.iloc[:, 1].var() > 0:
            out["beta"][col] = float(pair.cov().iloc[0, 1] / pair.iloc[:, 1].var())
    held = [n for n in (names if corr_names is None else corr_names) if n in names]
    if len(held) > 1:
        corr = win[held].corr().to_numpy()
        pairs = corr[np.triu_indices_from(corr, k=1)]
        pairs = pairs[np.isfinite(pairs)]
        out["avg_corr"] = float(pairs.mean()) if pairs.size else nan
    cov = win.cov().fillna(0.0)
    port_var = float(w @ cov @ w)
    if port_var > 0:
        share = w * (cov @ w) / port_var
        out["contributions"] = (pd.DataFrame({"name": names, "weight": w.to_numpy(),
                                              "risk_share": share.to_numpy()})
                                .sort_values("risk_share", ascending=False, ignore_index=True))
    return out


def _window_return(prices: pd.DataFrame, col: str, start, end):
    """Close-to-close return over [start, end], or None when the series doesn't
    reach back to ``start`` (a holding that didn't exist yet)."""
    if col not in prices:
        return None
    s = prices[col].dropna()
    if s.empty or s.index[0] > start:
        return None
    p0, p1 = s.asof(start), s.asof(end)
    return float(p1 / p0 - 1) if np.isfinite(p0) and np.isfinite(p1) and p0 > 0 else None


def stress(weights: pd.Series, prices: pd.DataFrame, windows: dict, *, total_cad: float,
           proxies: dict | None = None, bench_prices: pd.DataFrame | None = None) -> list[dict]:
    """Today's weights replayed through each ``(start, end)`` window of CAD closes.

    A holding without prices at ``start`` uses its ``proxies`` entry (e.g. a
    bitcoin fund -> bitcoin) when there is one, and is otherwise listed in
    ``missing`` and counted as flat — ``covered_weight`` says how much of the
    portfolio the scenario actually measured.
    """
    proxies = proxies or {}
    results = []
    for label, (start, end) in windows.items():
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        ret = covered = 0.0
        proxied, missing = {}, []
        for name, w in weights.items():
            r = _window_return(prices, name, start, end)
            if r is None and name in proxies:
                r = _window_return(prices, proxies[name], start, end)
                if r is not None:
                    proxied[name] = proxies[name]
            if r is None:
                missing.append(name)
                continue
            ret += w * r
            covered += w
        bench = {}
        if bench_prices is not None:
            for col in bench_prices.columns:
                r = _window_return(bench_prices, col, start, end)
                if r is not None:
                    bench[col] = r
        results.append({"scenario": label, "start": start, "end": end, "return": ret,
                        "loss_cad": ret * total_cad, "covered_weight": covered,
                        "proxied": proxied, "missing": missing, "bench": bench})
    return results


def account_drawdown(history: pd.Series, contributions: pd.Series | None = None) -> dict:
    """Drawdown of the real account from its daily History, with new money removed.

    A contribution lifts the balance without any gain, so the raw balance hides
    losses. Each day's return is ``(V_t - C_t) / V_{t-1} - 1``, where ``C_t`` is
    the contributions dated after the previous History day, up to this one (a
    time-weighted return); drawdowns come from that index. Contributions before
    the first History day are already in the starting balance and ignored. The
    raw-balance figures ride along for comparison. Fewer than two History days
    give ``{}`` — nothing to measure.
    """
    v = history.dropna().sort_index()
    if len(v) < 2:
        return {}
    c = (contributions if contributions is not None else pd.Series(dtype=float)).copy()
    c.index = pd.DatetimeIndex(c.index)           # an empty Series has a plain index
    c = c.sort_index()
    c = c[(c.index > v.index[0]) & (c.index <= v.index[-1])]
    landing = v.index[v.index.searchsorted(c.index, side="left")]
    flows = pd.Series(c.to_numpy(), index=landing).groupby(level=0).sum().reindex(v.index, fill_value=0.0)
    r = (v - flows) / v.shift(1) - 1
    r.iloc[0] = 0.0
    twr, raw = curve_stats((1 + r).cumprod()), curve_stats(v)
    return {
        "start": v.index[0], "end": v.index[-1],
        "twr": twr["total_return"],
        "max_drawdown": twr["max_drawdown"],
        "peak_date": twr["peak_date"], "trough_date": twr["trough_date"],
        "current_drawdown": twr["current_drawdown"],
        "raw_max_drawdown": raw["max_drawdown"],
        "raw_current_drawdown": raw["current_drawdown"],
        "contributions_cad": float(c.sum()),
    }


def _cad_rate(currency: str, usdcad: float) -> float:
    """CAD per unit of ``currency``; NaN for anything but CAD / USD, so a stray
    currency shows up as a gap instead of being silently counted as CAD."""
    return {"CAD": 1.0, "USD": usdcad}.get(str(currency).upper(), np.nan)


def stop_risk(stocks: pd.DataFrame, tech: dict, *, usdcad: float, total_cad: float) -> dict:
    """What each held stock would lose if it fell to its trade plan's stop.

    ``stocks`` are :func:`combined`'s stock rows (one per symbol, shares summed
    across accounts). Uses :func:`stockanalysis.tradeplan.build_trade_plan` on
    the holding's enriched price frame (``tech[name]``) — the same support-based
    stop the Trade Plan shows. ``heat`` is the total as a share of the
    household: the loss if every stop were hit at once. Holdings without prices
    or a stop are listed in ``no_stop``.
    """
    if stocks.empty:
        return {"rows": pd.DataFrame(), "heat": 0.0, "total_cad": 0.0, "no_stop": []}
    rows, no_stop = [], []
    for pos in stocks.itertuples():
        name = pos.name
        df = tech.get(name)
        plan = build_trade_plan(df) if df is not None else {}
        stop = plan.get("stop", np.nan)
        last = value_at(df, "Close") if df is not None else np.nan
        if not (np.isfinite(stop) and np.isfinite(last)):
            no_stop.append(name)
            continue
        per_share = max(last - stop, 0.0)
        rows.append({"name": name, "shares": pos.shares, "last": last, "stop": stop,
                     "drop": per_share / last,
                     "loss_cad": pos.shares * per_share * _cad_rate(pos.currency, usdcad)})
    table = pd.DataFrame(rows)
    total = float(table["loss_cad"].sum()) if not table.empty else 0.0
    if not table.empty:
        table = table.sort_values("loss_cad", ascending=False, ignore_index=True)
    return {"rows": table, "total_cad": total,
            "heat": total / total_cad if total_cad else np.nan, "no_stop": no_stop}


def funding(holdings: pd.DataFrame, signal_matrix: pd.DataFrame, *, usdcad: float) -> dict:
    """Can today's Buys be paid for from cash already in an account?

    Cash is counted per account in its own currency (a Buy needs its listing
    currency, :func:`stockanalysis.holdings.listing_currency`); a money-market
    fund isn't spendable until sold, so only ``kind == "cash"`` rows count. Registered accounts can't borrow, so a Buy no
    account can cover is flagged; accounts named "margin" are listed apart.
    """
    cash = (holdings[holdings["kind"] == "cash"].groupby(["account", "currency"], as_index=False)
            ["value"].sum().rename(columns={"value": "cash"}))
    margin = sorted({a for a in holdings["account"] if "margin" in a.casefold()})
    empty = pd.DataFrame(columns=["Ticker", "Shares", "Entry", "currency", "notional",
                                  "notional_cad", "accounts"])
    out = {"cash": cash, "buys": empty, "flags": [], "margin_accounts": margin}
    if signal_matrix is None or signal_matrix.empty or "Final Action Signal" not in signal_matrix:
        return out
    buys = signal_matrix.loc[signal_matrix["Final Action Signal"] == "Buy",
                             ["Ticker", "Shares", "Entry"]].copy()
    if buys.empty:
        return out
    buys["currency"] = buys["Ticker"].map(listing_currency)
    buys["notional"] = buys["Shares"] * buys["Entry"]
    buys["notional_cad"] = buys["notional"] * buys["currency"].map(lambda c: _cad_rate(c, usdcad))
    buys["accounts"] = [
        cash.loc[(cash["currency"] == ccy) & (cash["cash"] >= need), "account"].tolist()
        for ccy, need in zip(buys["currency"], buys["notional"])]
    out["buys"] = buys.reset_index(drop=True)
    out["flags"] = [f"{r.Ticker}: no account holds {r.currency} {r.notional:,.0f} in cash"
                    for r in buys.itertuples() if not r.accounts]
    return out


# -----------------------------------------------------------------------------
# Describing securities, CAD conversion and assembly
# -----------------------------------------------------------------------------

_CASH_WORDS = ("cash management", "money market", "high interest savings", "savings account",
               "t-bill", "treasury bill")


def describe(raw: dict) -> dict:
    """A yfinance ``.info`` dict -> ``{name, sector, currency, cash_equivalent, proxy}``.

    Classification is by the fund's own name, never by a list of tickers (the
    repo is public; a ticker list would name the holdings): a cash-management /
    money-market fund is cash, and a bitcoin fund is "Crypto" with bitcoin itself
    (in the fund's currency) as its stress-test proxy for years before it listed.
    Funds without a sector use their category, else "Index fund" / "Fund".
    """
    raw = raw or {}
    name = raw.get("longName") or raw.get("shortName") or ""
    lname = name.casefold()
    cash_eq = any(w in lname for w in _CASH_WORDS)
    bitcoin = "bitcoin" in lname
    is_fund = raw.get("quoteType") in ("ETF", "MUTUALFUND")
    sector = ("Cash" if cash_eq else "Crypto" if bitcoin
              else raw.get("sector") or raw.get("category")
              or ("Index fund" if is_fund and "index" in lname else "Fund" if is_fund else "Unknown"))
    currency = (raw.get("currency") or "USD").upper()
    return {"name": name, "sector": sector, "currency": currency, "cash_equivalent": cash_eq,
            "proxy": f"BTC-{currency}" if bitcoin else None}


def to_cad(closes: pd.DataFrame, currencies: dict, usdcad: pd.Series) -> pd.DataFrame:
    """Closes -> CAD: USD columns times the USD/CAD rate of the same day
    (forward-filled over FX holidays); anything else is left as is."""
    fx = usdcad.reindex(closes.index).ffill()
    out = closes.copy()
    for col in out.columns:
        if currencies.get(col, "CAD") == "USD":
            out[col] = out[col] * fx
    return out


def build_risk(holdings: pd.DataFrame, market: dict, *, history: pd.Series | None = None,
               contributions: pd.Series | None = None, signal_matrix: pd.DataFrame | None = None,
               windows: dict = config.STRESS_WINDOWS) -> dict:
    """Every section of the risk report from already-fetched data (pure).

    ``market`` is :func:`fetch_market`'s dict: ``info`` (symbol -> describe()),
    ``closes_cad`` / ``bench_cad`` (CAD closes on one calendar), ``fx``
    (currency -> CAD per unit, on that calendar), ``tech`` (symbol -> enriched
    native-currency bars), ``usdcad`` (latest rate) and ``proxies``.
    """
    ex = exposure(holdings, market["info"])
    total = ex["total_cad"]
    closes, bench = market["closes_cad"].copy(), market["bench_cad"]
    rows = ex["holdings"]
    stocks = rows[rows["kind"] == "stock"]
    # Foreign cash is flat in its own currency, not in CAD: price each such line
    # by its FX series. CAD cash and unpriced holdings stay out — flat.
    fx = market.get("fx") or {}
    fx_cash = rows[(rows["kind"] == "cash") & rows["currency"].isin(fx)]
    for r in fx_cash.itertuples():
        closes[r.name] = fx[r.currency].reindex(closes.index).ffill()
    priced = pd.concat([stocks, fx_cash])
    weights = pd.Series(priced["weight"].to_numpy(), index=priced["name"])
    return {
        "as_of": closes.index[-1] if not closes.empty else None,
        "exposure": ex,
        "market": market_risk(weights, closes.pct_change(fill_method=None),
                              bench.pct_change(fill_method=None), total_cad=total,
                              corr_names=list(stocks["name"])),
        "stress": stress(weights, closes, windows, total_cad=total,
                         proxies=market.get("proxies"), bench_prices=bench),
        "drawdown": account_drawdown(history, contributions) if history is not None else None,
        "stops": stop_risk(stocks, market.get("tech", {}), usdcad=market["usdcad"], total_cad=total),
        "funding": funding(holdings, signal_matrix, usdcad=market["usdcad"]),
    }


def fetch_market(holdings: pd.DataFrame, *, period: str = "10y") -> dict:
    """Network: describe each held symbol and fetch ``period`` of daily bars for
    them, their proxies, the benchmarks and USD/CAD — the ``market`` dict
    :func:`build_risk` takes. Closes are put on the first benchmark's trading
    calendar (TSX holidays and crypto weekends forward-filled a few days). The
    ``.info`` lookups run in parallel (they are most of the wall clock), and an
    empty answer is retried once: ``describe({})`` would call a money-market
    fund a stock."""
    from concurrent.futures import ThreadPoolExecutor

    from . import ingest
    from .pipeline import compute_indicators

    stocks = holdings[holdings["kind"] == "stock"]
    symbols = sorted(stocks["yahoo"].dropna().unique())
    with ThreadPoolExecutor(max_workers=6) as pool:
        raw = dict(zip(symbols, pool.map(ingest.fetch_info, symbols)))
    raw.update({s: ingest.fetch_info(s) for s, r in raw.items() if not r})
    info = {s: describe(r) for s, r in raw.items()}
    proxies = {s: d["proxy"] for s, d in info.items() if d["proxy"]}
    bench = config.RISK_BENCHMARKS
    bars = ingest.fetch_bulk_prices(symbols + list(bench.values()) + sorted(set(proxies.values()))
                                    + [config.USDCAD_TICKER], period=period)

    def closes(syms):
        return pd.DataFrame({s: bars[s]["Close"] for s in syms if s in bars})

    fx = bars[config.USDCAD_TICKER]["Close"] if config.USDCAD_TICKER in bars else pd.Series(dtype=float)
    anchor = next(iter(bench.values()))
    calendar = bars[anchor].index if anchor in bars else None
    currency = dict(stocks.groupby("yahoo")["currency"].first())
    currency.update({p: info[s]["currency"] for s, p in proxies.items()})

    held = closes(symbols + sorted(set(proxies.values())))
    bench_c = closes(bench.values()).rename(columns={v: k for k, v in bench.items()})
    if calendar is not None:
        held = held.reindex(held.index.union(calendar)).ffill(limit=5).reindex(calendar)
        bench_c = bench_c.reindex(calendar)
    return {
        "info": info,
        "proxies": proxies,
        "closes_cad": to_cad(held, currency, fx),
        "bench_cad": to_cad(bench_c, {k: "USD" for k in bench}, fx),
        "fx": {"USD": fx.reindex(held.index).ffill() if calendar is not None else fx},
        "tech": compute_indicators({s: bars[s] for s in symbols if s in bars}),
        "usdcad": float(fx.dropna().iloc[-1]) if not fx.dropna().empty else float("nan"),
    }
