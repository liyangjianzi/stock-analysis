"""Retirement report: one self-contained HTML page from a PlanResult.

``build_report`` is pure (it returns the HTML string), like
:func:`stockanalysis.report.build_full_report`. ``save_report``,
``write_summary`` and ``latest_summary`` are the only I/O.

Colours are the dataviz reference palette in its fixed categorical order. It
was validated on the light surface: every adjacent pair clears the CVD and
normal-vision floors, and the three sub-3:1 slots are relieved by the
year-by-year table view. The shortfall layer uses the reserved status-critical
red and always carries a ⚠ label.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

from ..report import save_report  # noqa: F401  (re-exported: the same writer as the other reports)
from . import engine, rules
from .engine import SOURCES, PlanResult, Projection
from .inputs import STRATEGY_LABELS

SURFACE, PAGE = "#fcfcfb", "#f9f9f7"
INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7")
CRITICAL, GOOD_TEXT, TRACK, BAND = "#d03b3b", "#006300", "#cde2fb", "rgba(42,120,214,0.15)"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
LABELS = {"earned": "Earned income", "cpp": "CPP", "oas": "OAS", "minimums": "RRIF/LIF minimums",
          "registered": "Registered", "tfsa": "TFSA", "nonreg": "Non-registered",
          "shortfall": "⚠ Shortfall"}
COLORS = {**dict(zip(SOURCES[:-1], SERIES)), "shortfall": CRITICAL}
SECTION_IDS = ("summary", "suggestions", "income", "money-left", "years", "assumptions")

_STYLE = f"""
body{{margin:0;background:{PAGE};color:{INK};font-family:{FONT}}}
header,section{{max-width:1180px;margin:0 auto;padding:12px 24px}}
h1{{font-size:1.5rem;margin:.6rem 0 .2rem}} h2{{font-size:1.1rem;margin:1.2rem 0 .5rem}}
.note,.generated{{color:{INK_2};font-size:.85rem}}
.banner{{background:#fff4e5;border:1px solid {AXIS};padding:8px 12px;border-radius:6px}}
.top{{display:grid;grid-template-columns:300px 240px 1fr;gap:16px;align-items:stretch}}
.card,.tile{{background:{SURFACE};border:1px solid rgba(11,11,11,.10);border-radius:10px;padding:12px}}
.tile .label{{color:{INK_2};font-size:.85rem}} .tile .value{{font-size:1.6rem;font-weight:600}}
.tile.hero .value{{font-size:3rem}} .tile .sub{{color:{MUTED};font-size:.8rem}}
.kpis{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
table{{border-collapse:collapse;width:100%;background:{SURFACE};font-size:.85rem}}
th,td{{padding:6px 8px;border-bottom:1px solid {GRID};text-align:right}}
th:first-child,td:first-child{{text-align:left}} td{{font-variant-numeric:tabular-nums}}
.up{{color:{GOOD_TEXT}}} .down{{color:{CRITICAL}}} .flat{{color:{INK_2}}} tr.base td{{font-weight:600}}
details summary{{cursor:pointer;font-weight:600;margin:.8rem 0}} .years{{overflow-x:auto}}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _money(x) -> str:
    x = float(x)
    return f"-C${abs(x):,.0f}" if x < 0 else f"C${x:,.0f}"


def _short(x) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"C${x / 1e6:.2f}M"
    if abs(x) >= 1e3:
        return f"C${x / 1e3:.0f}k"
    return _money(x)


def age_label(ages) -> str:
    return "/".join(str(a) for a in ages)


def _fig(fig: go.Figure, include) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=include,
                       config={"displaylogo": False, "responsive": True})


def _style_axes(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      font=dict(family=FONT, color=INK_2, size=13),
                      margin=dict(l=70, r=20, t=50, b=40),
                      hoverlabel=dict(bgcolor="white", font=dict(family=FONT, color=INK)))
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED),
                     tickprefix="C$", tickformat="~s")
    return fig


def success_meter(success: float, previous: float | None = None) -> go.Figure:
    if previous is not None and round(previous * 100) == round(success * 100):
        previous = None                 # a "0 pts" delta is noise, not news
    delta = None if previous is None else {
        "reference": round(previous * 100), "suffix": " pts",
        "increasing": {"color": GOOD_TEXT}, "decreasing": {"color": CRITICAL}}
    fig = go.Figure(go.Indicator(
        mode="gauge+number" + ("" if delta is None else "+delta"),
        value=round(success * 100), delta=delta,
        number={"suffix": "%", "font": {"size": 48, "color": INK}},
        title={"text": "Chance the money lasts", "font": {"size": 14, "color": INK_2}},
        gauge={"axis": {"range": [0, 100], "tickcolor": MUTED, "tickfont": {"color": MUTED}},
               "bar": {"color": SERIES[0], "thickness": 0.35}, "bgcolor": TRACK, "borderwidth": 0}))
    fig.update_layout(height=240, paper_bgcolor=SURFACE, font=dict(family=FONT, color=INK_2),
                      margin=dict(l=30, r=30, t=50, b=10))
    return fig


def income_chart(avg: Projection, bad: Projection) -> go.Figure:
    """Stacked income by source per year; buttons switch the average / bad-luck future."""
    fig = go.Figure()
    for proj, visible in ((avg, True), (bad, False)):
        x, zeros = proj.years, [0] * len(proj.years)
        fig.add_trace(go.Scatter(x=x, y=zeros, mode="markers", marker=dict(opacity=0),
                                 visible=visible, showlegend=False, name="Ages",
                                 customdata=[age_label(a) for a in proj.ages],
                                 hovertemplate="Ages %{customdata}<extra></extra>"))
        for s in SOURCES:
            fig.add_trace(go.Bar(x=x, y=proj.income[s][:, 0], name=LABELS[s], visible=visible,
                                 marker=dict(color=COLORS[s], line=dict(color=SURFACE, width=1)),
                                 hovertemplate=f"{LABELS[s]}: C$%{{y:,.0f}}<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=zeros, mode="markers", marker=dict(opacity=0),
                                 visible=visible, showlegend=False, name="Income tax",
                                 customdata=proj.tax[:, 0],
                                 hovertemplate="Income tax: C$%{customdata:,.0f}<extra></extra>"))
    per_set = len(SOURCES) + 2

    def show(average_first: bool) -> list:
        return [average_first] * per_set + [not average_first] * per_set

    fig.update_layout(
        barmode="stack", bargap=0.15, barcornerradius=4, hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.3, x=0, traceorder="normal",
                    font=dict(color=INK_2)),
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left", y=1.08,
                          yanchor="bottom", showactive=True, bgcolor=SURFACE, bordercolor=AXIS,
                          font=dict(color=INK),
                          buttons=[dict(label="Average future", method="update",
                                        args=[{"visible": show(True)}]),
                                   dict(label="Bad-luck future", method="update",
                                        args=[{"visible": show(False)}])])])
    _style_axes(fig, 540)
    idx = list(range(0, len(avg.years), 5))
    fig.update_xaxes(tickvals=[avg.years[i] for i in idx], ticktext=[age_label(avg.ages[i]) for i in idx],
                     title_text="Ages", rangeslider=dict(visible=True, thickness=0.06))
    return fig


def money_left_chart(sim: Projection, plan) -> go.Figure:
    """Investments by age as a bad / typical / good band, plus the home's value."""
    ref = plan.people[0]
    x = [ref.age + t for t in range(sim.investments.shape[0])]
    p10, p50, p90 = np.percentile(sim.investments, [10, 50, 90], axis=1)
    fig = go.Figure([
        go.Scatter(x=x, y=p90, name="Good luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   hovertemplate="Good luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p10, name="Bad luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   fill="tonexty", fillcolor=BAND, hovertemplate="Bad luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p50, name="Typical", line=dict(color=SERIES[0], width=2),
                   hovertemplate="Typical: C$%{y:,.0f}<extra></extra>"),
    ])
    if plan.home is not None:
        fig.add_trace(go.Scatter(x=x, y=sim.home_value, name="Home value",
                                 line=dict(color=SERIES[1], width=2, dash="dot"),
                                 hovertemplate="Home: C$%{y:,.0f}<extra></extra>"))
    def at(p, age):                    # p's age -> the chart's x (people[0]'s age)
        return ref.age + (age - p.age)

    marks = [(at(p, p.retire_age), f"{p.name}: retires") for p in plan.people]
    if plan.home is not None and plan.home.downsize_age is not None:
        marks.append((plan.home.downsize_age, "Downsize"))
    for p in plan.people:
        if p.cpp_start_age == p.oas_start_age:
            marks.append((at(p, p.cpp_start_age), f"{p.name}: CPP + OAS"))
        else:
            marks += [(at(p, p.cpp_start_age), f"{p.name}: CPP"), (at(p, p.oas_start_age), f"{p.name}: OAS")]
    grouped: dict = {}                 # one label per age, so marks never print on top of each other
    for age, label in marks:
        if x[0] <= age <= x[-1]:
            grouped.setdefault(age, []).append(label)
    for i, age in enumerate(sorted(grouped)):
        fig.add_vline(x=age, line=dict(color=AXIS, width=1, dash="dash"))
        fig.add_annotation(x=age, y=1.0, yref="paper", text="<br>".join(grouped[age]),
                           showarrow=False, yanchor="bottom", yshift=4 + 30 * (i % 3),
                           font=dict(color=MUTED, size=11))
    _style_axes(fig, 480)
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=-0.2),
                      margin=dict(l=70, r=20, t=120, b=40))
    fig.update_xaxes(title_text=f"Age of {ref.name}")
    return fig


def _legacy_tile(plan, avg: Projection, bad: Projection) -> str:
    return (f"<div class='tile hero'><div class='label'>Legacy at {plan.end_age} "
            f"(after tax, home included)</div><div class='value'>{_short(avg.legacy[0])}</div>"
            f"<div class='sub'>Bad luck: {_short(bad.legacy[0])}</div></div>")


def _kpis(result: PlanResult) -> str:
    avg, bad, r = result.average, result.bad_luck, result.inputs.returns
    tiles = [
        ("Short years", f"{int(avg.shortfall_years[0])}", f"Bad luck: {int(bad.shortfall_years[0])}"),
        ("Investments at retirement", _short(avg.investments_at_retirement[0]),
         f"Bad luck: {_short(bad.investments_at_retirement[0])}"),
        ("Rate of return", f"{result.average_return:.2%}",
         f"After inflation; {r.mean:.0%} average with ups and downs"),
        ("Lifetime taxes", _short(avg.lifetime_tax[0]), "Income tax plus tax at death"),
    ]
    return "<div class='kpis'>" + "".join(
        f"<div class='tile'><div class='label'>{_esc(a)}</div><div class='value'>{_esc(b)}</div>"
        f"<div class='sub'>{_esc(c)}</div></div>" for a, b, c in tiles) + "</div>"


def _suggestions(baseline, suggestions) -> str:
    def row(s, base: bool) -> str:
        if base:
            cls, change = "flat", "—"
        else:
            cls = "up" if s.delta > 0.0005 else "down" if s.delta < -0.0005 else "flat"
            arrow = {"up": "▲", "down": "▼", "flat": "•"}[cls]
            change = f"{arrow} {s.delta * 100:+.1f} pts"
        attr = ' class="base"' if base else ""
        return (f"<tr{attr}><td>{_esc(s.label)}</td><td>{s.success:.0%}</td>"
                f"<td class='{cls}'>{_esc(change)}</td><td>{_money(s.lifetime_tax)}</td>"
                f"<td>{_money(s.legacy)}</td></tr>")

    head = ("<tr><th>Suggestion</th><th>Chance of success</th><th>Change</th>"
            "<th>Lifetime tax</th><th>Legacy</th></tr>")
    body = row(baseline, True) + "".join(row(s, False) for s in suggestions)
    return ("<p class='note'>Each line reruns the plan with one change, on the same random "
            "futures, so the differences are the change's effect.</p>"
            f"<table><thead>{head}</thead><tbody>{body}</tbody></table>")


def _year_table(proj: Projection) -> str:
    head = ["Year", "Ages", "Spending", "Tax", *[LABELS[s] for s in SOURCES], "Saved",
            "RRSP/RRIF", "Pension/LIF", "TFSA", "Non-registered", "Total"]
    rows = []
    for t, year in enumerate(proj.years):
        cells = [year, age_label(proj.ages[t]), _money(proj.need[t, 0]), _money(proj.tax[t, 0]),
                 *[_money(proj.income[s][t, 0]) for s in SOURCES], _money(proj.saved[t, 0]),
                 *[_money(proj.balances[k][t, 0]) for k in ("rrsp", "pension", "tfsa", "nonreg")],
                 _money(proj.investments[t, 0])]
        rows.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    return ("<div class='years'><table><thead><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in head)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _rule_value(value) -> str:
    text = str(value).replace("inf", "∞")
    return text if len(text) <= 140 else text[:137] + "…"


def _assumptions(plan, result: PlanResult, holdings_source: str | None) -> str:
    people = "".join(
        f"<tr><td>{_esc(p.name)}</td><td>{p.age}</td><td>{p.retire_age}</td>"
        f"<td>{p.cpp_start_age}</td><td>{p.oas_start_age}</td><td>{_money(engine.cpp_at_65(p))}</td>"
        f"<td>{p.years_in_canada_at_65:g}</td><td>{p.rrif_start_age}</td>"
        f"<td>{_esc(p.lif_start_age if p.lif_start_age is not None else 'at retirement (50+)')}</td>"
        f"<td>{_money(p.tfsa_room)}</td></tr>"
        for p in plan.people)
    s, h, r = plan.spending, plan.home, plan.returns
    facts = [
        ("Withdrawal order", STRATEGY_LABELS[plan.withdrawal.strategy]),
        ("Returns", f"{r.mean:.1%} average, {r.sd:.0%} yearly swings; typical "
                    f"{result.average_return:.2%} a year after inflation"),
        ("Spending", f"{_money(s.base)} a year after tax; slow-go from {s.slow_go_age} "
                     f"({s.slow_go_share:.0%}), no-go from {s.no_go_age} ({s.no_go_share:.0%}) "
                     f"plus {_money(s.care)} care"),
        ("Bad-market rule", f"cut {s.bad_market_cut:.0%} when investments are below "
                            f"{s.bad_market_trigger:.0%} of their value on retirement day"),
        ("Home", "none" if h is None else (
            f"{_money(h.value)}; downsize at {h.downsize_age} to {_money(h.new_value)}"
            if h.downsize_age is not None else f"{_money(h.value)}; never sold")),
        ("Balances from", holdings_source or "plan.json"),
        ("Futures simulated", f"{result.simulated.paths:,}"),
    ]
    rules_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(_rule_value(rule.value))}</td><td>{rule.year}</td>"
        f"<td><a href='{_esc(rule.source)}'>source</a></td></tr>" for name, rule in rules.all_rules())
    return (
        "<table><thead><tr><th>Person</th><th>Age</th><th>Retires</th><th>CPP from</th>"
        "<th>OAS from</th><th>CPP at 65 (yearly)</th><th>Years in Canada at 65</th>"
        f"<th>RRIF from</th><th>LIF from</th><th>TFSA room carried in</th></tr></thead>"
        f"<tbody>{people}</tbody></table>"
        "<table><tbody>" + "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in facts)
        + "</tbody></table><h2>Canadian rules used</h2>"
        "<table><thead><tr><th>Rule</th><th>Value</th><th>Year</th><th>Official source</th></tr>"
        f"</thead><tbody>{rules_rows}</tbody></table>")


def build_report(result: PlanResult, baseline, suggestions, *, generated_at: str,
                 holdings_source: str | None = None, previous: dict | None = None,
                 today: dt.date | None = None) -> str:
    """The whole page as a string. ``previous`` is the last run's summary, for the change."""
    plan, sim, avg, bad = result.inputs, result.simulated, result.average, result.bad_luck
    today = today or dt.date.today()
    banner = ""
    if rules.is_stale(today.year):
        banner = (f"<p class='banner'>⚠ The tax and benefit rules are for {rules.TAX_YEAR}. "
                  f"Update rules.py for {today.year} from the official pages it cites.</p>")
    prev = previous.get("success") if previous else None
    top = (f"<div class='top'><div class='card'>{_fig(success_meter(sim.success, prev), 'cdn')}</div>"
           f"{_legacy_tile(plan, avg, bad)}{_kpis(result)}</div>")
    sections = [
        ("summary", "", top),
        ("suggestions", "Expert planning", _suggestions(baseline, suggestions)),
        ("income", "Detailed income projection",
         "<p class='note'>Each bar is one year's spending plus income tax, by where the money "
         "comes from. Switch to the bad-luck future (the 1-in-10 bad run of returns) to see "
         "short years in red.</p>" + _fig(income_chart(avg, bad), False)),
        ("money-left", "Money left",
         "<p class='note'>Investments only, in today's dollars; the home is the dotted line.</p>"
         + _fig(money_left_chart(sim, plan), False)),
        ("years", "", "<details><summary>Year-by-year table (average future)</summary>"
                      f"{_year_table(avg)}</details>"),
        ("assumptions", "Assumptions &amp; rules", _assumptions(plan, result, holdings_source)),
    ]
    body = "".join(f'<section id="{sid}">' + (f"<h2>{title}</h2>" if title else "") + content
                   + "</section>" for sid, title, content in sections)
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Retirement plan</title><style>{_STYLE}</style></head><body>"
            f"<header><h1>Retirement plan</h1><p class='generated'>Generated {_esc(generated_at)} · "
            f"{sim.paths:,} simulated futures · all amounts in today's dollars (CAD)</p>{banner}"
            f"</header>{body}</body></html>")


def headline(result: PlanResult) -> dict:
    """The run's headline numbers (simulated success, the average future's money)."""
    avg = result.average
    return {"success": result.simulated.success, "paths": result.simulated.paths,
            "legacy": float(avg.legacy[0]), "lifetime_tax": float(avg.lifetime_tax[0]),
            "investments_at_retirement": float(avg.investments_at_retirement[0])}


def write_summary(result: PlanResult, path) -> str:
    """The run's headline numbers, so the next run can show the change."""
    data = {"generated": dt.datetime.now().isoformat(timespec="seconds"), **headline(result)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


def latest_summary(root) -> dict | None:
    """The newest ``<root>/<timestamp>/summary.json``, or None."""
    root = Path(root)
    found = sorted(root.glob("*/summary.json")) if root.is_dir() else []
    return json.loads(found[-1].read_text(encoding="utf-8")) if found else None
