"""Combined per-run HTML report.

One self-contained ``report.html`` bundling the Fundamental Screener, the
Combined Signal Matrix, top-N Technical Dashboards, top-N Fundamental
Profiles, and the Daily Market Overview — everything a run produces, in one
file. Mirrors :mod:`stockanalysis.thesis.report`'s shape: a pure renderer
(:func:`build_full_report`) that takes already-fetched data and returns an
HTML string, plus a thin I/O wrapper (:func:`save_report`) that persists it
(mirroring :func:`stockanalysis.charts.save_html` /
:func:`stockanalysis.profile.save_report` — unlike
``thesis.report.write_report``, it does not resolve its own timestamped
directory, since :func:`stockanalysis.pipeline.run` already owns one shared
``run_dir`` that everything lands in).

No ``Styler``/``fig.show()``/print side effects — like ``thesis/report.py``,
this module is a deliberate, precedented exception to "presentation lives
only in the notebook/CLI": the rule is about *interactive* display, not a
pure function that returns a string.
"""
from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.offline import get_plotlyjs_version

from . import charts
from .profile import _fmt_val  # cross-module private helper: same number
# formatting the ASCII profile report already uses, kept consistent rather
# than re-implemented.
from .signals import ACTION_COLORS, POSTURE_COLORS

# CSS wants a leading '#'; signals.py's palette is bare hex (openpyxl's
# PatternFill wants it bare too, so that's the shared, format-agnostic form).
_ACTION_COLORS = {k: f"#{v}" for k, v in ACTION_COLORS.items()}
_POSTURE_COLORS = {k: f"#{v}" for k, v in POSTURE_COLORS.items()}

# Number formats mirroring the notebook's style_screen / excel.py conventions.
_NUMBER_FORMATS = {
    "PE": "{:.1f}", "EPS_Growth": "{:.1%}", "Rev_Growth": "{:.1%}",
    "Debt_Equity": "{:.2f}", "Div_Yield": "{:.2%}", "FCF": "{:,.0f}",
    "Composite": "{:.3f}",
}

_SCREENER_COLUMNS = ["Ticker", "Sector", "PE", "EPS_Growth", "Rev_Growth",
                     "Debt_Equity", "Div_Yield", "FCF", "Fundamental_Score"]
_SIGNAL_COLUMNS = ["Ticker", "Sector", "Fundamental Score", "Technical Posture",
                   "Tech Score", "Composite", "Final Action Signal"]


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))


def _esc(value) -> str:
    """HTML-escape a cell value; ``None``/NaN -> em dash."""
    return "—" if _is_missing(value) else html.escape(str(value))


def _fmt_cell(col: str, value):
    """Format one table cell via ``_NUMBER_FORMATS`` when the column has a
    format, else fall back to plain escaping."""
    fmt = _NUMBER_FORMATS.get(col)
    if fmt is None or _is_missing(value):
        return _esc(value)
    try:
        return _esc(fmt.format(value))
    except (ValueError, TypeError):
        return _esc(value)


def _badge(value, colors: dict) -> str:
    color = colors.get(value, "#D9D9D9")
    return (f'<span class="badge" style="background:{color}">'
            f'{_esc(value)}</span>')


def _score_color(score, max_score: int) -> str:
    """Green/amber/red by fraction of max_score — reuses the badge palette."""
    if score is None or max_score <= 0:
        return "#D9D9D9"
    frac = score / max_score
    if frac >= 2 / 3:
        return "#B7E1CD"
    if frac >= 1 / 3:
        return "#FCE8B2"
    return "#F4C7C3"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _render_screener_section(screened_df: pd.DataFrame) -> str:
    if screened_df is None or screened_df.empty:
        return '<p class="empty">No fundamentals passed the screener.</p>'
    df = screened_df.reset_index()
    cols = [c for c in _SCREENER_COLUMNS if c in df.columns]
    rows = [[_fmt_cell(c, row[c]) for c in cols] for _, row in df[cols].iterrows()]
    return _table(cols, rows)


def _render_signal_matrix_section(signal_matrix: pd.DataFrame) -> str:
    if signal_matrix is None or signal_matrix.empty:
        return '<p class="empty">No signals generated.</p>'
    df = signal_matrix
    cols = [c for c in _SIGNAL_COLUMNS if c in df.columns]
    rows = []
    for _, row in df[cols].iterrows():
        cells = []
        for c in cols:
            if c == "Final Action Signal":
                cells.append(_badge(row[c], _ACTION_COLORS))
            elif c == "Technical Posture":
                cells.append(_badge(row[c], _POSTURE_COLORS))
            else:
                cells.append(_fmt_cell(c, row[c]))
        rows.append(cells)
    return _table(cols, rows)


def _render_fig(fig, div_id: str) -> str:
    """Embed a Plotly figure inline, reusing the page's single CDN script tag."""
    return (f'<div class="dashboard">'
           f'{fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id)}</div>')


def _render_dashboards_section(tech: dict, selected: list[str]) -> str:
    if not selected:
        return '<p class="empty">No tickers selected.</p>'
    parts = []
    for ticker in selected:
        fig = charts.build_technical_dashboard(ticker, tech)
        if fig is None:
            parts.append(f'<p class="empty">No chart data for {_esc(ticker)}.</p>')
            continue
        parts.append(_render_fig(fig, div_id=f"plt-{ticker}"))
    return "".join(parts)


def _render_profile_card(profile_dict: dict) -> str:
    ticker = profile_dict.get("ticker", "")
    raw = profile_dict.get("raw") or {}
    scores = profile_dict.get("scores") or {}
    # build_profile's own screened_df-preferred-over-raw resolution (PE,
    # Debt_Equity, FCF, EPS/Rev growth — profile.py:69-85) — read directly
    # rather than re-derived here, so this card never drifts from the ASCII
    # report's numbers for the same ticker.
    fund = profile_dict.get("fundamentals") or {}

    def fv(key: str, fmt: str) -> str:
        return _fmt_val(fund.get(key), fmt)

    def rv(key: str, fmt: str) -> str:
        return _fmt_val(raw.get(key), fmt)

    header = (
        f'<div class="profile-header"><h3>{_esc(profile_dict.get("name") or ticker)} '
        f'({_esc(ticker)})</h3>'
        f'<p>{_esc(profile_dict.get("sector"))} · {_esc(profile_dict.get("industry"))} · '
        f'{_esc(profile_dict.get("country"))}</p>'
    )
    fund_score = profile_dict.get("fundamental_score")
    if fund_score is not None:
        header += (f'<span class="badge" style="background:{_score_color(fund_score, 6)}">'
                   f'Fundamental {fund_score}/6</span>')
    header += "</div>"

    score_badges = "".join(
        f'<span class="badge" style="background:{_score_color(scores.get(key), maxv)}">'
        f'{label} {scores.get(key, "—")}/{maxv}</span>'
        for key, label, maxv in (("management", "Management", 3),
                                 ("moat", "Moat", 3), ("long_term", "Long-Term", 4))
    )

    def _group_html(title, rows):
        items = "".join(f"<li>{label}: <b>{val}</b></li>" for val, label in rows)
        return f"<div class='profile-group'><h4>{_esc(title)}</h4><ul>{items}</ul></div>"

    groups = [
        _group_html("Growth", [
            (fv("eps_growth", "pct"), "EPS Growth"),
            (fv("rev_growth", "pct"), "Revenue Growth"),
        ]),
        _group_html("Profitability", [
            (rv("grossMargins", "pct"), "Gross Margin"),
            (rv("operatingMargins", "pct"), "Op. Margin"),
            (rv("profitMargins", "pct"), "Net Margin"),
            (rv("returnOnEquity", "pct"), "ROE"),
            (rv("returnOnAssets", "pct"), "ROA"),
        ]),
        _group_html("Health", [
            (fv("debt_equity", "ratio"), "Debt/Equity"),
            (fv("fcf", "cash"), "FCF / Cash"),
            (rv("currentRatio", "ratio"), "Current Ratio"),
            (rv("quickRatio", "ratio"), "Quick Ratio"),
        ]),
        _group_html("Valuation", [
            (fv("pe", "ratio"), "P/E"),
            (rv("priceToBook", "ratio"), "P/B"),
            (rv("priceToSalesTrailing12Months", "ratio"), "P/S"),
            (rv("pegRatio", "ratio"), "PEG"),
            (rv("enterpriseToEbitda", "ratio"), "EV/EBITDA"),
        ]),
        _group_html("Ownership", [
            (rv("heldPercentInsiders", "pct"), "Insiders"),
            (rv("heldPercentInstitutions", "pct"), "Institutions"),
            (rv("shortPercentOfFloat", "pct"), "Short % Float"),
        ]),
    ]

    return (f'<div class="profile-card">{header}<div class="scores">{score_badges}</div>'
           f'<div class="profile-groups">{"".join(groups)}</div></div>')


def _render_profiles_section(profiles: list[dict]) -> str:
    if not profiles:
        return '<p class="empty">No profiles selected.</p>'
    return "".join(_render_profile_card(p) for p in profiles)


def _render_overview_section(overview_data: dict) -> str:
    overview_data = overview_data or {}
    index_data = overview_data.get("index_data") or {}
    parts = []

    if index_data:
        parts.append(_render_fig(charts.build_index_overview(index_data), div_id="plt-overview"))
    else:
        parts.append('<p class="empty">No index data available.</p>')

    vix = overview_data.get("vix")
    if vix:
        parts.append(f'<p><b>VIX</b> {vix["value"]:.2f} → {_esc(vix["label"])}</p>')
    else:
        parts.append('<p class="empty">VIX: unavailable</p>')

    indices = overview_data.get("indices") or []
    if indices:
        cols = ["Index", "Last", "Day %", "Week %", "YTD %", "RSI", "Trend"]

        def _index_cell(val):
            return _esc(round(val, 2)) if isinstance(val, float) else _esc(val)

        rows = [[_index_cell(s.get(c)) for c in cols] for s in indices]
        parts.append(_table(cols, rows))
    else:
        parts.append('<p class="empty">No index stats available.</p>')

    headlines = overview_data.get("headlines") or []
    if headlines:
        items = "".join(
            f'<li>{_esc(h.get("published"))} — {_esc(h.get("title"))} '
            f'({_esc(h.get("publisher"))})</li>' for h in headlines
        )
        parts.append(f"<ul>{items}</ul>")
    else:
        parts.append('<p class="empty">No recent headlines.</p>')

    return "".join(parts)


_STYLE = """
body{font-family:system-ui,Arial,sans-serif;margin:0;color:#222;background:#fafafa}
header{padding:1.5rem 2rem 1rem}
header h1{margin-bottom:0}
.generated{color:#888;font-size:0.9em}
nav{position:sticky;top:0;background:#1F3864;padding:0.75rem 2rem;z-index:10}
nav a{color:#fff;text-decoration:none;margin-right:1.5rem;font-size:0.95em}
nav a:hover{text-decoration:underline}
section{padding:1.5rem 2rem;max-width:1400px}
section:has(.dashboard){max-width:1900px}
section h2{border-bottom:2px solid #1F3864;padding-bottom:0.3rem}
table{border-collapse:collapse;width:100%;font-size:0.9em;margin-top:0.5rem}
th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
th{background:#1F3864;color:#fff}
tr:nth-child(even){background:#f3f3f3}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.85em;margin:2px}
.empty{color:#888;font-style:italic}
.dashboard{margin:1rem 0}
.profile-card{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0;background:#fff}
.profile-header h3{margin-bottom:0.2rem}
.profile-header p{color:#666;margin-top:0}
.profile-groups{display:flex;flex-wrap:wrap;gap:1.5rem;margin-top:0.5rem}
.profile-group h4{margin-bottom:0.3rem}
.profile-group ul{margin:0;padding-left:1.2rem}
"""

_SECTIONS = [
    ("screener", "Fundamental Screener"),
    ("signals", "Combined Signal Matrix"),
    ("dashboards", "Top Technical Dashboards"),
    ("profiles", "Fundamental Profiles"),
    ("overview", "Daily Market Overview"),
]


def build_full_report(
    screened_df: pd.DataFrame,
    signal_matrix: pd.DataFrame,
    tech: dict,
    profiles: list[dict],
    overview_data: dict,
    *,
    selected: list[str],
    generated_at: str,
) -> str:
    """Render the full combined report as one self-contained HTML string.

    Pure: no I/O, no network. ``screened_df``/``signal_matrix`` render in
    full (never capped by ``selected``); the Dashboards and Profiles
    sections cover only ``selected`` (the top-N picks). ``profiles`` is the
    pre-fetched list of :func:`stockanalysis.profile.build_profile` dicts
    for ``selected``, in the same order; ``overview_data`` is
    :func:`stockanalysis.overview.daily_overview`'s return dict.
    """
    nav = "".join(f'<a href="#{anchor}">{title}</a>' for anchor, title in _SECTIONS)
    body_sections = [
        _render_screener_section(screened_df),
        _render_signal_matrix_section(signal_matrix),
        _render_dashboards_section(tech, selected),
        _render_profiles_section(profiles),
        _render_overview_section(overview_data),
    ]
    sections_html = "".join(
        f'<section id="{anchor}"><h2>{title}</h2>{content}</section>'
        for (anchor, title), content in zip(_SECTIONS, body_sections)
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Stock Analysis Report</title>"
        # plotly.js CDN versions track the bundled JS library, not the plotly.py
        # package version (plotly.__version__) — using the latter 404s the script.
        f'<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"></script>'
        f"<style>{_STYLE}</style></head><body>"
        f"<header><h1>Stock Analysis Report</h1>"
        f"<p class='generated'>Generated {_esc(generated_at)}</p></header>"
        f"<nav>{nav}</nav>"
        f"{sections_html}"
        "</body></html>"
    )


def save_report(html_doc: str, path) -> str:
    """Write ``html_doc`` to ``path`` as UTF-8, creating parent dirs. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
    return str(path)
