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

from . import charts, config
from .profile import _fmt_val  # cross-module private helper: same number
# formatting the ASCII profile report already uses, kept consistent rather
# than re-implemented.
from .screener import screen_fundamentals
from .patterns import detect_patterns
from .signals import TECHNICAL_COMPONENTS, compute_technical_posture
from .tradeplan import MATRIX_COLUMNS, order_ticket

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
    # Trade plan columns, keyed off tradeplan.MATRIX_COLUMNS so a rename there
    # can't silently orphan the format (_fmt_cell fails open to plain escaping).
    MATRIX_COLUMNS["entry"]: "{:,.2f}", MATRIX_COLUMNS["stop"]: "{:,.2f}",
    MATRIX_COLUMNS["target"]: "{:,.2f}", MATRIX_COLUMNS["rr"]: "{:.2f}",
    MATRIX_COLUMNS["shares"]: "{:,.0f}", MATRIX_COLUMNS["risk_amount"]: "{:,.0f}",
    MATRIX_COLUMNS["adv_dollar"]: "{:,.0f}",
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


def _plain_cell(value, col: str, row) -> str:
    """Default cell: the value through ``_NUMBER_FORMATS``, no styling."""
    return f"<td>{_fmt_cell(col, value)}</td>"


def _screener_cell(value, col: str, row) -> str:
    """Screener metric cell, highlighted when its Pass_* flag is set."""
    text = _fmt_cell(col, value)
    pass_col = _SCREENER_PASS_COLS.get(col)
    if pass_col and bool(row.get(pass_col, False)):
        return (f'<td style="background:{_SCREENER_PASS_BG};'
                f'color:{_contrast_text(_SCREENER_PASS_BG)}">{text}</td>')
    return f"<td>{text}</td>"


def _floor_cell(floor: float):
    """A cell coloured when its value falls below ``floor`` (a thin R:R, a thin
    dollar volume) — flagged rather than hidden, so the trader still sees the
    plan and decides."""
    def cell(value, col: str, row) -> str:
        if _is_missing(value) or value >= floor:
            return f"<td>{_fmt_cell(col, value)}</td>"
        return (f'<td style="color:{_WARN_COLOR};font-weight:600;'
                f'text-align:right">{_fmt_cell(col, value)}</td>')
    return cell


#: Column -> cell renderer ``(value, col, row) -> "<td>...</td>"``. Cell styling is
#: a property of the *column*, not of which table it appears in, so every frame
#: renderer shares one dispatch and a column added to a second table renders the
#: same way for free. Columns absent here fall back to :func:`_plain_cell`.
_CELLS = {
    "Final Action Signal": lambda v, c, r: _action_cell(v),
    "Technical Posture":   lambda v, c, r: _posture_cell(v),
    "Fundamental Score":   lambda v, c, r: _heatmap_cell(v, 6, _GREENS),
    "Tech Score":          lambda v, c, r: _heatmap_cell(v, len(TECHNICAL_COMPONENTS), _GREENS),
    "Composite":           lambda v, c, r: _heatmap_cell(v, 1, _RDYLGN, fmt="{:.2f}"),
}


def _render_frame(df: pd.DataFrame, columns: list[str], *, cells: dict | None = None,
                  headers: dict | None = None) -> str:
    """Render ``df`` as an HTML table over the ``columns`` allow-list.

    ``columns`` is intersected with the frame's actual columns (so a partial
    frame degrades to fewer columns rather than raising) and also fixes display
    order. ``cells`` overrides :data:`_CELLS` per column; ``headers`` renames
    headers. The shared body of every table section in this module.
    """
    cells = _CELLS if cells is None else cells
    cols = [c for c in columns if c in df.columns]
    hdr = [html.escape((headers or {}).get(c, c)) for c in cols]
    rows = [[cells.get(c, _plain_cell)(row[c], c, row) for c in cols]
            for _, row in df.iterrows()]
    return _table_raw(hdr, rows)


def _render_screener_section(screened_df: pd.DataFrame) -> str:
    if screened_df is None or screened_df.empty:
        return '<p class="empty">No fundamentals passed the screener.</p>'
    df = screened_df.reset_index()
    return _render_frame(df, _SCREENER_COLUMNS, headers=_SCREENER_HEADERS,
                         cells={c: _screener_cell for c in _SCREENER_PASS_COLS})


def _render_technical_screener_section(tech: dict) -> str:
    if not tech:
        return '<p class="empty">No technical data available.</p>'
    tech_max = len(TECHNICAL_COMPONENTS)
    entries = sorted(
        ((ticker, *compute_technical_posture(df)) for ticker, df in tech.items()),
        key=lambda e: (-e[2], e[0]),
    )

    headers = (["Ticker"]
               + [html.escape(_tech_header(c.name, c.predicate)) for c in TECHNICAL_COMPONENTS]
               + ["Tech Score", "Technical Posture", "Patterns"])

    rows = []
    for ticker, posture, score, detail in entries:
        cells = [f"<td>{_esc(ticker)}</td>"]
        for c in TECHNICAL_COMPONENTS:
            if detail.get(c.name, False):
                cells.append(f'<td style="background:{_SCREENER_PASS_BG};'
                             f'color:{_contrast_text(_SCREENER_PASS_BG)};'
                             f'text-align:center">&#10003;</td>')
            else:
                cells.append('<td style="text-align:center">—</td>')
        cells.append(_heatmap_cell(score, tech_max, _GREENS))
        cells.append(_posture_cell(posture))
        # Display only — a chart reading, not part of the score or the gate.
        patterns = ", ".join(f"{p['name']} ({p['status']})" for p in detect_patterns(tech[ticker]))
        cells.append(f"<td>{_esc(patterns or None)}</td>")
        rows.append(cells)
    return _table_raw(headers, rows)


#: Columns of the Trade Plan table, in display order: the plan columns (derived
#: from tradeplan.MATRIX_COLUMNS, so they follow a rename) plus the identifying
#: Ticker/Action and the order ticket built from them. Like _SIGNAL_COLUMNS this
#: is an allow-list — the renderer intersects it with the frame's actual columns.
_PLAN_COLUMNS = (["Ticker", "Final Action Signal"] + list(MATRIX_COLUMNS.values())
                 + ["Next Earnings", "Earnings Soon", "Order"])

#: Colour for a plan value below its configured floor (R:R, dollar volume) — a
#: plan worth flagging, not suppressing: the trader still sees it and decides.
_WARN_COLOR = "#f85149"


def _earnings_soon_cell(value, col: str, row) -> str:
    """Warn when a report lands inside a typical trade — a gap the stop can't cap.
    Shown, not acted on: skipping those entries is an untested rule."""
    if not _is_missing(value) and bool(value):
        days = row.get("Days to Earnings")
        when = f"in {int(days)} sessions" if not _is_missing(days) else "soon"
        return f'<td style="color:{_WARN_COLOR};font-weight:600">earnings {when}</td>'
    return f"<td>{'—' if _is_missing(value) else 'no'}</td>"


def _render_trade_plan_section(signal_matrix: pd.DataFrame, *, account_size: float,
                               risk_pct: float, min_rr: float,
                               min_dollar_volume: float) -> str:
    """The executable half of the report: entry, stop, target, R:R and size.

    Only Buy and Hold rows appear — a Watch name failed the quality test and
    carries no plan. The sizing assumptions are stated inline so a share count is
    never mistaken for a real position.
    """
    if signal_matrix is None or signal_matrix.empty:
        return '<p class="empty">No signals generated.</p>'
    if "Final Action Signal" not in signal_matrix.columns:
        return '<p class="empty">No trade plan available.</p>'

    df = signal_matrix[signal_matrix["Final Action Signal"].isin(("Buy", "Hold"))]
    if df.empty:
        return '<p class="empty">Nothing passed the quality screen.</p>'
    df = df.assign(Order=[order_ticket({key: row.get(col) for key, col in MATRIX_COLUMNS.items()})
                          for _, row in df.iterrows()])

    note = (f'<p class="note">Sized for an account of '
            f'<strong>{account_size:,.0f}</strong> risking '
            f'<strong>{risk_pct:.2%}</strong> per trade. Entry is the last close; '
            f'fills are assumed at the next open. R:R below {min_rr:.1f} and '
            f'average daily dollar volume (ADV) below {min_dollar_volume:,.0f} '
            f'are flagged in red. Each order is the type whose fill the backtest '
            f'assumes: market-on-open entry, a one-cancels-other stop-market / '
            f'limit bracket, and a market-on-close exit on the Exit By date. '
            f'Earnings within {config.EARNINGS_WARN_DAYS} sessions are flagged: '
            f'a gap on the report can open beyond the stop.</p>')
    return note + _render_frame(df, _PLAN_COLUMNS, cells={
        **_CELLS,
        MATRIX_COLUMNS["rr"]: _floor_cell(min_rr),
        MATRIX_COLUMNS["adv_dollar"]: _floor_cell(min_dollar_volume),
        "Earnings Soon": _earnings_soon_cell,
    })


def _render_signal_matrix_section(signal_matrix: pd.DataFrame) -> str:
    if signal_matrix is None or signal_matrix.empty:
        return '<p class="empty">No signals generated.</p>'
    return _render_frame(signal_matrix, _SIGNAL_COLUMNS)


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
    earnings = profile_dict.get("earnings") or []
    if earnings:
        groups.append(_group_html("Earnings Surprise", [
            (_esc(f"{q['surprise']:+.1%}" if pd.notna(q.get("surprise")) else None),
             _esc(q.get("date"))) for q in earnings
        ]))

    return (f'<div class="profile-card">{header}<div class="scores">{score_badges}</div>'
           f'<div class="profile-groups">{"".join(groups)}</div></div>')


def _render_profiles_section(profiles: list[dict]) -> str:
    if not profiles:
        return '<p class="empty">No profiles selected.</p>'
    return "".join(_render_profile_card(p) for p in profiles)


def _signed(value, fmt: str = ".1%", suffix: str = "") -> str:
    return "—" if _is_missing(value) else f"{value:{fmt}}{suffix}"


def _render_macro(macro: dict) -> str:
    """Rates / markets / economy panel — context for a human, not a signal input
    (FRED is revised data, and regime filters are already tested dead)."""
    rates, curve, economy = (macro.get("rates") or [], macro.get("curve"),
                             macro.get("economy") or [])
    parts = ["<h3>Rates &amp; Macro</h3>"]
    if not (rates or economy):
        return parts[0] + '<p class="empty">Macro data unavailable.</p>'
    if rates:
        def chg(r, key):
            return (_signed(r[key], "+.0f", " bps") if r["Unit"] == "bps"
                    else _signed(r[key], "+.2f", "%"))
        parts.append(_table(["Series", "Last", "1W", "1M"],
                            [[_esc(r["Name"]), _esc(f'{r["Last"]:,.2f}'),
                              _esc(chg(r, "1W")), _esc(chg(r, "1M"))] for r in rates]))
    if curve:
        state = "inverted" if curve["inverted"] else "normal"
        parts.append(f'<p><b>Yield curve</b> 10Y − 3M: {curve["slope_bps"]:+.0f} bps ({state})</p>')
    if economy:
        parts.append(_table(["Indicator", "Latest", "Change", "As of"], [
            [_esc(r["Indicator"]),
             _esc(f'{r["Value"]:,.2f}{r["Unit"]}' if r["Unit"] != "k" else f'{r["Value"]:+,.0f}k'),
             _esc(f'{_signed(r["Change"], "+.2f")} {r["Change Label"]}'),
             _esc(r["As of"])] for r in economy]))
        parts.append('<p class="note">Economic data from FRED (latest revised '
                     'values), shown for context only.</p>')
    return "".join(parts)


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

    parts.append(_render_macro(overview_data.get("macro") or {}))

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
.note{color:#8b949e;font-size:0.85rem;margin:0 0 0.6rem}
.dashboard{margin:1rem 0;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:0.5rem}
.profile-card{border:1px solid #30363d;border-radius:8px;padding:1rem;margin:1rem 0;background:#161b22}
.profile-header h3{margin-bottom:0.2rem;color:#f0f6fc}
.profile-header p{color:#8b949e;margin-top:0}
.profile-groups{display:flex;flex-wrap:wrap;gap:1.5rem;margin-top:0.5rem}
.profile-group h4{margin-bottom:0.3rem;color:#f0f6fc}
.profile-group ul{margin:0;padding-left:1.2rem}
"""



def _page(title: str, sections: list, *, generated_at: str, head_extra: str = "",
          header_extra: str = "") -> str:
    """A self-contained report page from ``(anchor, title, html)`` sections — one
    list, so the nav and the sections can't drift apart."""
    nav = "".join(f'<a href="#{a}">{t}</a>' for a, t, _ in sections)
    body = "".join(f'<section id="{a}"><h2>{t}</h2>{c}</section>' for a, t, c in sections)
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{_esc(title)}</title>{head_extra}<style>{_STYLE}</style></head><body>"
            f"<header><h1>{_esc(title)}</h1><p class='generated'>Generated "
            f"{_esc(generated_at)}</p>{header_extra}</header><nav>{nav}</nav>{body}</body></html>")


def build_full_report(
    screened_df: pd.DataFrame,
    signal_matrix: pd.DataFrame,
    tech: dict,
    profiles: list[dict],
    overview_data: dict,
    *,
    selected: list[str],
    generated_at: str,
    account_size: float = config.DEFAULT_ACCOUNT_SIZE,
    risk_pct: float = config.DEFAULT_RISK_PCT,
    min_rr: float = config.MIN_RR,
    min_dollar_volume: float = config.MIN_DOLLAR_VOLUME,
) -> str:
    """Render the full combined report as one self-contained HTML string.

    Pure: no I/O, no network. ``screened_df``/``signal_matrix`` render in
    full (never capped by ``selected``); the Dashboards and Profiles
    sections cover only ``selected`` (the top-N picks). ``profiles`` is the
    pre-fetched list of :func:`stockanalysis.profile.build_profile` dicts
    for ``selected``, in the same order; ``overview_data`` is
    :func:`stockanalysis.overview.daily_overview`'s return dict.

    ``account_size``/``risk_pct``/``min_rr``/``min_dollar_volume`` are
    presentation-only here: the share counts were already computed upstream by
    :func:`stockanalysis.signals.generate_signals`. They are passed so the Trade
    Plan section can state the assumptions it is reporting and flag a thin R:R
    or a thinly traded name.
    """
    # One list, so anchor / title / body can't drift apart. They used to be two
    # lists joined by zip(), which silently dropped a section if you added to one
    # and forgot the other.
    sections = [
        ("screener", "Fundamental Screener",
         _render_screener_section(screened_df)),
        ("tech_screener", "Technical Screener",
         _render_technical_screener_section(tech)),
        ("signals", "Combined Signal Matrix",
         _render_signal_matrix_section(signal_matrix)),
        ("trade_plan", "Trade Plan",
         _render_trade_plan_section(signal_matrix, account_size=account_size,
                                    risk_pct=risk_pct, min_rr=min_rr,
                                    min_dollar_volume=min_dollar_volume)),
        ("dashboards", "Top Technical Dashboards",
         _render_dashboards_section(tech, selected)),
        ("profiles", "Fundamental Profiles",
         _render_profiles_section(profiles)),
        ("overview", "Daily Market Overview",
         _render_overview_section(overview_data)),
    ]
    # plotly.js CDN versions track the bundled JS library, not the plotly.py
    # package version (plotly.__version__) — using the latter 404s the script.
    return _page("Stock Analysis Report", sections, generated_at=generated_at,
                 head_extra=f'<script src="https://cdn.plot.ly/plotly-'
                            f'{get_plotlyjs_version()}.min.js"></script>')


# -----------------------------------------------------------------------------
# Household risk report (stockanalysis.portfolio) — a separate document
# -----------------------------------------------------------------------------

def _cad(value) -> str:
    text = _signed(value, ",.0f")
    return text if _is_missing(value) else f"C${text}"


def _weights_table(frame: pd.DataFrame, key: str, header: str) -> str:
    """A ``key | Value | Weight`` table of an exposure breakdown."""
    return _table([header, "Value", "Weight"], [
        [_esc(getattr(r, key)), _cad(r.value_cad), _signed(r.weight)] for r in frame.itertuples()])


def _render_exposure(ex: dict) -> str:
    rows = ex["holdings"]
    held = _table(["Holding", "Name", "Sector", "Value", "Weight", "Accounts"], [
        [_esc(r.name), _esc(r.label), _esc(r.sector), _cad(r.value_cad), _signed(r.weight),
         _esc(int(r.accounts))] for r in rows.itertuples()])
    sectors = _weights_table(ex["sectors"], "sector", "Sector")
    ccy = _weights_table(ex["currencies"], "currency", "Currency")
    accounts = _table(["Account", "Value", "Weight", "Cash"], [
        [_esc(r.account), _cad(r.value_cad), _signed(r.weight), _cad(r.cash_cad)]
        for r in ex["accounts"].itertuples()])
    note = ""
    if (ex["sectors"]["sector"] == "Index fund").any():
        note = ('<p class="note">Index fund rows (e.g. a Nasdaq-100 fund) hold many of the '
                "same large caps you own directly, so the real overlap is larger than these "
                "per-holding weights show; the risk shares under Market Risk capture it.</p>")
    return (f"<p>Cash {_signed(ex['cash_weight'])} · top five holdings (excluding cash) "
            f"{_signed(ex['top5_weight'])}</p>{held}{note}<h3>By sector</h3>{sectors}"
            f"<h3>By currency</h3>{ccy}<h3>By account</h3>{accounts}")


def _render_market(mr: dict) -> str:
    if not mr.get("n_days"):
        return '<p class="empty">Not enough price history to measure market risk.</p>'
    rows = [["Volatility (annualised)", _signed(mr["vol_ann"]), ""]]
    rows += [[f"Beta to the {_esc(k)} (in CAD)", f"{v:.2f}", ""] for k, v in mr["beta"].items()]
    for label, key in (("1-day loss, 1 day in 20 (VaR 95%)", "var_1d"),
                       ("1-day average loss beyond that (CVaR)", "cvar_1d"),
                       ("1-month loss, 1 month in 20 (VaR 95%)", "var_21d"),
                       ("1-month average loss beyond that (CVaR)", "cvar_21d")):
        rows.append([label, _signed(mr[key], ".2%"), _cad(mr.get(f"{key}_cad"))])
    rows.append(["Average correlation between holdings", _signed(mr["avg_corr"], ".2f"), ""])
    contrib = _table(["Holding", "Share of value", "Share of risk"], [
        [_esc(r.name), _signed(r.weight), _signed(r.risk_share)] for r in mr["contributions"].itertuples()])
    short = (f'<p class="note">Less than 90% of the window priced (missing days counted '
             f'flat): {_esc(", ".join(mr["short_history"]))}.</p>' if mr["short_history"] else "")
    return (f'<p class="note">Today\'s portfolio replayed over {mr["n_days"]} trading days '
            f'({_esc(mr["start"].date())} to {_esc(mr["end"].date())}), in CAD, so USD/CAD '
            f"moves count. CAD cash and unpriced holdings are counted flat.</p>"
            f"{_table(['Measure', 'Value', 'In CAD'], rows)}{short}"
            f"<h3>Where the risk comes from</h3>{contrib}")


def _render_stress(results: list) -> str:
    if not results:
        return '<p class="empty">No stress scenarios.</p>'
    benches = list(dict.fromkeys(b for r in results for b in r["bench"]))
    rows = []
    for r in results:
        notes = []
        if r["proxied"]:
            notes.append("stand-ins: " + ", ".join(f"{k} → {v}" for k, v in r["proxied"].items()))
        if r["missing"]:
            notes.append("not in the data: " + ", ".join(r["missing"]))
        rows.append([_esc(r["scenario"]), f'{r["start"].date()} → {r["end"].date()}',
                     _signed(r["return"], "+.1%"), _cad(r["loss_cad"]),
                     *[_signed(r["bench"].get(b), "+.1%") for b in benches],
                     _signed(r["covered_weight"]), _esc("; ".join(notes) or None)])
    return ('<p class="note">Today\'s weights run through past crashes, in CAD. "Measured" is '
            "the share of the portfolio with prices for that window; the rest counts as flat.</p>"
            + _table(["Scenario", "Window", "Portfolio", "Change",
                      *[f"{b} (CAD)" for b in benches], "Measured", "Notes"], rows))


def _render_drawdown(dd) -> str:
    if not dd:
        return ('<p class="empty">No account history. Pass the sheet\'s .xlsx export '
                "(History and Touzi tabs) to measure the real account.</p>")
    rows = [
        ["Period", f'{dd["start"].date()} → {dd["end"].date()}'],
        ["Time-weighted return", _signed(dd["twr"], "+.1%")],
        ["Worst drawdown", f'{_signed(dd["max_drawdown"])} ({dd["peak_date"].date()} → '
                           f'{dd["trough_date"].date()})'],
        ["Current drawdown", _signed(dd["current_drawdown"])],
        ["Worst drawdown of the raw balance", _signed(dd["raw_max_drawdown"])],
        ["New money added in the period", _cad(dd["contributions_cad"])],
    ]
    return ('<p class="note">From the sheet\'s daily History, with contributions removed: '
            "new money lifts the balance without any gain, so the raw balance understates "
            "losses.</p>" + _table(["Measure", "Value"], rows))


def _render_stops(st: dict) -> str:
    rows = st["rows"]
    table = (_table(["Holding", "Last", "Stop", "Drop to stop", "Loss"], [
        [_esc(r.name), f"{r.last:,.2f}", f"{r.stop:,.2f}", _signed(r.drop), _cad(r.loss_cad)]
        for r in rows.itertuples()]) if not rows.empty else "")
    missing = (f'<p class="note">No stop (no price data or no support level): '
               f'{_esc(", ".join(st["no_stop"]))}.</p>' if st["no_stop"] else "")
    return (f"<p>If every holding fell to its trade-plan stop: <b>{_cad(st['total_cad'])}</b>, "
            f"{_signed(st['heat'])} of the portfolio.</p>{table}{missing}")


def _render_funding(fund: dict, signals_source: str | None) -> str:
    cash = _table(["Account", "Currency", "Cash"], [
        [_esc(r.account), _esc(r.currency), f"{r.cash:,.0f}"] for r in fund["cash"].itertuples()])
    margin = (f'<p class="note">Margin account(s): {_esc(", ".join(fund["margin_accounts"]))}. '
              "The registered accounts (RRSP, TFSA, RESP) can't borrow.</p>"
              if fund["margin_accounts"] else "")
    buys = fund["buys"]
    if signals_source is None:
        plan = ('<p class="empty">No signal matrix checked: run the pipeline with '
                "<code>--risk</code>, or <code>stock-analysis risk --from-run "
                "&lt;run folder&gt;</code>.</p>")
    elif buys.empty:
        plan = f'<p class="empty">No Buy signals in {_esc(signals_source)}.</p>'
    else:
        plan = f"<h3>Buys in {_esc(signals_source)}</h3>" + _table(
            ["Ticker", "Shares", "Entry", "Cost", "Cost (CAD)", "Accounts with the cash"],
            [[_esc(r.Ticker), f"{r.Shares:,.0f}", f"{r.Entry:,.2f}",
              f"{r.currency} {r.notional:,.0f}", _cad(r.notional_cad),
              _esc(", ".join(r.accounts) or None)] for r in buys.itertuples()])
    return f"{cash}{margin}{plan}"


def build_risk_report(risk: dict, *, generated_at: str, source: str,
                      signals_source: str | None = None) -> str:
    """Self-contained HTML for :func:`stockanalysis.portfolio.build_risk`'s result.

    Pure, like :func:`build_full_report`. ``source`` names where the holdings
    came from (and when), so a stale snapshot is visible; ``signals_source``
    names the run whose Buys were checked against cash (None: none were).
    """
    ex = risk["exposure"]
    flags = ex["flags"] + risk["funding"]["flags"]
    flag_html = ("<ul class='flags'>" + "".join(f"<li>{_esc(f)}</li>" for f in flags) + "</ul>"
                 if flags else "")
    as_of = risk.get("as_of")
    summary = (f"<p><b>Total {_cad(ex['total_cad'])}</b> · holdings from {_esc(source)} · "
               f"prices as of {_esc(as_of.date() if as_of is not None else None)}</p>"
               f"{flag_html}"
               '<p class="note">Display only: this measures what is held and is not an input '
               "to any signal.</p>")
    sections = [
        ("exposure", "Exposure", _render_exposure(ex)),
        ("market", "Market Risk", _render_market(risk["market"])),
        ("stress", "Stress Tests", _render_stress(risk["stress"])),
        ("drawdown", "Account Drawdown", _render_drawdown(risk["drawdown"])),
        ("stops", "Loss to Stops", _render_stops(risk["stops"])),
        ("funding", "Cash & Funding", _render_funding(risk["funding"], signals_source)),
    ]
    return _page("Portfolio Risk", sections, generated_at=generated_at, header_extra=summary)


def save_report(html_doc: str, path) -> str:
    """Write ``html_doc`` to ``path`` as UTF-8, creating parent dirs. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
    return str(path)
