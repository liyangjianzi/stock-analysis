"""Aggregated HTML journal report for the thesis store.

A pure, offline renderer turns the thesis list plus ``review.summary_stats``
output into one self-contained HTML page — mirroring how
``review._render_postmortem`` builds a markdown string inside the package
(returning an artifact string is fine; ``Styler`` / ``fig.show`` / ``print``
presentation stays in the notebook/CLI). ``write_report`` (below) is the thin
I/O wrapper that resolves a timestamped ``output/theses/<ts>/`` folder and saves
``report.html``.
"""
from __future__ import annotations

import html

from . import model

# Mirrors pipeline.RUN_DIR_FMT — replicated so the thesis subpackage stays free
# of the heavy pipeline import chain (charts/plotly/ingest/yfinance).
RUN_DIR_FMT = "%Y-%m-%d_%H%M%S"

# Status -> badge background color for the journal table.
_STATUS_COLORS = {
    "IDEA": "#9e9e9e", "ENTRY_READY": "#1976d2", "ACTIVE": "#2e7d32",
    "PARTIALLY_CLOSED": "#f9a825", "CLOSED": "#455a64", "INVALIDATED": "#c62828",
}

_HEADERS = ["Thesis ID", "Ticker", "Type", "Status", "Entry", "Exit",
            "P&L $", "P&L %", "Hold (d)", "MAE %", "MFE %", "Next review"]


def _esc(value, suffix: str = "") -> str:
    """HTML-escape a cell value; ``None`` -> em dash."""
    return "—" if value is None else f"{html.escape(str(value))}{suffix}"


def _price_date(price, date) -> str:
    """Render a 'price @ date' cell; em dash when both are missing."""
    if price is None and date is None:
        return "—"
    return f"{_esc(price)} @ {_esc(model.date_only(date))}"


def _badge(status: str) -> str:
    color = _STATUS_COLORS.get(status, "#9e9e9e")
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;'
            f'border-radius:10px;font-size:0.85em">{_esc(status)}</span>')


def _row(t: dict) -> str:
    e, x, o, m = t["entry"], t["exit"], t["outcome"], t["monitoring"]
    cells = [
        _esc(t["thesis_id"]), _esc(t["ticker"]), _esc(t["thesis_type"]),
        _badge(t["status"]),
        _price_date(e.get("actual_price"), e.get("actual_date")),
        _price_date(x.get("actual_price"), x.get("actual_date")),
        _esc(o.get("pnl_dollars")), _esc(o.get("pnl_pct"), "%"),
        _esc(o.get("holding_days")),
        _esc(o.get("mae_pct"), "%"), _esc(o.get("mfe_pct"), "%"),
        _esc(m.get("next_review_date")),
    ]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _summary_block(summary: dict) -> str:
    def pct(v):
        return "—" if v is None else f"{round(v * 100, 1)}%"
    by_type = "".join(
        f"<li>{_esc(k)}: {v['count']} trades, win {pct(v['win_rate'])}, "
        f"avg P&L {_esc(v['avg_pnl_pct'], '%')}</li>"
        for k, v in (summary.get("by_type") or {}).items()
    )
    return (
        '<div class="summary"><h2>Summary</h2>'
        f"<p><b>Closed trades:</b> {summary.get('count', 0)} &nbsp; "
        f"<b>Win rate:</b> {pct(summary.get('win_rate'))} &nbsp; "
        f"<b>Avg P&amp;L:</b> {_esc(summary.get('avg_pnl_pct'), '%')}</p>"
        + (f"<ul>{by_type}</ul>" if by_type else "")
        + "</div>"
    )


def build_html_report(theses: list[dict], summary: dict, *, generated_at: str) -> str:
    """Render the full thesis journal as one self-contained HTML document.

    Pure: no I/O, no network. ``theses`` is the list from ``store.query``
    (rendered in the given order); ``summary`` is ``review.summary_stats``
    output; ``generated_at`` is a human-readable timestamp shown in the header.
    Missing optional fields render as an em dash. MAE/MFE appear only when a
    prior postmortem persisted them onto each thesis ``outcome``.
    """
    if theses:
        head = "".join(f"<th>{h}</th>" for h in _HEADERS)
        body = "".join(_row(t) for t in theses)
        table = (f"<table><thead><tr>{head}</tr></thead>"
                 f"<tbody>{body}</tbody></table>")
    else:
        table = '<p class="empty">No theses yet.</p>'
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Thesis Journal</title><style>"
        "body{font-family:system-ui,Arial,sans-serif;margin:2rem;color:#222}"
        "h1{margin-bottom:0}.generated{color:#888;font-size:0.9em}"
        ".summary{background:#f5f5f5;padding:1rem;border-radius:8px;margin:1rem 0}"
        "table{border-collapse:collapse;width:100%;font-size:0.9em}"
        "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}"
        "th{background:#fafafa}tr:nth-child(even){background:#fbfbfb}"
        ".empty{color:#888;font-style:italic}"
        "</style></head><body>"
        "<h1>Thesis Journal</h1>"
        f"<p class='generated'>Generated {_esc(generated_at)} &middot; "
        f"{len(theses)} thesis(es)</p>"
        f"{_summary_block(summary)}{table}"
        "</body></html>"
    )
