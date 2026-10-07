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
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
LABELS = {"earned": "Earned income", "cpp": "CPP", "oas": "OAS", "minimums": "RRIF/LIF minimums",
          "registered": "Registered", "tfsa": "TFSA", "nonreg": "Non-registered",
          "ccb": "Child benefit", "other": "One-time money", "shortfall": "⚠ Shortfall"}
COLORS = {**dict(zip(SOURCES[:-1], SERIES)), "ccb": MUTED, "other": AXIS, "shortfall": CRITICAL}
DRAWN = ("registered", "tfsa", "nonreg")   # year-table sources that are withdrawals from an account
ACTIONS_ID = "actions"
SECTION_IDS = ("summary", ACTIONS_ID, "suggestions", "income", "money-left", "years", "assumptions")
EDUCATION_ID = "education"          # only when the plan has children's education
SAVED_ID = "saved"                  # only when the plan has saved scenarios

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
table.actions th,table.actions td{{text-align:left;vertical-align:top}}
.up{{color:{GOOD_TEXT}}} .down{{color:{CRITICAL}}} .flat{{color:{INK_2}}} tr.base td{{font-weight:600}}
details summary{{cursor:pointer;font-weight:600;margin:.8rem 0}}
.years{{overflow:auto;max-height:70vh;border:1px solid rgba(11,11,11,.10);border-radius:10px}}
.years table{{font-size:.78rem;white-space:nowrap}}
.years th,.years td{{padding:3px 7px}}
.years thead{{position:sticky;top:0;z-index:2}}
.years thead tr{{background:{PAGE}}} .years th{{color:{INK_2};font-weight:600;vertical-align:bottom}}
.years th.grp{{text-align:center;color:{MUTED};font-weight:500;border-bottom:1px solid {AXIS}}}
.years .g{{border-left:1px solid {AXIS}}}
.years .stick{{position:sticky;left:0;z-index:1;background:inherit;text-align:left}}
.years tr>.stick:first-child{{width:3.2em;min-width:3.2em}}
.years .s2{{left:calc(3.2em + 14px);border-right:1px solid {AXIS}}}
.years tbody tr{{background:{SURFACE}}} .years tbody tr:nth-child(even){{background:{PAGE}}}
.years tbody tr:hover{{background:{TRACK}}} .years tbody tr.short{{background:#fbe9e9}}
.years tr.short td:first-child{{box-shadow:inset 3px 0 {CRITICAL}}}
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


def success_meter(success: float, previous: float | None = None,
                  title: str = "Chance the money lasts") -> go.Figure:
    if previous is not None and round(previous * 100) == round(success * 100):
        previous = None                 # a "0 pts" delta is noise, not news
    delta = None if previous is None else {
        "reference": round(previous * 100), "suffix": " pts",
        "increasing": {"color": GOOD_TEXT}, "decreasing": {"color": CRITICAL}}
    fig = go.Figure(go.Indicator(
        mode="gauge+number" + ("" if delta is None else "+delta"),
        value=round(success * 100), delta=delta,
        number={"suffix": "%", "font": {"size": 48, "color": INK}},
        title={"text": title, "font": {"size": 14, "color": INK_2}},
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
    rows = min(sim.investments.shape[0], engine.steps(plan) + 1)   # stop when the youngest reaches end_age
    inv = sim.investments[:rows]
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
        fig.add_trace(go.Scatter(x=x, y=sim.home_value[:rows], name="Home value",
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
    fixed = engine.average_deaths(plan)
    for i, p in enumerate(plan.people):
        if len(plan.people) == 2 and fixed[i, 0] < p.age + engine.steps(plan):
            marks.append((at(p, int(fixed[i, 0])), f"{p.name}: dies (average future)"))
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
         f"After inflation; {r.mean:.0%} average with ups and downs"),
        ("Lifetime taxes", _short(avg.lifetime_tax[0]), "Income tax plus tax at death"),
    ]
    return "<div class='kpis'>" + "".join(_tile(*row) for row in rows) + "</div>"


def _actions(result: PlanResult) -> str:
    """The plan as a dated to-do list, plus the inputs it still guesses at."""
    rows = "".join(
        f"<tr><td>{a.year}{'–' + str(a.until) if a.until else ''}</td><td>{_esc(a.who)}</td>"
        f"<td>{_esc(a.what)}</td><td>{_esc(a.why)}</td></tr>" for a in actions.plan_actions(result))
    missing = actions.missing_inputs(result.inputs)
    todo = ("<h3>Information to add</h3><p class='note'>The plan estimates these; the real figures "
            "make it more accurate.</p><ul>" + "".join(f"<li>{_esc(m)}</li>" for m in missing)
            + "</ul>") if missing else ""
    return ("<p class='note'>What the plan assumes you do, and when. Amounts are in today's dollars "
            "from the average future; the numbers above only hold if these happen.</p>"
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


def _education(plan, avg: Projection, bad: Projection) -> str:
    """Children's school in the average future: who pays, year by year."""
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
    rows = "".join(
        f"<tr><td>{avg.years[t]}</td><td>{_esc(names[t])}</td><td>{_money(s.cost[t])}</td>"
        f"<td>{_money(grants[t])}</td><td>{_money(resp_pays[t])}</td><td>{_money(uncovered[t])}</td>"
        f"<td>{_money(avg.resp[t, 0])}</td></tr>" for t in years)
    hint = ""
    if plan.education.student_grant and plan.withdrawal.strategy != "proportional":
        hint = ("<p class='note'>The student grant is tested on last year's taxable family income: "
                "RRSP/RRIF withdrawals count, TFSA withdrawals don't. Drawing proportionally from all "
                "accounts (see Expert planning) can keep that income low enough in school years.</p>")
    return (f"{tiles}"
            "<table><thead><tr><th>Year</th><th>In school</th><th>Cost</th><th>Student grant</th>"
            "<th>RESP pays</th><th>You pay</th><th>RESP after</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>{hint}"
            "<p class='note'>Average future, today's dollars. Each grant uses the family income of the "
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


def _year_table(proj: Projection) -> str:
    # (group, header, series). Sources are what was drawn that year; balances are January 1.
    cols = [("", "Spending", proj.need[:, 0]), ("", "Tax", proj.tax[:, 0]),
            *[("Income by source", LABELS[s] + (" drawn" if s in DRAWN else ""), proj.income[s][:, 0])
              for s in SOURCES],
            ("Income by source", "Saved", proj.saved[:, 0]),
            *[("January 1 balances", h, proj.balances[k][:, 0]) for k, h in
              (("rrsp", "RRSP/RRIF"), ("pension", "Pension/LIF"), ("tfsa", "TFSA"), ("nonreg", "Non-reg"))],
            ("January 1 balances", "Total invested", proj.investments[:, 0])]
    if proj.education is not None:
        cols += [("Education", "Household", proj.education[:, 0]),
                 ("Education", "Student grants", proj.student_grant[:, 0]),
                 ("Education", "RESP", proj.resp[:, 0])]
    keep = {"Spending", "Tax", "Total invested"}
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
           + "".join(f"<th{edge(i)}>{_esc(h)}</th>" for i, (_, h, _) in enumerate(cols)) + "</tr>")
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
    return ("<p class='note'>C$, today's dollars; k = thousand, M = million, – = none. Hover a cell "
            "for the exact amount. Columns that are zero every year are hidden; short years are "
            "shaded red; † = has died (the survivor's years follow). Tax includes CPP/EI premiums while working; Saved includes your planned contributions (not an employer's pension match).</p><div class='years'><table><thead>" + top + sub + "</thead><tbody>"
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
        ("Returns", f"{r.mean:.1%} average, {r.sd:.0%} yearly swings; typical "
                    f"{result.average_return:.2%} a year after inflation"),
        ("Inflation", f"{r.inflation_rate:.2%} a year"
                      + (" (Canada's CPI, average of the last 40 years)" if r.inflation is None else "")
                      + "; spending, benefits and tax brackets keep pace, but the book value of "
                        "non-registered investments doesn't, so their gains are taxed on nominal values"),
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
    meter = success_meter(sim.success, prev, title=lasts_label(plan))
    top = (f"<div class='top'><div class='card'>{_fig(meter, 'cdn')}</div>"
           f"{_legacy_tile(plan, avg, bad)}{_kpis(result)}</div>{_lifespan_tiles(result)}"
           f"{_flex_tile(result)}")
    sections = [
        ("summary", "", top),
        (ACTIONS_ID, "Action plan", _actions(result)),
        ("suggestions", "Expert planning", _suggestions(baseline, suggestions)),
        *([(SAVED_ID, "Saved scenarios", _saved_table(saved))] if saved else []),
        *([(EDUCATION_ID, "Children's education", _education(plan, avg, bad))]
          if avg.education is not None else []),
        ("income", "Detailed income projection",
         "<p class='note'>Each bar is one year's spending plus income tax, by where the money "
         "comes from. Switch to the bad-luck future (the 1-in-10 bad run of returns) to see "
         "short years in red.</p>" + _fig(income_chart(avg, bad), False)),
        ("money-left", "Money left",
         "<p class='note'>Investments only, in today's dollars; the home is the dotted line.</p>"
         + _fig(money_left_chart(sim, plan), False)),
        ("years", "", "<details><summary>Year-by-year table (average future)</summary>"
                      f"{_year_table(avg)}</details>"),
        ("assumptions", "Assumptions &amp; rules", _assumptions(plan, result, holdings_source, refunds)),
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
