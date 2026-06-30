"""Monitoring & postmortem: MAE/MFE, the closed-trade report, and summary stats.

Prices come from a small **injectable** adapter so the rest of the module is
pure and offline-testable. The default :class:`YFinancePriceAdapter` reuses the
package's existing :func:`stockanalysis.ingest.fetch_stock_data`, so MAE/MFE
needs no FMP key (the original skill's paid dependency).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from . import model, store


class YFinancePriceAdapter:
    """Daily closes from Yahoo Finance, shaped for :func:`compute_mae_mfe`.

    Wraps the package's own fetcher; only used when a real (online) price series
    is wanted. Tests inject a fake adapter with the same ``get_daily_closes``
    contract instead.
    """
    source = "yfinance"

    def get_daily_closes(self, ticker: str, from_date: str, to_date: str) -> list[dict]:
        from .. import ingest          # local import: keep core import-time light
        hist, _ = ingest.fetch_stock_data(ticker, period="max")
        if hist is None or hist.empty or "Close" not in hist:
            return []
        window = hist.loc[from_date:to_date]
        return [{"date": ts.date().isoformat(), "close": float(c)}
                for ts, c in window["Close"].items()]


def compute_mae_mfe(thesis: dict, price_adapter) -> dict:
    """Max adverse / favorable excursion (%) over the holding window.

    Measured against the actual entry price across daily closes from entry date
    to exit date (or today if still open). Returns all-``None`` when the entry
    price or a price series is unavailable.
    """
    none = {"mae_pct": None, "mfe_pct": None, "mae_mfe_source": None}
    entry_price = thesis.get("entry", {}).get("actual_price")
    entry_date = model.date_only(thesis.get("entry", {}).get("actual_date"))
    if not entry_price or not entry_date or price_adapter is None:
        return none

    exit_date = model.date_only(thesis.get("exit", {}).get("actual_date")) \
        or datetime.now(timezone.utc).date().isoformat()
    closes = [p["close"] for p in price_adapter.get_daily_closes(
        thesis["ticker"], entry_date, exit_date)]
    if not closes:
        return none
    return {
        "mae_pct": round((min(closes) - entry_price) / entry_price * 100, 2),
        "mfe_pct": round((max(closes) - entry_price) / entry_price * 100, 2),
        "mae_mfe_source": getattr(price_adapter, "source", "price_adapter"),
    }


def _fmt(v, suffix="") -> str:
    return "—" if v is None else f"{v}{suffix}"


def _render_postmortem(th: dict) -> str:
    o, e, x, p = th["outcome"], th["entry"], th["exit"], th["position"]
    lines = [
        f"# Postmortem: {th['thesis_id']}", "",
        f"**Ticker:** {th['ticker']}  ",
        f"**Type:** {th['thesis_type']}  ",
        f"**Status:** {th['status']}", "",
        "## Thesis", "", th["thesis_statement"], "",
        "## Timeline", "",
        "| Event | Date | Price |", "|---|---|---|",
        f"| Created | {model.date_only(th['created_at'])} | — |",
        f"| Entry | {_fmt(model.date_only(e['actual_date']))} | {_fmt(e['actual_price'])} |",
        f"| Exit | {_fmt(model.date_only(x['actual_date']))} | {_fmt(x['actual_price'])} |", "",
        "## Outcome", "",
        "| Metric | Value |", "|---|---|",
        f"| P&L ($) | {_fmt(o['pnl_dollars'])} |",
        f"| P&L (%) | {_fmt(o['pnl_pct'], '%')} |",
        f"| Holding days | {_fmt(o['holding_days'])} |",
        f"| Exit reason | {_fmt(x['exit_reason'])} |",
        f"| MAE (%) | {_fmt(o['mae_pct'], '%')} |",
        f"| MFE (%) | {_fmt(o['mfe_pct'], '%')} |", "",
        "## Position", "",
        "| Metric | Value |", "|---|---|",
        f"| Shares | {_fmt(p['shares'])} |",
        f"| Position value | {_fmt(p['position_value'])} |",
        f"| Risk ($) | {_fmt(p['risk_dollars'])} |", "",
    ]
    if th.get("evidence"):
        lines += ["## Evidence at entry", ""] + [f"- {item}" for item in th["evidence"]] + [""]
    if th.get("kill_criteria"):
        lines += ["## Kill criteria", ""] + [f"- {item}" for item in th["kill_criteria"]] + [""]
    lines += ["## Lessons learned", "", o.get("lessons_learned") or "_(none recorded)_", ""]
    return "\n".join(lines)


def generate_postmortem(state_dir, thesis_id: str, *, price_adapter=None,
                        journal_dir=None) -> str:
    """Write a markdown postmortem for a CLOSED/INVALIDATED thesis; return its path.

    If ``price_adapter`` is given, MAE/MFE is computed and persisted back onto
    the thesis' ``outcome`` first. The report is written to
    ``<journal_dir or state_dir/journal>/pm_<thesis_id>.md``.
    """
    th = store.get(state_dir, thesis_id)
    if th["status"] not in model.TERMINAL_STATUSES:
        raise ValueError(f"{thesis_id} is {th['status']}; postmortem needs a closed thesis")

    if price_adapter is not None:
        mm = compute_mae_mfe(th, price_adapter)
        th = store.update(state_dir, thesis_id, {"outcome": mm})

    journal = Path(journal_dir) if journal_dir else Path(state_dir) / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    out_path = journal / f"pm_{thesis_id}.md"
    out_path.write_text(_render_postmortem(th))
    return str(out_path)


def summary_stats(state_dir) -> dict:
    """Aggregate realized performance across all closed/invalidated theses.

    Returns ``count``, ``win_rate`` (fraction with positive P&L), ``avg_pnl_pct``,
    and a per-``thesis_type`` breakdown. All-empty shape when nothing is closed.
    """
    closed = [t for t in store.query(state_dir)
              if t["status"] in model.TERMINAL_STATUSES
              and t["outcome"].get("pnl_dollars") is not None]

    def _agg(theses: list[dict]) -> dict:
        n = len(theses)
        if not n:
            return {"count": 0, "win_rate": None, "avg_pnl_pct": None}
        wins = sum(1 for t in theses if t["outcome"]["pnl_dollars"] > 0)
        pcts = [t["outcome"]["pnl_pct"] for t in theses if t["outcome"].get("pnl_pct") is not None]
        avg = round(sum(pcts) / len(pcts), 4) if pcts else None
        return {"count": n, "win_rate": round(wins / n, 4), "avg_pnl_pct": avg}

    by_type: dict[str, dict] = {}
    for t in closed:
        by_type.setdefault(t["thesis_type"], []).append(t)
    return {**_agg(closed), "by_type": {k: _agg(v) for k, v in by_type.items()}}
