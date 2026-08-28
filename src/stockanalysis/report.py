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
import inspect
import re
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.offline import get_plotlyjs_version

from . import charts
from .profile import _fmt_val  # cross-module private helper: same number
# formatting the ASCII profile report already uses, kept consistent rather
# than re-implemented.
from .screener import screen_fundamentals
from .signals import TECHNICAL_COMPONENTS, compute_technical_posture

# Solid dark-theme palette for the signal matrix, mirroring the notebook's
# local style_signals() ac/pc dicts (notebooks/stock_analysis.ipynb) rather
# than signals.py's ACTION_COLORS/POSTURE_COLORS — those are pastel fills
# tuned for Excel's white sheet background, a different rendering surface.
_ACTION_COLORS = {"Buy": "#1b7837", "Hold": "#b8860b", "Watch": "#6e7681"}
_POSTURE_TEXT_COLORS = {"Bullish": "#3fb950", "Neutral": "#d4a72c", "Bearish": "#f85149"}

# ColorBrewer sequential/diverging stops (low -> high), linearly interpolated
# by _colormap — same "Greens"/"RdYlGn" families the notebook's
# .background_gradient(cmap=...) calls use, hand-rolled here since report.py
# may not use pandas Styler (see module docstring).
_GREENS = ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476",
           "#41ab5d", "#238b45", "#006d2c", "#00441b"]
_RDYLGN = ["#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b",
           "#ffffbf", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"]

# Number formats mirroring the notebook's style_screen / excel.py conventions.
_NUMBER_FORMATS = {
    "PE": "{:.1f}", "EPS_Growth": "{:.1%}", "Rev_Growth": "{:.1%}",
    "Debt_Equity": "{:.2f}", "Div_Yield": "{:.2%}", "FCF": "{:,.0f}",
}

_SCREENER_COLUMNS = ["Ticker", "Sector", "PE", "EPS_Growth", "Rev_Growth",
                     "Debt_Equity", "Div_Yield", "FCF", "Fundamental_Score"]
_SIGNAL_COLUMNS = ["Ticker", "Sector", "Fundamental Score", "Technical Posture",
                   "Tech Score", "Composite", "Final Action Signal"]

# Which boolean pass-flag column (from screen_fundamentals) backs each metric
# column, and the threshold shown in its header — read off screen_fundamentals'
# own defaults so this can't drift from the actual scoring logic.
_SCREEN_PARAMS = inspect.signature(screen_fundamentals).parameters
_SCREENER_PASS_COLS = {
    "PE": "Pass_PE", "EPS_Growth": "Pass_EPS", "Rev_Growth": "Pass_Rev",
    "Debt_Equity": "Pass_DE", "Div_Yield": "Pass_Div", "FCF": "Pass_FCF",
}
_SCREENER_HEADERS = {
    "PE": f"PE (< {_SCREEN_PARAMS['pe_max'].default:g})",
    "EPS_Growth": f"EPS Growth (> {_SCREEN_PARAMS['eps_growth_min'].default:.0%})",
    "Rev_Growth": f"Rev Growth (> {_SCREEN_PARAMS['rev_growth_min'].default:.0%})",
    "Debt_Equity": f"Debt/Equity (< {_SCREEN_PARAMS['de_max'].default:g})",
    "Div_Yield": f"Div Yield (> {_SCREEN_PARAMS['div_yield_min'].default:.1%})",
    "FCF": f"FCF (> {_SCREEN_PARAMS['fcf_min'].default:g})",
}
_SCREENER_PASS_BG = "#1b7837"  # same green as the signal matrix's "Buy" cell

_TECH_FORMULA_RE = re.compile(r":\s*(?P<formula>[^:]+)$")


def _tech_header(name: str, fn) -> str:
    """Short label + the predicate's own formula, read live from its
    docstring (mirrors _SCREENER_HEADERS reading screen_fundamentals's
    defaults) so a header can't drift from the actual predicate logic.
    Falls back to the bare label when the docstring doesn't end in the
    expected "...: formula." shape, rather than risking a garbled header."""
    label = name.replace("_", " ").title()
    doc = " ".join((fn.__doc__ or "").split())
    match = _TECH_FORMULA_RE.search(doc)
    if not match:
        return label
    return f"{label} ({match['formula'].rstrip('.')})"


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


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _colormap(stops: list[str], frac: float) -> str:
    """Linearly interpolate a ColorBrewer-style stop list at ``frac`` (0-1,
    NaN/out-of-range clamped) — a hand-rolled stand-in for matplotlib's
    ``Greens``/``RdYlGn`` colormaps (see ``_GREENS``/``_RDYLGN``)."""
    frac = 0.0 if frac != frac else min(max(frac, 0.0), 1.0)
    n = len(stops) - 1
    pos = frac * n
    i = min(int(pos), n - 1)
    t = pos - i
    c0, c1 = _hex_to_rgb(stops[i]), _hex_to_rgb(stops[i + 1])
    return _rgb_to_hex(c0[k] + (c1[k] - c0[k]) * t for k in range(3))


def _contrast_text(bg_hex: str) -> str:
    """Light or dark text for readability atop ``bg_hex``, by perceptual luminance."""
    r, g, b = _hex_to_rgb(bg_hex)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#0d1117" if luminance > 0.55 else "#f0f6fc"


def _score_color(score, max_score: int) -> str:
    """Greens-gradient background by fraction of max_score."""
    if score is None or max_score <= 0 or _is_missing(score):
        return "#30363d"
    return _colormap(_GREENS, score / max_score)


def _score_badge(label: str, score, maxv: int) -> str:
    bg = _score_color(score, maxv)
    shown = "—" if _is_missing(score) else score
    return (f'<span class="badge" style="background:{bg};color:{_contrast_text(bg)}">'
            f'{_esc(label)} {_esc(shown)}/{maxv}</span>')


def _heatmap_cell(value, vmax: float, stops: list[str], fmt: str | None = None) -> str:
    """A ``<td>`` whose background is ``stops`` interpolated at value/vmax,
    with auto contrast text — the signal matrix's per-column heatmap cells."""
    if _is_missing(value):
        return '<td class="empty-cell">—</td>'
    text = fmt.format(value) if fmt else str(value)
    if vmax <= 0:
        return f'<td style="text-align:center">{_esc(text)}</td>'
    bg = _colormap(stops, value / vmax)
    return (f'<td style="background:{bg};color:{_contrast_text(bg)};'
            f'text-align:center">{_esc(text)}</td>')


def _action_cell(value) -> str:
    if _is_missing(value):
        return '<td class="empty-cell">—</td>'
    bg = _ACTION_COLORS.get(value, "#6e7681")
    return (f'<td style="background:{bg};color:#ffffff;font-weight:700;'
            f'text-align:center">{_esc(value)}</td>')


def _posture_cell(value) -> str:
    if _is_missing(value):
        return '<td class="empty-cell">—</td>'
    color = _POSTURE_TEXT_COLORS.get(value, "#c9d1d9")
    return f'<td style="color:{color};font-weight:600;text-align:center">{_esc(value)}</td>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _table_raw(headers: list[str], rows: list[list[str]]) -> str:
    """Like :func:`_table` but each row cell is already a complete ``<td>...</td>``
    string (used where cells need per-cell inline styling, e.g. heatmap fills)."""
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _render_screener_section(screened_df: pd.DataFrame) -> str:
    if screened_df is None or screened_df.empty:
        return '<p class="empty">No fundamentals passed the screener.</p>'
    df = screened_df.reset_index()
    cols = [c for c in _SCREENER_COLUMNS if c in df.columns]
    headers = [html.escape(_SCREENER_HEADERS.get(c, c)) for c in cols]
    rows = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            text = _fmt_cell(c, row[c])
            pass_col = _SCREENER_PASS_COLS.get(c)
            if pass_col in df.columns and bool(row[pass_col]):
                cells.append(f'<td style="background:{_SCREENER_PASS_BG};'
                             f'color:{_contrast_text(_SCREENER_PASS_BG)}">{text}</td>')
            else:
                cells.append(f"<td>{text}</td>")
        rows.append(cells)
    return _table_raw(headers, rows)


def _render_technical_screener_section(tech: dict) -> str:
    if not tech:
        return '<p class="empty">No technical data available.</p>'
    tech_max = len(TECHNICAL_COMPONENTS)
    entries = sorted(
        ((ticker, *compute_technical_posture(df)) for ticker, df in tech.items()),
        key=lambda e: (-e[2], e[0]),
    )

    headers = (["Ticker"]
               + [html.escape(_tech_header(name, fn)) for name, fn in TECHNICAL_COMPONENTS]
               + ["Tech Score", "Technical Posture"])

    rows = []
    for ticker, posture, score, detail in entries:
        cells = [f"<td>{_esc(ticker)}</td>"]
        for name, _ in TECHNICAL_COMPONENTS:
            if detail.get(name, False):
                cells.append(f'<td style="background:{_SCREENER_PASS_BG};'
                             f'color:{_contrast_text(_SCREENER_PASS_BG)};'
                             f'text-align:center">&#10003;</td>')
            else:
                cells.append('<td style="text-align:center">—</td>')
        cells.append(_heatmap_cell(score, tech_max, _GREENS))
        cells.append(_posture_cell(posture))
        rows.append(cells)
    return _table_raw(headers, rows)


def _render_signal_matrix_section(signal_matrix: pd.DataFrame) -> str:
    if signal_matrix is None or signal_matrix.empty:
        return '<p class="empty">No signals generated.</p>'
    df = signal_matrix
    cols = [c for c in _SIGNAL_COLUMNS if c in df.columns]
    tech_max = len(TECHNICAL_COMPONENTS)
    rows = []
    for _, row in df[cols].iterrows():
        cells = []
        for c in cols:
            value = row[c]
            if c == "Final Action Signal":
                cells.append(_action_cell(value))
            elif c == "Technical Posture":
                cells.append(_posture_cell(value))
            elif c == "Fundamental Score":
                cells.append(_heatmap_cell(value, 6, _GREENS))
            elif c == "Tech Score":
                cells.append(_heatmap_cell(value, tech_max, _GREENS))
            elif c == "Composite":
                cells.append(_heatmap_cell(value, 1, _RDYLGN, fmt="{:.2f}"))
            else:
                cells.append(f"<td>{_fmt_cell(c, value)}</td>")
        rows.append(cells)
    return _table_raw(cols, rows)


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
        header += _score_badge("Fundamental", fund_score, 6)
    header += "</div>"

    score_badges = "".join(
        _score_badge(label, scores.get(key), maxv)
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
body{font-family:system-ui,Arial,sans-serif;margin:0;color:#c9d1d9;background:#0d1117}
header{padding:1.5rem 2rem 1rem}
header h1{margin-bottom:0;color:#f0f6fc}
.generated{color:#8b949e;font-size:0.9em}
nav{position:sticky;top:0;background:#161b22;padding:0.75rem 2rem;z-index:10;border-bottom:1px solid #30363d}
nav a{color:#c9d1d9;text-decoration:none;margin-right:1.5rem;font-size:0.95em}
nav a:hover{color:#58a6ff;text-decoration:underline}
section{padding:1.5rem 2rem;max-width:1400px}
section:has(.dashboard){max-width:1900px}
section h2{border-bottom:2px solid #30363d;padding-bottom:0.3rem;color:#f0f6fc}
table{border-collapse:collapse;width:100%;font-size:0.9em;margin-top:0.5rem}
th,td{border:1px solid #30363d;padding:6px 8px;text-align:left}
th{background:#161b22;color:#f0f6fc}
tr:nth-child(even) td:not([style]){background:#11151c}
td.empty-cell{color:#8b949e;text-align:center}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.85em;margin:2px;font-weight:600}
.empty{color:#8b949e;font-style:italic}
.dashboard{margin:1rem 0;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:0.5rem}
.profile-card{border:1px solid #30363d;border-radius:8px;padding:1rem;margin:1rem 0;background:#161b22}
.profile-header h3{margin-bottom:0.2rem;color:#f0f6fc}
.profile-header p{color:#8b949e;margin-top:0}
.profile-groups{display:flex;flex-wrap:wrap;gap:1.5rem;margin-top:0.5rem}
.profile-group h4{margin-bottom:0.3rem;color:#f0f6fc}
.profile-group ul{margin:0;padding-left:1.2rem}
"""

_SECTIONS = [
    ("screener", "Fundamental Screener"),
    ("tech_screener", "Technical Screener"),
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
        _render_technical_screener_section(tech),
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
