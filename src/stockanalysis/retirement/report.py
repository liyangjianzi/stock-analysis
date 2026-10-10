"""Retirement report: one self-contained HTML page from a PlanResult, the headline on
top and the rest in tabs (``TABS``; each keeps its section ids, print shows all).

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
import warnings
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

from ..report import save_report  # noqa: F401  (re-exported: the same writer as the other reports)
from . import actions, engine, mortality, refund, rules
from .engine import SOURCES, PlanResult, Projection
from .inputs import STRATEGY_LABELS, event_span

SURFACE, PAGE = "#fcfcfb", "#f9f9f7"
INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7")
CRITICAL, GOOD_TEXT, TRACK, BAND = "#d03b3b", "#006300", "#cde2fb", "rgba(42,120,214,0.15)"
PLOTLY_CONFIG = {"displaylogo": False, "responsive": True}   # the report's, the GUI's and theme.js's
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
LABELS = {"earned": "Earned income", "cpp": "CPP", "oas": "OAS", "minimums": "RRIF/LIF minimums",
          "registered": "Registered", "tfsa": "TFSA", "nonreg": "Non-registered",
          "ccb": "Child benefit", "other": "One-time money", "shortfall": "⚠ Shortfall"}
COLORS = {**dict(zip(SOURCES[:-1], SERIES)), "ccb": MUTED, "other": AXIS, "shortfall": CRITICAL}
SHORT = {"earned": "Earned", "cpp": "CPP", "oas": "OAS", "minimums": "RRIF min",   # year-table headings
         "registered": "Reg", "tfsa": "TFSA", "nonreg": "Non-reg", "ccb": "CCB", "other": "Events",
         "shortfall": "⚠ Short"}
DRAWN = ("registered", "tfsa", "nonreg")   # year-table sources that are withdrawals from an account
LONG = {**{SHORT[k]: LABELS[k] + (" drawn" if k in DRAWN else "")
           for k in SHORT}, "Total": "Total sources", "Spend": "Spending", "Jan 1": "January 1",
        "Other": "Added without passing through cash"}   # each heading's hover
DRAWN_FROM = ("minimums", *DRAWN)          # every source that leaves the investments
ACTIONS_ID = "actions"
NET_WORTH_ID = "net-worth"
SECTION_IDS = ("summary", ACTIONS_ID, "suggestions", "income", "money-left", NET_WORTH_ID, "years",
               "assumptions")
EDUCATION_ID = "education"          # only when the plan has children's education
SAVED_ID = "saved"                  # only when the plan has saved scenarios
DOLLARS_ID = "dollars"              # the today's / future dollars switch
TABS = (("tab-plan", "Plan", (ACTIONS_ID,)),          # (panel id, button, sections it holds);
        ("tab-options", "Options", ("suggestions", SAVED_ID)),     # the summary stays above them
        ("tab-education", "Children's education", (EDUCATION_ID,)),   # only with kids
        ("tab-income", "Income projection", ("income",)),
        ("tab-net-worth", "Net worth", (NET_WORTH_ID, "money-left")),   # the futures' range below
        ("tab-years", "Cash flow", ("years",)),
        ("tab-details", "Details", ("assumptions",)))

DARK_SERIES = ("#5a9cec", "#f07c4c", "#2ec08a", "#f0b232", "#ee8fb2", "#3fae3f", "#8c7fe6")
# Dark mode. Each CSS variable is (light, dark); the light values are the constants above.
THEME = {"surface": (SURFACE, "#1d1d1b"), "page": (PAGE, "#141413"), "ink": (INK, "#ecebe6"),
         "ink2": (INK_2, "#b8b6ae"), "muted": (MUTED, "#8f8d86"), "grid": (GRID, "#2f2e2b"),
         "axis": (AXIS, "#4c4b46"), "blue": (SERIES[0], DARK_SERIES[0]), "track": (TRACK, "#1e3554"),
         "good": (GOOD_TEXT, "#5fbf5f"), "bad": (CRITICAL, "#ef6461"), "amber": (SERIES[3], DARK_SERIES[3]),
         "field": ("#ffffff", "#262624"), "line": ("rgba(11,11,11,.10)", "rgba(255,255,255,.12)"),
         "warn-bg": ("#fff4e5", "#3a2f17"), "bad-bg": ("#fff3f3", "#3b2020"),
         "short-bg": ("#fbe9e9", "#432222")}
# Light -> dark for the colours the figures carry (Plotly can't read CSS variables). Every value
# is distinct from every key, so theme.js can swap a drawn chart either way.
CHART_DARK = {**{THEME[k][0]: THEME[k][1] for k in ("surface", "ink", "ink2", "muted", "grid", "axis",
                                                     "track", "good", "bad")},
              **dict(zip(SERIES, DARK_SERIES)), BAND: "rgba({},{},{},0.22)".format(*bytes.fromhex(DARK_SERIES[0][1:]))}


def _vars(i: int) -> str:
    return ";".join(f"--{k}:{v[i]}" for k, v in THEME.items())


# theme.js sets data-theme to the theme in effect, from <head>, before the page draws.
THEME_CSS = (f":root{{{_vars(0)};--font:{FONT};color-scheme:light}}\n"
             f":root[data-theme=dark]{{{_vars(1)};color-scheme:dark}}\n"
             # Plotly fills an active or hovered menu button with a fixed #F4FAFF.
             ":root[data-theme=dark] .updatemenu-item-rect[style*='244, 250, 255']"
             "{fill:var(--track)!important}\n"
             "#theme{font:inherit;font-size:.8rem;border:1px solid var(--axis);background:var(--field);"
             "color:var(--ink2);border-radius:6px;padding:4px 10px;cursor:pointer}\n")
# Auto (the system setting) -> Dark -> Light, remembered per browser. The figures are built light;
# window.theme.draw(gd, data, layout) recolours one for the theme in effect and records it in
# gd.dataset.theme, so a theme change redraws only the charts drawn in the other one. The
# button is added to the page's <header>, before its first button.
THEME_JS = """(function(){
const PAIRS=__PAIRS__,CFG=__CFG__,KEY="retirement-theme",NEXT={auto:"dark",dark:"light",light:"auto"},
  mq=matchMedia("(prefers-color-scheme: dark)"),toDark=new Map(PAIRS),toLight=new Map(PAIRS.map(([l,d])=>[d,l]));
const choice=()=>{try{return localStorage.getItem(KEY)||"auto"}catch(e){return "auto"}};
const mode=()=>choice()==="dark"||(choice()==="auto"&&mq.matches)?"dark":"light";
function swap(v,m){
  if(typeof v==="string")return m.get(v.toLowerCase())||v;
  if(Array.isArray(v))return v.map(x=>swap(x,m));
  if(v&&typeof v==="object"&&!ArrayBuffer.isView(v)){const o={};for(const k in v)o[k]=swap(v[k],m);return o}
  return v}
function draw(gd,data,layout){
  const t=mode(),m=t==="dark"?toDark:toLight;
  Plotly.react(gd,swap(data,m),swap(layout,m),CFG);gd.dataset.theme=t}
function button(){
  let b=document.getElementById("theme");const h=document.querySelector("header");
  if(!b&&h){b=document.createElement("button");Object.assign(b,{id:"theme",type:"button",
    title:"Auto follows the system setting",onclick:toggle});h.insertBefore(b,h.querySelector(":scope>button"))}
  if(b)b.textContent="Theme: "+choice()}
function apply(){
  const t=mode();document.documentElement.dataset.theme=t;button();
  if(window.Plotly)for(const gd of document.querySelectorAll(".js-plotly-plot"))
    if(gd.data&&(gd.dataset.theme||"light")!==t)draw(gd,gd.data,gd.layout)}
function toggle(){try{localStorage.setItem(KEY,NEXT[choice()])}catch(e){}apply()}
window.theme={draw,toggle};
mq.addEventListener("change",apply);
addEventListener("storage",e=>{if(e.key===KEY)apply()});
document.addEventListener("DOMContentLoaded",apply);
apply()})();
""".replace("__PAIRS__", json.dumps(list(CHART_DARK.items()))).replace("__CFG__", json.dumps(PLOTLY_CONFIG))

_STYLE = """
body{margin:0;background:var(--page);color:var(--ink);font-family:var(--font)}
header,section{max-width:1180px;margin:0 auto;padding:12px 24px}
header{position:relative} header #theme{position:absolute;top:18px;right:24px}
h1{font-size:1.5rem;margin:.6rem 0 .2rem} h2{font-size:1.1rem;margin:1.2rem 0 .5rem}
.note,.generated{color:var(--ink2);font-size:.85rem}
.banner{background:var(--warn-bg);border:1px solid var(--axis);padding:8px 12px;border-radius:6px}
.top{display:grid;grid-template-columns:300px 240px 1fr;gap:16px;align-items:stretch}
.card,.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px}
.tile .label,.meter-title{color:var(--ink2);font-size:.85rem} .meter-title{text-align:center} .tile .value{font-size:1.6rem;font-weight:600}
.tile.hero .value{font-size:3rem} .tile .sub{color:var(--muted);font-size:.8rem}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
table{border-collapse:collapse;width:100%;background:var(--surface);font-size:.85rem}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:right}
th:first-child,td:first-child{text-align:left} td{font-variant-numeric:tabular-nums}
table.actions th,table.actions td{text-align:left;vertical-align:top}
.up{color:var(--good)} .down{color:var(--bad)} .flat{color:var(--ink2)} tr.base td{font-weight:600}
details summary{cursor:pointer;font-weight:600;margin:.8rem 0}
a{color:var(--blue)}
.years{overflow:auto;max-height:70vh;border:1px solid var(--line);border-radius:10px}
.years table{font-size:.78rem;white-space:nowrap}
.years th,.years td{padding:3px 7px}
.years thead{position:sticky;top:0;z-index:2}
.years thead tr{background:var(--page)} .years th{color:var(--ink2);font-weight:600;vertical-align:bottom}
.years th.grp{text-align:center;color:var(--muted);font-weight:500;border-bottom:1px solid var(--axis)}
.years .g{border-left:1px solid var(--axis)}
.years .stick{position:sticky;left:0;z-index:1;background:inherit;text-align:left}
.years tr>.stick:first-child{width:3.2em;min-width:3.2em}
.years .s2{left:calc(3.2em + 14px);border-right:1px solid var(--axis)}
.years tbody tr{background:var(--surface)} .years tbody tr:nth-child(even){background:var(--page)}
.years tbody tr:hover{background:var(--track)} .years tbody tr.short{background:var(--short-bg)}
.years tr.short td:first-child{box-shadow:inset 3px 0 var(--bad)}
body:not(.future) .dollars-future{display:none} body.future .dollars-today{display:none}
.switch{margin:.4rem 0} .switch button{border:1px solid var(--axis);background:var(--field);color:var(--ink);
  padding:4px 10px;border-radius:6px;cursor:pointer}
.switch button.on{background:var(--blue);border-color:var(--blue);color:#fff}
nav.tabs{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--axis);
  max-width:1180px;margin:12px auto 0;padding:8px 24px 0;display:flex;gap:4px}
nav.tabs button{border:1px solid var(--axis);border-bottom:none;background:var(--field);padding:6px 16px;
  border-radius:6px 6px 0 0;cursor:pointer;font:inherit;color:var(--ink2)}
nav.tabs button.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.panel{display:none} .panel.on{display:block}
@media print{.panel{display:block} nav.tabs,.switch,#theme{display:none}}
"""


def future_scale(plan, n: int) -> np.ndarray:
    """Today's dollars → each year's dollars on the average inflation path."""
    return (1 + plan.returns.inflation_rate) ** np.arange(n)


def _scale(scale, n: int) -> np.ndarray:
    """The first ``n`` factors of ``scale``; ones for today's dollars."""
    return np.ones(n) if scale is None else np.asarray(scale)[:n]


def _dollars(scale) -> str:
    """How a section's amounts are measured, for its note."""
    return "today's dollars" if scale is None else "each year's dollars (average inflation)"


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
                       config=PLOTLY_CONFIG)


_AXIS = dict(gridcolor=GRID, linecolor=AXIS, zerolinecolor=GRID, tickcolor=AXIS,
             tickfont=dict(color=MUTED), title=dict(font=dict(color=INK_2)))
# Every colour a figure draws, set from the palette rather than left to Plotly's defaults,
# so CHART_DARK can recolour all of it (a test checks every figure against the map).
CHART_TEMPLATE = go.layout.Template(layout=dict(
    paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, colorway=list(SERIES),
    font=dict(family=FONT, color=INK_2, size=13),
    hoverlabel=dict(bgcolor=SURFACE, bordercolor=AXIS, font=dict(family=FONT, color=INK)),
    legend=dict(bgcolor=SURFACE, font=dict(color=INK_2)),
    xaxis=dict(_AXIS, rangeslider=dict(bgcolor=SURFACE, bordercolor=AXIS)), yaxis=_AXIS,
    modebar=dict(bgcolor=SURFACE, color=MUTED, activecolor=INK),
    annotationdefaults=dict(font=dict(color=MUTED)), shapedefaults=dict(line=dict(color=AXIS)),
    updatemenudefaults=dict(bgcolor=SURFACE, bordercolor=AXIS, font=dict(color=INK))))


def _style_axes(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(template=CHART_TEMPLATE, height=height, margin=dict(l=70, r=20, t=50, b=70),
                      # Pinned to the figure's bottom: a legend in paper y collides with the x title.
                      legend=dict(orientation="h", yref="container", yanchor="bottom", y=0, x=0,
                                  traceorder="normal"))
    fig.update_yaxes(tickprefix="C$", tickformat="~s")
    return fig


def success_meter(success: float, previous: float | None = None) -> go.Figure:
    """The success gauge. Its label (lasts_label) is HTML above it, where text wraps."""
    if previous is not None and round(previous * 100) == round(success * 100):
        previous = None                 # a "0 pts" delta is noise, not news
    delta = None if previous is None else {
        "reference": round(previous * 100), "suffix": " pts",
        "increasing": {"color": GOOD_TEXT}, "decreasing": {"color": CRITICAL}}
    fig = go.Figure(go.Indicator(
        mode="gauge+number" + ("" if delta is None else "+delta"),
        value=round(success * 100), delta=delta,
        number={"suffix": "%", "font": {"size": 48, "color": INK}},
        gauge={"axis": {"range": [0, 100], "tickcolor": MUTED, "tickfont": {"color": MUTED}},
               "bar": {"color": SERIES[0], "thickness": 0.35}, "bgcolor": TRACK, "borderwidth": 0}))
    fig.update_layout(template=CHART_TEMPLATE, height=200, margin=dict(l=30, r=30, t=20, b=10))
    return fig


def _future_switch(fig: go.Figure, y: float) -> list:
    """Buttons between the average future (the first half of the traces) and the bad-luck one."""
    half = len(fig.data) // 2
    buttons = [dict(label=label, method="update", args=[{"visible": [avg] * half + [not avg] * half}])
               for label, avg in (("Average future", True), ("Bad-luck future", False))]
    return [dict(type="buttons", direction="right", x=0, xanchor="left", y=y, yanchor="bottom",
                 showactive=True, buttons=buttons)]


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
    fig.update_layout(
        barmode="stack", bargap=0.15, barcornerradius=4, hovermode="x unified",
        updatemenus=_future_switch(fig, 1.08))
    _style_axes(fig, 540)
    idx = list(range(0, len(avg.years), 5))
    fig.update_xaxes(tickvals=[avg.years[i] for i in idx], ticktext=[age_label(avg.ages[i]) for i in idx],
                     title_text="Ages", rangeslider=dict(visible=True, thickness=0.06))
    return fig


def money_left_chart(sim: Projection, plan, scale=None) -> go.Figure:
    """Investments by age as a bad / typical / good band, plus the home's value.
    ``scale`` (see future_scale) turns today's dollars into each year's."""
    ref = plan.people[0]
    rows = min(sim.investments.shape[0], engine.steps(plan) + 1)   # stop when the youngest reaches end_age
    k = _scale(scale, rows)
    inv = sim.investments[:rows] * k[:, None]
    x = [ref.age + t for t in range(rows)]
    with warnings.catch_warnings():                       # a year where every future has ended
        warnings.simplefilter("ignore", RuntimeWarning)
        p10, p50, p90 = np.nanpercentile(inv, [10, 50, 90], axis=1)
    fig = go.Figure([
        go.Scatter(x=x, y=p90, name="Good luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   hovertemplate="Good luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p10, name="Bad luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   fill="tonexty", fillcolor=BAND, hovertemplate="Bad luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p50, name="Typical", line=dict(color=SERIES[0], width=2),
                   hovertemplate="Typical: C$%{y:,.0f}<extra></extra>"),
    ])
    if plan.home is not None:
        fig.add_trace(go.Scatter(x=x, y=sim.home_value[:rows] * k, name="Home value",
                                 line=dict(color=SERIES[1], width=2, dash="dot"),
                                 hovertemplate="Home: C$%{y:,.0f}<extra></extra>"))
    _life_marks(fig, plan, x)
    _style_axes(fig, 480)
    fig.update_layout(hovermode="x unified", margin=dict(t=120))
    fig.update_xaxes(title_text=f"Age of {ref.name}")
    return fig


def _first_death(plan) -> tuple | None:
    """(step, person) of the first death in a couple in the average and bad-luck futures
    (both use engine.average_deaths); None when nobody dies before the plan ends."""
    if len(plan.people) != 2:
        return None
    d, T = engine.average_deaths(plan), engine.steps(plan)
    gone = [(int(d[i, 0]) - p.age, p) for i, p in enumerate(plan.people) if d[i, 0] < p.age + T]
    return min(gone, key=lambda g: g[0]) if gone else None


def _life_marks(fig: go.Figure, plan, x: list) -> None:
    """Dashed lines at retirements, CPP/OAS, downsizing and the average future's first
    death, on an x axis of people[0]'s age."""
    ref = plan.people[0]

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
    if first := _first_death(plan):
        t, p = first
        marks.append((ref.age + t, f"{p.name}: dies (average future)"))
    grouped: dict = {}                 # one label per age, so marks never print on top of each other
    for age, label in marks:
        if x[0] <= age <= x[-1]:
            grouped.setdefault(age, []).append(label)
    for i, age in enumerate(sorted(grouped)):
        fig.add_vline(x=age, line=dict(width=1, dash="dash"))
        fig.add_annotation(x=age, y=1.0, yref="paper", text="<br>".join(grouped[age]),
                           showarrow=False, yanchor="bottom", yshift=4 + 30 * (i % 3),
                           font=dict(color=MUTED, size=11))


NET_WORTH_PARTS = (("rrsp", "RRSP/RRIF", COLORS["registered"]), ("pension", "Pension/LIF", COLORS["minimums"]),
                   ("tfsa", "TFSA", COLORS["tfsa"]), ("nonreg", "Non-registered", COLORS["nonreg"]),
                   ("home", "Home", SERIES[1]))


def net_worth(proj: Projection, plan) -> dict:
    """Each January 1 of one future (column 0): the balances, the home, and the tax the
    estate rule (engine: top rate on registered money, on the taxable part of gains)
    would charge if everything were cashed in that day. At the household's last
    January ``after_tax`` is the legacy; later years are NaN."""
    out = {k: proj.balances[k][:, 0] for k in ("rrsp", "pension", "tfsa", "nonreg")}
    out["total"] = proj.investments[:, 0] + proj.home_value
    out["home"] = np.where(np.isnan(out["total"]), np.nan, proj.home_value)
    out["tax_registered"], out["tax_gains"] = engine.estate_tax(
        plan.province, out["rrsp"] + out["pension"], proj.unrealized_gains[:, 0])
    out["after_tax"] = out["total"] - out["tax_registered"] - out["tax_gains"]
    return out


def net_worth_chart(avg: Projection, bad: Projection, plan) -> go.Figure:
    """Stacked assets by age with the after-tax net worth on top; buttons switch the
    average / bad-luck future."""
    ref = plan.people[0]
    rows = engine.steps(plan) + 1
    x = [ref.age + t for t in range(rows)]
    fig = go.Figure()
    for proj, visible in ((avg, True), (bad, False)):
        nw = {key: v[:rows] for key, v in net_worth(proj, plan).items()}
        for key, label, color in NET_WORTH_PARTS:
            if key == "home" and plan.home is None:
                continue
            fig.add_trace(go.Scatter(x=x, y=nw[key], name=label, visible=visible, stackgroup="assets",
                                     line=dict(color=color, width=0.5), fillcolor=color,
                                     hovertemplate=f"{label}: C$%{{y:,.0f}}<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=nw["total"], name="Total (before tax)", visible=visible,
                                 line=dict(color=INK_2, width=1, dash="dot"),
                                 hovertemplate="Total: C$%{y:,.0f}<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=nw["after_tax"], name="Net worth after tax", visible=visible,
                                 line=dict(color=INK, width=3),
                                 hovertemplate="After tax: C$%{y:,.0f}<extra></extra>"))
    _life_marks(fig, plan, x)
    _style_axes(fig, 520)
    fig.update_layout(
        hovermode="x unified", margin=dict(t=165),     # buttons above the age marks
        updatemenus=_future_switch(fig, 1.36))
    fig.update_xaxes(title_text=f"Age of {ref.name}")
    return fig


def _moments(proj: Projection, plan) -> list:
    """(label, step) columns for the statement: today, retirements, 65 and 72 for
    people[0], the first death and the estate, in the average future."""
    ref, end = plan.people[0], int(proj.end_step[0])
    marks = [("Today", 0)]
    marks += [(f"{p.name} retires", p.retire_age - p.age) for p in plan.people]
    marks += [(f"{ref.name} {a}", a - ref.age) for a in (65, 72)]
    if first := _first_death(plan):
        t, p = first
        marks.append((f"After {p.name} dies", t))
    marks.append(("Estate", end))
    merged: dict = {}
    for label, t in marks:
        if 0 <= t <= end:
            merged.setdefault(t, []).append(label)
    return [(" · ".join(merged[t]), t) for t in sorted(merged)]


def _net_worth_table(proj: Projection, plan, scale=None) -> str:
    """The balance sheet at the statement's moments."""
    nw, cols = net_worth(proj, plan), _moments(proj, plan)
    k = _scale(scale, len(nw["total"]))
    head = "".join(f"<th>{_esc(label)}<br><span class='note'>{plan.start_year + t} · "
                   f"{_esc(age_label(p.age + t for p in plan.people))}</span></th>" for label, t in cols)
    lines = [(label, key, "") for key, label, _ in NET_WORTH_PARTS if key != "home" or plan.home is not None]
    lines += [("= Total", "total", "base"), ("− Tax on RRSP/RRIF and pension/LIF", "tax_registered", ""),
              ("− Tax on investment gains", "tax_gains", ""), ("= Net worth after tax", "after_tax", "base")]
    body = "".join(f"<tr class='{cls}'><td>{_esc(label)}</td>"
                   + "".join(f"<td>{_short(nw[key][t] * k[t])}</td>" for _, t in cols) + "</tr>"
                   for label, key, cls in lines)
    return (f"<table><thead><tr><th>Average future, {_dollars(scale)}</th>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _both(today_html: str, future_html: str) -> str:
    """A section's today's-dollars and future-dollars versions; the switch shows one."""
    return (f"<div class='dollars-today'>{today_html}</div>"
            f"<div class='dollars-future'>{future_html}</div>")


def _dollars_fig(fig: go.Figure) -> str:
    """A chart in today's dollars that the switch rescales in place (setDollars), rather
    than a second copy in future dollars."""
    return f"<div class='dollars-chart'>{_fig(fig, False)}</div>"


def _net_worth(plan, avg: Projection, bad: Projection, scale) -> str:
    return ("<p class='note'>What you own each January 1, and what would be left after tax if "
            "everything were cashed in that day: RRSP/RRIF and pension/LIF money taxed at the top "
            "rate and the taxable part of investment gains too, the same rule as the legacy, so the "
            "last column is the Legacy tile. Drawing the money out slowly over retirement usually "
            "costs less tax than this. The RESP is left out, as in the legacy.</p>"
            + _dollars_fig(net_worth_chart(avg, bad, plan))
            + _both(_net_worth_table(avg, plan), _net_worth_table(avg, plan, scale)))


def lasts_label(plan) -> str:
    who = "either of you lives" if len(plan.people) == 2 else "you live"
    return f"Chance the money lasts as long as {who}"


def lifespans(result: PlanResult) -> dict:
    """Median age at death per person, the chance someone reaches 95, and the median
    years a survivor lives alone, over the simulated futures."""
    plan, d = result.inputs, result.simulated.death_ages
    age0 = np.array([[p.age] for p in plan.people])
    out = {"people": [{"name": p.name,      # the upper median, as mortality.median_death_age
                       "median_age_at_death": int(np.quantile(d[i], 0.5, method="higher")) - 1}
                      for i, p in enumerate(plan.people)],
           "reach_95": float((d > 95).any(axis=0).mean()),
           "alone_years": None}
    if len(plan.people) == 2:
        left = d - age0
        out["alone_years"] = float(np.median(np.abs(left[0] - left[1])))
    return out


def _lifespan_tiles(result: PlanResult) -> str:
    life = lifespans(result)
    ages = " · ".join(f"{x['name']} {x['median_age_at_death']}" for x in life["people"])
    tiles = [_tile("Median age at death", ages, "Alberta life table, improving over time"),
             _tile("Chance one of you reaches 95" if len(life["people"]) == 2 else "Chance of reaching 95",
                   f"{life['reach_95']:.0%}", "Plan-to age stays your choice")]
    if life["alone_years"] is not None:
        tiles.append(_tile("Survivor alone (median)", f"{life['alone_years']:.0f} years",
                           f"Spending {result.inputs.spending.survivor_share:.0%} of the couple's"))
    return _tile_row(tiles)


def spending_flex(result: PlanResult) -> dict:
    """With guardrails: the lowest spending level reached (1 = as planned) in the typical
    future and in the 1-in-10 bad one, plus the typical highest."""
    adj = result.simulated.spend_adjust
    low, high = adj.min(axis=0), adj.max(axis=0)
    return {"typical_low": float(np.median(low)), "bad_low": float(np.percentile(low, 10)),
            "typical_high": float(np.median(high))}


def _flex_tile(result: PlanResult) -> str:
    if result.inputs.spending.rule != "guardrails":
        return ""
    f = spending_flex(result)
    pct = lambda x: f"{(x - 1):+.0%}"
    return _tile_row([_tile("Spending with guardrails: lowest level", pct(f["typical_low"]),
                            f"Typical future; 1 in 10: {pct(f['bad_low'])}"),
                      _tile("Highest level reached", pct(f["typical_high"]),
                            "Typical future, after strong years")])


def _legacy_tile(plan, avg: Projection, bad: Projection) -> str:
    return (f"<div class='tile hero'><div class='label'>Legacy at {plan.end_age} "
            f"(after tax, home included)</div><div class='value'>{_short(avg.legacy[0])}</div>"
            f"<div class='sub'>Bad luck: {_short(bad.legacy[0])}</div></div>")


def _kpis(result: PlanResult) -> str:
    avg, bad, r = result.average, result.bad_luck, result.inputs.returns
    rows = [
        ("Short years", f"{int(avg.shortfall_years[0])}", f"Bad luck: {int(bad.shortfall_years[0])}"),
        ("Investments at retirement", _short(avg.investments_at_retirement[0]),
         f"Bad luck: {_short(bad.investments_at_retirement[0])}"),
        ("Rate of return", f"{result.average_return:.2%}",
         f"After inflation; {r.mean:.0%} average with ups and downs" if r.model == "lognormal"
         else f"After inflation; {mix_label(r.mix)}, historical ups and downs"),
        ("Lifetime taxes", _short(avg.lifetime_tax[0]), "Income tax plus tax at death"),
    ]
    return "<div class='kpis'>" + "".join(_tile(*row) for row in rows) + "</div>"


def _actions(result: PlanResult, future: bool = False) -> str:
    """The plan as a dated to-do list, plus the inputs it still guesses at. ``future``
    shows each amount in its year's dollars."""
    def factor(a):
        return actions.future_factor(result.inputs, a.year) if future else 1.0
    rows = "".join(
        f"<tr><td>{a.year}{'–' + str(a.until) if a.until else ''}</td><td>{_esc(a.who)}</td>"
        f"<td>{_esc(a.text(factor(a)))}</td><td>{_esc(a.why)}</td></tr>" for a in actions.plan_actions(result))
    missing = actions.missing_inputs(result.inputs)
    todo = ("<h3>Information to add</h3><p class='note'>The plan estimates these; the real figures "
            "make it more accurate.</p><ul>" + "".join(f"<li>{_esc(m)}</li>" for m in missing)
            + "</ul>") if missing else ""
    return ("<p class='note'>What the plan assumes you do, and when. Amounts are in "
            + ("each year's dollars (average inflation; a range uses its first year)" if future
               else "today's dollars") + " from the average future; the numbers above only hold if these happen.</p>"
            "<table class='actions'><thead><tr><th>When</th><th>Who</th><th>What to do</th><th>Why</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>{todo}")


def _tile_row(tiles: list, gap: bool = True) -> str:
    """Tiles side by side, one column each."""
    margin = ";margin-top:16px" if gap else ""
    return (f"<div class='kpis' style='grid-template-columns:repeat({len(tiles)},1fr){margin}'>"
            + "".join(tiles) + "</div>")


def _tile(label: str, value: str, sub: str) -> str:
    return (f"<div class='tile'><div class='label'>{_esc(label)}</div><div class='value'>{_esc(value)}</div>"
            f"<div class='sub'>{_esc(sub)}</div></div>")


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


def _saved_table(rows) -> str:
    """The plan and its saved scenarios side by side (same futures)."""
    head = "".join(f"<th>{_esc(r.name)}</th>" for r in rows)
    def line(label, cells):
        return f"<tr><td>{_esc(label)}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    body = (line("Chance the money lasts", [f"{r.success:.0%}" for r in rows])
            + line("Legacy (average future)", [_short(r.legacy) for r in rows])
            + line("Lifetime tax (average future)", [_short(r.lifetime_tax) for r in rows])
            + line("Spending (base, a year)", [_money(r.spending) for r in rows])
            + line("Retire at", [_esc(age_label(r.retire_ages)) for r in rows]))
    return ("<p class='note'>Each column reruns the plan with that scenario's changes on the same "
            f"random futures.</p><table><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>")


def _education(plan, avg: Projection, bad: Projection, scale=None) -> str:
    """Children's school in the average future: who pays, year by year. ``scale`` puts
    the table (not the totals) in each year's dollars."""
    s = avg.school
    uncovered = avg.education[:, 0] - s.contribution
    grants = avg.student_grant[:, 0]
    resp_pays = s.cost - grants - uncovered
    years = [t for t in range(len(avg.years)) if s.cost[t] > 0]
    tiles = _tile_row([
        _tile("School costs", _short(s.cost.sum()), f"{int(s.students.sum())} student-years"),
        _tile("Canada Student Grants", _short(grants.sum()),
             f"Bad luck: {_short(bad.student_grant[:, 0].sum())}" if plan.education.student_grant
             else "not applied for"),
        _tile("Paid by the RESP", _short(resp_pays.sum()), f"RESP today {_short(s.balance)}"),
        _tile("Paid by you", _short(uncovered.sum()),
             f"plus {_short(s.contribution.sum())} of RESP contributions"),
    ], gap=False)
    names = {t: ", ".join(k.name for k in s.kids if avg.years[t] in k.school_years) for t in years}
    k = _scale(scale, len(avg.years))
    rows = "".join(
        f"<tr><td>{avg.years[t]}</td><td>{_esc(names[t])}</td><td>{_money(s.cost[t] * k[t])}</td>"
        f"<td>{_money(grants[t] * k[t])}</td><td>{_money(resp_pays[t] * k[t])}</td>"
        f"<td>{_money(uncovered[t] * k[t])}</td><td>{_money(avg.resp[t, 0] * k[t])}</td></tr>"
        for t in years)
    hint = ""
    if plan.education.student_grant and plan.withdrawal.strategy != "proportional":
        hint = ("<p class='note'>The student grant is tested on last year's taxable family income: "
                "RRSP/RRIF withdrawals count, TFSA withdrawals don't. Drawing proportionally from all "
                "accounts (see Expert planning) can keep that income low enough in school years.</p>")
    return (f"{tiles}"
            "<table><thead><tr><th>Year</th><th>In school</th><th>Cost</th><th>Student grant</th>"
            "<th>RESP pays</th><th>You pay</th><th>RESP after</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>{hint}"
            f"<p class='note'>Average future; totals in today's dollars, the table in {_dollars(scale)}. Each grant uses the family income of the "
            "year before; a year with no known prior income gets none.</p>")


def _compact(x) -> str:
    """A table cell: 85.2k / 1.23M, no currency (the caption says C$); zero is a dash."""
    x = float(x)
    if round(x) == 0:
        return "–"
    sign, x = ("-" if x < 0 else ""), abs(x)
    if x >= 1e6:
        return f"{sign}{x / 1e6:.2f}M"
    if x >= 1e3:
        return f"{sign}{x / 1e3:.1f}k"
    return f"{sign}{x:.0f}"


def cash_flow(proj: Projection) -> dict:
    """One future's yearly cash flow (column 0). Sources (shortfall included) equal
    uses (spending + tax + saved): the engine's identity. Investments roll from each
    January 1 to the next: − drawn + saved + growth + added, where ``added`` is the
    engine's ``Projection.added``: what enters without passing through cash. (Not
    ``other``: that key is the one-time-money source.)"""
    T = len(proj.years)
    out = {s: proj.income[s][:, 0] for s in SOURCES}
    out["total_sources"] = sum(out[s] for s in SOURCES)
    out["spending"], out["tax"], out["saved"] = proj.need[:, 0], proj.tax[:, 0], proj.saved[:, 0]
    out["total_uses"] = out["spending"] + out["tax"] + out["saved"]
    out["jan1"], out["next"] = proj.investments[:T, 0], proj.investments[1:T + 1, 0]
    out["drawn"] = sum(proj.income[s][:, 0] for s in DRAWN_FROM)
    out["growth"] = proj.growth[:, 0]
    out["added"] = proj.added[:, 0]
    return out


def _year_table(proj: Projection, scale=None) -> str:
    # (group, header, series): the year's cash flow, then how the investments rolled forward.
    # No column repeats another: the uses' total is the sources', drawn is the drawn sources,
    # saved is the uses' saved, and next January 1 is the next row's January 1.
    cf = cash_flow(proj)
    cols = [*[("Sources", SHORT[s], cf[s]) for s in SOURCES],
            ("Sources", "Total", cf["total_sources"]),
            ("Uses", "Spend", cf["spending"]), ("Uses", "Tax", cf["tax"]), ("Uses", "Saved", cf["saved"]),
            ("Investments", "Jan 1", cf["jan1"]), ("Investments", "Growth", cf["growth"]),
            ("Investments", "Other", cf["added"])]
    if scale is not None:              # one year's dollars across a row, so each row adds up
        cols = [(g, h, v * _scale(scale, len(v))) for g, h, v in cols]
    keep = {"Total", "Spend", "Tax", "Saved", "Jan 1", "Growth"}
    cols = [c for c in cols if c[1] in keep or np.any(np.round(c[2]) != 0)]   # all-zero columns say nothing

    groups: list = []                  # [group, span], merging neighbours
    for g, _, _ in cols:
        if groups and groups[-1][0] == g:
            groups[-1][1] += 1
        else:
            groups.append([g, 1])
    first = {i for i, (g, _, _) in enumerate(cols) if i == 0 or cols[i - 1][0] != g}
    def edge(i):                       # a rule where each group starts
        return " class='g'" if i in first else ""

    top = "<tr><th class='stick' colspan='2'></th>" + "".join(
        f"<th class='g grp' colspan='{n}'>{_esc(g)}</th>" for g, n in groups) + "</tr>"
    sub = ("<tr><th class='stick'>Year</th><th class='stick s2'>Ages</th>"
           + "".join(f"<th{edge(i)} title='{_esc(LONG.get(h, h))}'>{_esc(h)}</th>"
                     for i, (_, h, _) in enumerate(cols)) + "</tr>")
    short = proj.income["shortfall"][:, 0]
    rows = []
    for t, year in enumerate(proj.years):
        cells = "".join(f"<td{edge(i)} title='{_money(v[t])}'>{_compact(v[t])}</td>"
                        for i, (_, _, v) in enumerate(cols))
        cls = " class='short'" if round(short[t]) > 0 else ""
        ages = "/".join(f"{a}{'' if proj.alive is None or proj.alive[t, i, 0] else '†'}"
                        for i, a in enumerate(proj.ages[t]))
        rows.append(f"<tr{cls}><td class='stick'>{year}</td>"
                    f"<td class='stick s2'>{_esc(ages)}</td>{cells}</tr>")
    future = (" Each row is in its own year's dollars, so the roll lands one year's inflation "
              "below the next row's Jan 1." if scale is not None else "")
    return (f"<p class='note'>C$, {_dollars(scale)}; k = thousand, M = million, – = none. Hover a "
            "heading for its full name, a cell for the exact amount. Earned = salary and other work "
            "income; RRIF min = RRIF/LIF minimums; Reg / TFSA / Non-reg = drawn from those accounts; "
            "CCB = Canada Child Benefit; Events = one-time money in; Short = shortfall. Each year the "
            "Total pays for the uses: Spend + Tax + Saved. A shortfall is spending nothing could pay "
            "for. Investments roll from one Jan 1 to the next row's: less the drawn sources (RRIF min, "
            "Reg, TFSA, Non-reg), plus Saved, Growth and Other. Other is money that arrives without passing through your "
            "hands: an employer's pension match, and on the next January 1 home-sale money, RESP "
            "leftovers or the CPP death benefit. Tax includes CPP/EI premiums while working; Saved includes your planned "
            "contributions. Columns that are zero every year are hidden; short years are shaded red; "
            "† = has died (the survivor's years follow)." + future
            + "</p><div class='years'><table><thead>" + top + sub + "</thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _rule_value(value) -> str:
    text = str(value).replace("inf", "∞")
    return text if len(text) <= 140 else text[:137] + "…"


def _education_facts(plan, s) -> list:
    if s is None:
        return []
    rows = [(f"School: {k.name}",
             f"{k.school_years[0]}–{k.school_years[-1]}, living {k.living} at {_money(k.yearly_cost)} "
             f"a year; RESP contributions {_money(k.contributions)} earning {_money(k.grants)} "
             f"of grant ({_money(k.grant_left)} of lifetime grant left unclaimed)")
            for k in s.kids]
    grant = ("applied for: tested each school year on last year's family income"
             if plan.education.student_grant else "not applied for")
    return [("RESP", f"{_money(s.balance)} today ({_money(s.contributed)} contributed, "
                     f"{_money(s.grants)} grants); pays school first, the household pays the rest"),
            ("Canada Student Grant", grant), *rows]


def _refund_check(plan, refunds) -> str:
    """The model's expected refund beside the refunds found in the bank CSVs."""
    if refunds is None or refunds[0] is None:
        return ""
    actual, deposits = refunds
    expected = refund.expected_refund(plan)
    total = sum(r for _, r in expected)
    rows = "".join(f"<tr><td>Expected for {_esc(n)} (model)</td><td>{_money(r)}</td></tr>" for n, r in expected)
    rows += "".join(f"<tr><td>Deposited {_esc(d)} (bank)</td><td>{_money(a)}</td></tr>" for d, a in deposits)
    rows += (f"<tr class='base'><td>Model vs bank</td><td>{_money(total)} vs {_money(actual)}</td></tr>")
    gap = actual - total
    note = ("The model's refund comes from this year's RRSP contributions and child care; the bank's "
            "deposits are last spring's, for the previous tax year, so expect some difference. "
            + (f"The bank shows {_money(gap)} more: likely a deduction or credit the plan doesn't "
               "know about (donations, medical, over-withholding), so the plan's tax may be a "
               "little high." if gap > 0.1 * max(total, 1.0) else
               f"The bank shows {_money(-gap)} less: withholding may already allow for the RRSP, or "
               "a contribution was smaller than planned." if gap < -0.1 * max(total, 1.0) else
               "They agree, so the plan's tax looks right."))
    return ("<h2>Tax refund check</h2><p class='note'>Payroll withholds tax as if there were no RRSP "
            "contribution and the spring refund gives it back; the plan already charges the true tax, "
            "so this only checks it. Never changes the projection.</p>"
            f"<table><tbody>{rows}</tbody></table><p class='note'>{note}</p>")


def _event_text(plan, ev) -> str:
    who = plan.people[event_span(plan, ev)[0]].name
    start = str(ev.year) if ev.year is not None else f"{who} at {ev.age}"
    end = (f" to {ev.until}" if ev.until is not None else
           f" to {who} at {ev.until_age}" if ev.until_age is not None else "")
    if ev.kind == "income":
        return f"{ev.label}: {_money(ev.amount)} a year for {who}, {start}{end}, taxed like salary"
    repeat = f", every {ev.every} years" if ev.every else ""
    sign = "in (untaxed, saved)" if ev.amount >= 0 else "out (spent)"
    return f"{ev.label}: {_money(abs(ev.amount))} {sign}, {start}{repeat}{end}"


def mix_label(mix) -> str:
    """'80% stocks', or '90% stocks to age 45, gliding to 60% at 65, then to 40% at 80'."""
    (a0, s0), rest = mix[0], mix[1:]
    if not rest:
        return f"{s0:.0%} stocks"
    return ", ".join([f"{s0:.0%} stocks to age {a0:g}"]
                     + [f"{'gliding' if i == 0 else 'then'} to {s:.0%} at {a:g}" for i, (a, s) in enumerate(rest)])


def worst_stretch(returns, years: int = 5) -> float | None:
    """The worst ``years``-year compound change in ``returns``; None when too short."""
    r = np.asarray(returns, dtype=float)
    if len(r) < years:
        return None
    return float(min(np.prod(1 + r[t:t + years]) for t in range(len(r) - years + 1)) - 1)


def _returns_fact(plan, result) -> str:
    r = plan.returns
    if r.model == "lognormal":
        return (f"{r.mean:.1%} average, {r.sd:.0%} yearly swings; typical "
                f"{result.average_return:.2%} a year after inflation")
    source = (f"FP Canada {rules.RETURN_ASSUMPTIONS.year} guidelines, before fees"
              if r.stocks is None and r.bonds is None else "your figures")
    return (f"{mix_label(r.mix)}, rebalanced each January; stocks {r.stock_return:.2%} and bonds "
            f"{r.bond_return:.2%} a year after inflation ({source}); each future strings together "
            f"5-year runs of 1928–2025 US stock and bond returns shifted to those averages; typical "
            f"{result.average_return:.2%} a year")


def _assumptions(plan, result: PlanResult, holdings_source: str | None, refunds=None) -> str:
    people = "".join(
        f"<tr><td>{_esc(p.name)}</td><td>{p.age}</td><td>{_esc(p.sex or 'not set (average table)')}</td>"
        f"<td>{p.retire_age}</td>"
        f"<td>{p.cpp_start_age}</td><td>{p.oas_start_age}</td><td>{_money(engine.cpp_at_65(p))}</td>"
        f"<td>{p.years_in_canada_at_65:g}</td><td>{p.rrif_start_age}</td>"
        f"<td>{_esc(p.lif_start_age if p.lif_start_age is not None else 'at retirement (50+)')}</td>"
        f"<td>{_money(p.tfsa_room)}</td>"
        f"<td>{_money(p.salary) if p.salary is not None else 'not set'}</td></tr>"
        for p in plan.people)
    s, h, r, ni = plan.spending, plan.home, plan.returns, plan.nonreg_income
    facts = [
        ("Withdrawal order", STRATEGY_LABELS[plan.withdrawal.strategy]),
        ("TFSA top-up", "each January after retiring, non-registered money fills new TFSA room "
                        "(gains on the shares moved are taxed that year)"
                        if plan.withdrawal.tfsa_top_up else "off"),
        ("Returns", _returns_fact(plan, result)),
        ("Inflation", f"{r.inflation_rate:.2%} a year on average"
                      + (" (Canada's CPI, last 40 years)" if r.inflation is None else "")
                      + (("; each future replays the same 5-year runs of 1928–2025 as the returns, "
                          "centred on that average, so high inflation comes with the returns it brought"
                          if r.model == "history" else
                          "; each future replays 5-year runs of inflation since 1928 (centred on that "
                          "average) and returns lag it in high-inflation years")
                         if r.inflation_shocks and r.inflation is None else "; steady")
                      + "; spending, benefits and tax brackets keep pace, but the book value of "
                        "non-registered investments doesn't, so their gains are taxed on nominal values"),
        ("Costs rising faster than inflation", ", ".join(
            f"{label} {plan.cost_growth.rate(name):+.2%}" for name, label in (
                ("base", "base spending"), ("care", "care"), ("education", "education"),
                ("property_tax", "property tax"), ("insurance", "home insurance")))
         + " a year; shown in today's dollars, so the Future dollars view adds inflation on top"),
        ("Spending", f"{_money(s.base)} a year after tax; slow-go from {s.slow_go_age} "
                     f"({s.slow_go_share:.0%}), no-go from {s.no_go_age} ({s.no_go_share:.0%}) "
                     f"plus {_money(s.care)} care"),
        ("Working years", "each salary pays income tax (after RRSP and your own pension "
                          "contributions) and CPP/EI premiums; take-home pay covers spending, then "
                          "your planned contributions, and the rest is saved (or drawn from savings "
                          "when short). An employer pension match goes straight into the pension"
         if all(p.salary is not None for p in plan.people if p.age < p.retire_age) else
         "Set each worker's salary for an honest view: without one, earned income is assumed to "
         "cover spending exactly and salary tax isn't shown"),
        ("RRSP room", "tracked from your Notice of Assessment: contributions above it go to the TFSA, "
                      "and it grows 18% of salary (up to the yearly limit) less the pension adjustment"
         if any(p.rrsp_room is not None for p in plan.people) else
         "not checked: set people[].rrsp_room to the \"RRSP deduction limit\" on your Notice of "
         "Assessment, or contributions above your room are counted as deductible"),
        *([("Child care", f"{_money(plan.education.childcare)} a year (part of spending), deducted by the "
                          "lower earner while a child is under 16")]
          if plan.education is not None and plan.education.childcare > 0 else []),
        *([("Money events", "; ".join(_event_text(plan, ev) for ev in plan.events))]
          if plan.events else []),
        ("Survivor spending", f"{s.survivor_share:.0%} of the couple's budget once one of you has "
                              "died (care costs stay whole)" if len(plan.people) == 2 else "n/a"),
        ("Lifespans", "drawn per future from the Statistics Canada Alberta life table "
                      "(2021–2023) with the CPP actuarial report's mortality improvement; "
                      + (f"the average future assumes the median first death and the survivor to {plan.end_age}"
                         if len(plan.people) == 2 else f"the average future lives to {plan.end_age}")),
        ("Non-registered payouts",
         "none" if not ni.total else
         f"{ni.eligible_dividends:.1%} Canadian dividends, {ni.foreign_dividends:.1%} foreign "
         f"dividends, {ni.interest:.1%} interest a year, taxed every year and reinvested; "
         f"while working, taxed on top of salary"),
        (("Guardrails", f"spending is cut {s.guardrail_step:.0%} when the withdrawal rate rises "
                        f"{s.guardrail_band:.0%} above where it began (not in the last "
                        f"{s.guardrail_stop_years} years) and raised {s.guardrail_step:.0%} when it falls "
                        f"{s.guardrail_band:.0%} below, kept between {s.guardrail_floor:.0%} and "
                        f"{s.guardrail_ceiling:.0%} of plan")
         if s.rule == "guardrails" else
         ("Bad-market rule", f"cut {s.bad_market_cut:.0%} when investments are below "
                             f"{s.bad_market_trigger:.0%} of their value in the first year nobody earns")),
        ("Home", "none" if h is None else (
            f"{_money(h.value)}; downsize at {h.downsize_age} to {_money(h.new_value)}"
            if h.downsize_age is not None else f"{_money(h.value)}; never sold")),
        *_education_facts(plan, result.average.school),
        ("Balances from", holdings_source or "plan.json"),
        ("Futures simulated", f"{result.simulated.paths:,}"),
    ]
    worst = worst_stretch(result.bad_luck_returns) if (
        r.model == "history" and result.bad_luck_returns is not None) else None
    if worst is not None:
        facts.insert(next(i for i, f in enumerate(facts) if f[0] == "Inflation") + 1,
                     ("Worst stretch", f"the bad-luck future's worst 5 years change investments by "
                                       f"{worst:+.0%} after inflation"))
    rules_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(_rule_value(rule.value))}</td><td>{rule.year}</td>"
        f"<td><a href='{_esc(rule.source)}'>source</a></td></tr>" for name, rule in rules.all_rules())
    life = [("mortality.life_table", "Alberta 2021–2023, by sex, ages 0–110", mortality.QX),
            ("mortality.improvement", _rule_value(mortality.IMPROVEMENT.value), mortality.IMPROVEMENT)]
    rules_rows += "".join(
        f"<tr><td>{_esc(n)}</td><td>{_esc(v)}</td><td>{r.year}</td>"
        f"<td><a href='{_esc(r.source)}'>source</a></td></tr>" for n, v, r in life)
    return (
        "<table><thead><tr><th>Person</th><th>Age</th><th>Sex</th><th>Retires</th><th>CPP from</th>"
        "<th>OAS from</th><th>CPP at 65 (yearly)</th><th>Years in Canada at 65</th>"
        f"<th>RRIF from</th><th>LIF from</th><th>TFSA room carried in</th><th>Salary</th></tr></thead>"
        f"<tbody>{people}</tbody></table>"
        "<table><tbody>" + "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in facts)
        + "</tbody></table>"
        + _refund_check(plan, refunds) +
        "<h2>Canadian rules used</h2>"
        "<table><thead><tr><th>Rule</th><th>Value</th><th>Year</th><th>Official source</th></tr>"
        f"</thead><tbody>{rules_rows}</tbody></table>")


# The switch: show one version of each section, and rescale each dollars chart's numeric y
# and customdata by year (point i is year i) from the copy in today's dollars kept on gd._today.
# Plotly keeps a packed array as {dtype, bdata, _inputArray}; the values are in _inputArray.
_SET_DOLLARS = """
function setDollars(f,b){document.body.classList.toggle('future',f);
for(const x of b.parentNode.children)x.classList.toggle('on',x===b);
const num=a=>a&&(ArrayBuffer.isView(a)||Array.isArray(a)&&a.some(v=>typeof v==="number"));
const k=a=>Array.from(a,(v,i)=>v==null?v:v*(f?SCALE[i]:1));
for(const gd of document.querySelectorAll('.dollars-chart .js-plotly-plot')){
  gd._today=gd._today||gd.data.map(t=>[t.y,t.customdata].map(a=>(a=a&&(a._inputArray||a),num(a)?Array.from(a):null)));
  Plotly.restyle(gd,{y:gd._today.map(([y],j)=>y?k(y):gd.data[j].y),
                     customdata:gd._today.map(([,c],j)=>c?k(c):gd.data[j].customdata)})}
window.dispatchEvent(new Event('resize'))}"""
_RANGE_NOTE = ("The chart above follows two futures; this one spans all the simulated ones. "
               "Investments only (no tax taken off): the band runs from the 1-in-10 bad outcome to "
               "the 1-in-10 good one, the middle line is typical, and the home is the dotted line.")


def build_report(result: PlanResult, baseline, suggestions, *, generated_at: str,
                 holdings_source: str | None = None, previous: dict | None = None,
                 today: dt.date | None = None, refunds: tuple | None = None,
                 saved: list | None = None) -> str:
    """The whole page as a string. ``previous`` is the last run's summary, for the change;
    ``refunds`` is ``refund.actual_refunds(...)`` for the refund check."""
    plan, sim, avg, bad = result.inputs, result.simulated, result.average, result.bad_luck
    today = today or dt.date.today()
    banner = ""
    if rules.is_stale(today.year):
        banner = (f"<p class='banner'>⚠ The tax and benefit rules are for {rules.TAX_YEAR}. "
                  f"Update rules.py for {today.year} from the official pages it cites.</p>")
    prev = previous.get("success") if previous else None
    meter = success_meter(sim.success, prev)
    top = (f"<div class='top'><div class='card'><div class='meter-title'>{_esc(lasts_label(plan))}</div>"
           f"{_fig(meter, 'cdn')}</div>"
           f"{_legacy_tile(plan, avg, bad)}{_kpis(result)}</div>{_lifespan_tiles(result)}"
           f"{_flex_tile(result)}")
    scale = future_scale(plan, len(avg.years) + 1)

    sections = [
        ("summary", "", top),
        (ACTIONS_ID, "Action plan", _both(_actions(result), _actions(result, future=True))),
        ("suggestions", "Expert planning", _suggestions(baseline, suggestions)),
        *([(SAVED_ID, "Saved scenarios", _saved_table(saved))] if saved else []),
        *([(EDUCATION_ID, "Children's education", _both(_education(plan, avg, bad),
                                                          _education(plan, avg, bad, scale)))]
          if avg.education is not None else []),
        ("income", "Detailed income projection",
         "<p class='note'>Each bar is one year's spending plus income tax, by where the money "
         "comes from. Switch to the bad-luck future (the 1-in-10 bad run of returns) to see "
         "short years in red.</p>"
         + _dollars_fig(income_chart(avg, bad))),
        ("money-left", "How wide the range is",
         _both(f"<p class='note'>{_RANGE_NOTE} In today's dollars.</p>",
               f"<p class='note'>{_RANGE_NOTE} In {_dollars(scale)}.</p>")
         + _dollars_fig(money_left_chart(sim, plan))),
        (NET_WORTH_ID, "Net worth", _net_worth(plan, avg, bad, scale)),
        ("years", "Cash flow, year by year (average future)",
         _both(_year_table(avg), _year_table(avg, scale))),
        ("assumptions", "Assumptions &amp; rules", _assumptions(plan, result, holdings_source, refunds)),
    ]
    switch = (f'<div id="{DOLLARS_ID}" class="switch">'
              "<button class='on' onclick=\"setDollars(false,this)\">Today's dollars</button> "
              "<button onclick=\"setDollars(true,this)\">Future dollars</button></div>"
              f"<script>const SCALE={json.dumps(np.round(scale, 6).tolist())};" + _SET_DOLLARS + "</script>")
    html_of = {sid: f'<section id="{sid}">' + (f"<h2>{title}</h2>" if title else "") + content
               + "</section>" for sid, title, content in sections}
    tabs = [(tid, label, "".join(html_of[s] for s in sids if s in html_of))
            for tid, label, sids in TABS if any(s in html_of for s in sids)]
    on = lambda i: " on" if i == 0 else ""
    nav = ("<nav class='tabs'>" + "".join(
        f"<button data-tab='{tid}' class='{on(i)}' onclick=\"showTab('{tid}')\">{label}</button>"
        for i, (tid, label, _) in enumerate(tabs)) + "</nav>")
    panels = "".join(f"<div id=\"{tid}\" class='panel{on(i)}'>{content}</div>"
                     for i, (tid, _, content) in enumerate(tabs))
    tab_js = ("<script>function showTab(id){for(const p of document.querySelectorAll('.panel'))"
              "p.classList.toggle('on',p.id===id);for(const b of document.querySelectorAll('nav.tabs button'))"
              "b.classList.toggle('on',b.dataset.tab===id);history.replaceState(null,'','#'+id);"
              "window.dispatchEvent(new Event('resize'));}"
              "(function(){const h=location.hash.slice(1),el=h&&document.getElementById(h),"
              "p=el&&el.closest('.panel');if(p){showTab(p.id);if(el!==p)el.scrollIntoView();}})();</script>")
    body = html_of["summary"] + nav + panels + tab_js
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Retirement plan</title><style>{THEME_CSS}{_STYLE}</style>"
            f"<script>{THEME_JS}</script></head><body>"
            f"<header><h1>Retirement plan</h1><p class='generated'>Generated {_esc(generated_at)} · "
            f"{sim.paths:,} simulated futures · amounts in CAD</p>{switch}{banner}"
            f"</header>{body}</body></html>")


def headline(result: PlanResult) -> dict:
    """The run's headline numbers (simulated success, the average future's money)."""
    avg = result.average
    return {"success": result.simulated.success, "paths": result.simulated.paths,
            "legacy": float(avg.legacy[0]), "lifetime_tax": float(avg.lifetime_tax[0]),
            "investments_at_retirement": float(avg.investments_at_retirement[0]),
            "lifespans": lifespans(result)}


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
