"""The retirement HTML report: sections, the Average/Bad-luck switch, red shortfalls."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import replace

import numpy as np
import pytest

from stockanalysis.retirement import engine, inputs, report, rules, scenarios


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    plan = inputs.load_inputs(inputs.write_template(tmp_path_factory.mktemp("r") / "plan.json"))
    result = engine.run(plan, paths=40, seed=1)
    baseline, ranked = scenarios.rank(plan, paths=20, seed=1)
    return plan, result, baseline, ranked


def _html(built, **kw):
    _, result, baseline, ranked = built
    kw.setdefault("today", dt.date(rules.TAX_YEAR, 6, 1))
    return report.build_report(result, baseline, ranked, generated_at="2026-09-29 12:00", **kw)


def test_report_has_all_sections(built):
    html = _html(built)
    for sid in report.SECTION_IDS:
        assert f'id="{sid}"' in html
    assert "Expert planning" in html and "Detailed income projection" in html


def test_sections_sit_in_their_tabs(built):
    html = _html(built)
    present = [t for t in report.TABS if any(f'id="{s}"' in html for s in t[2])]
    assert len(present) >= 5                                     # education is the optional one
    starts = {tid: html.find(f'id="{tid}"') for tid, _, _ in present}
    assert all(i > 0 for i in starts.values())
    assert html.find('id="summary"') < min(starts.values())      # the headline stays above the tabs
    for tid, label, sids in present:
        assert f"showTab('{tid}')" in html and f">{label}</button>" in html
        for sid in sids:
            at = html.find(f'id="{sid}"')
            if at < 0:                                         # optional section (saved, education)
                continue
            owner = max((t for t, i in starts.items() if i < at), key=starts.get)
            assert owner == tid, sid
    assert html.count("class='panel on'") == 1
    assert "<details><summary>Year-by-year" not in html          # the tab is the fold now


def test_net_worth_at_the_estate_is_the_legacy(built):
    plan, result, _, _ = built
    for proj in (result.average, result.bad_luck):
        nw = report.net_worth(proj, plan)
        end = int(proj.end_step[0])
        assert nw["after_tax"][end] == pytest.approx(proj.legacy[0])
        assert nw["total"][end] - nw["after_tax"][end] == pytest.approx(proj.death_tax[0])
        assert np.isnan(nw["after_tax"][end + 1:]).all()          # nobody left: nothing to show


def test_net_worth_without_registered_money_or_gains_is_the_total(built):
    plan, result, _, _ = built
    avg = result.average
    zero = {k: (np.zeros_like(v) if k in ("rrsp", "pension") else v) for k, v in avg.balances.items()}
    proj = replace(avg, balances=zero, unrealized_gains=np.zeros_like(avg.unrealized_gains))
    nw = report.net_worth(proj, plan)
    assert np.allclose(nw["after_tax"], nw["total"], equal_nan=True)


def test_net_worth_tab_has_a_statement_from_today(built):
    html = _html(built)
    assert f'id="{report.NET_WORTH_ID}"' in html and "showTab('tab-net-worth')" in html
    assert "Net worth after tax" in html and ">Today<" in html


def test_income_chart_has_average_and_bad_luck_buttons(built):
    html = _html(built)
    assert "Average future" in html and "Bad-luck future" in html


def test_shortfall_layer_is_status_red(built):
    assert report.CRITICAL in _html(built)


def test_stale_rules_banner(built):
    assert "update rules.py" not in _html(built).lower()
    assert "update rules.py" in _html(built, today=dt.date(rules.TAX_YEAR + 1, 1, 5)).lower()


def test_previous_run_shows_the_change(built):
    assert "pts" in _html(built, previous={"success": 0.5})


def test_holdings_source_is_named(built):
    assert "book.xlsx, saved 2026-09-01" in _html(built, holdings_source="book.xlsx, saved 2026-09-01")


def test_summary_round_trip(built, tmp_path):
    _, result, _, _ = built
    report.write_summary(result, tmp_path / "2026-09-29_120000" / "summary.json")
    summary = report.latest_summary(tmp_path)
    assert summary["success"] == pytest.approx(result.simulated.success)
    assert report.latest_summary(tmp_path / "missing") is None


def test_single_person_household_labels_one_age(built):
    plan, _, _, _ = built
    single = replace(plan, people=plan.people[:1],
                     accounts=tuple(a for a in plan.accounts if a.owner == "A"))
    result = engine.run(single, paths=20, seed=1)
    baseline, ranked = scenarios.rank(single, paths=10, seed=1)
    html = report.build_report(result, baseline, ranked, generated_at="x",
                               today=dt.date(rules.TAX_YEAR, 1, 1))
    assert report.age_label((62,)) == "62" and report.age_label((62, 60)) == "62/60"
    assert 'id="income"' in html


def test_income_legend_reads_in_stack_order(built):
    # Horizontal legend reads left-to-right like the stack reads bottom-to-top
    # (Earned income first, Shortfall last), not Plotly's reversed stacked-bar default.
    _, result, _, _ = built
    assert report.income_chart(result.average, result.bad_luck).layout.legend.traceorder == "normal"


def test_money_left_marks_cpp_and_oas_for_each_person(built):
    plan, result, _, _ = built
    texts = [a.text for a in report.money_left_chart(result.simulated, plan).layout.annotations]
    assert any("OAS" in s for s in texts)
    assert sum(s.count("CPP") for s in texts) == len(plan.people)


def test_money_left_labels_do_not_collide(built):
    # Marks at the same age share one label; marks within a few years sit at different heights.
    plan, result, _, _ = built
    notes = report.money_left_chart(result.simulated, plan).layout.annotations
    xs = [a.x for a in notes]
    assert len(xs) == len(set(xs))
    for a in notes:
        for b in notes:
            if a is not b and abs(a.x - b.x) < 5:
                assert a.yshift != b.yshift


def test_assumptions_explain_tfsa_room(built):
    assert "TFSA room carried in" in _html(built)


def test_education_section_only_when_the_plan_has_children():
    import copy
    from stockanalysis.retirement import engine, inputs, scenarios
    d = copy.deepcopy(inputs.TEMPLATE)
    d["education"] = {"kids": [{"name": "Older", "age": 17}], "resp_balance": 30_000}
    for plan, present in ((inputs.parse(d), True), (inputs.parse(inputs.TEMPLATE), False)):
        result = engine.run(plan, paths=40)
        baseline, ranked = scenarios.rank(plan, paths=20, seed=1)
        html = report.build_report(result, baseline, ranked, generated_at="now")
        assert (f'id="{report.EDUCATION_ID}"' in html) is present
        assert ("showTab('tab-education')" in html) is present     # no empty tab
        assert ("Canada Student Grants" in html) is present


def test_compact_cells():
    assert report._compact(0) == "–" and report._compact(0.4) == "–"
    assert report._compact(850) == "850" and report._compact(85_240) == "85.2k"
    assert report._compact(1_234_567) == "1.23M" and report._compact(-2_500) == "-2.5k"


def test_year_table_hides_all_zero_columns_and_keeps_exact_values(built):
    _, result, _, _ = built
    avg = result.average
    table = report._year_table(avg)
    assert table.count("<tbody><tr") == 1 and table.count("</tr>") == len(avg.years) + 2
    assert (">⚠ Short</th>" in table) == bool((avg.income["shortfall"][:, 0].round() != 0).any())
    assert f"title='{report._money(avg.investments[0, 0])}'" in table
    for always in ("Spend", "Tax", "Saved", "Jan 1", "Growth"):
        assert f">{always}</th>" in table
    assert table.count(">Total</th>") == 1                        # the uses' total would repeat it
    assert "title='January 1'>Jan 1</th>" in table                # the full name on hover
    for repeat in ("Drawn", "Next January 1"):                    # read off the sources / next row
        assert f">{repeat}</th>" not in table


def test_cash_flow_balances_every_year(built):
    _, result, _, _ = built
    for proj in (result.average, result.bad_luck):
        cf = report.cash_flow(proj)
        np.testing.assert_allclose(cf["total_sources"], cf["total_uses"], atol=2.0)
        roll = cf["jan1"] - cf["drawn"] + cf["saved"] + cf["growth"] + cf["added"]
        np.testing.assert_allclose(roll, cf["next"], atol=0.01)
        np.testing.assert_allclose(cf["next"][:-1], cf["jan1"][1:])


def test_growth_is_the_return_and_other_is_only_real_inflows():
    import copy
    d = copy.deepcopy(inputs.TEMPLATE)
    d["home"]["downsize_age"] = None
    plain = engine.run(inputs.parse(d), paths=20, seed=1).average
    cf = report.cash_flow(plain)
    flows = cf["added"][cf["added"] != 0]                         # nothing else hides in growth:
    np.testing.assert_allclose(flows, rules.CPP["death_benefit"].value)   # only the death benefit
    assert (cf["growth"] > 0).any()
    moving = engine.run(inputs.parse(inputs.TEMPLATE), paths=20, seed=1).average
    t = moving.downsize_step                                      # the sale lands that January 1
    assert report.cash_flow(moving)["added"][t - 1] > 100_000
    for c in (cf, report.cash_flow(moving)):                      # the source is the events, not the roll
        np.testing.assert_array_equal(c["other"], 0.0)


def test_headline_says_as_long_as_either_of_you_lives(built):
    plan, result, *_ = built
    html = _html(built)
    assert report.lasts_label(plan) in html
    assert report.lasts_label(plan) == "Chance the money lasts as long as either of you lives"
    single = replace(plan, people=plan.people[:1])
    assert report.lasts_label(single) == "Chance the money lasts as long as you live"


def test_lifespans_tile_and_summary(built):
    plan, result, *_ = built
    life = report.lifespans(result)
    assert [x["name"] for x in life["people"]] == [p.name for p in plan.people]
    for x, p in zip(life["people"], plan.people):
        assert p.age <= x["median_age_at_death"] < 111
    assert 0 <= life["reach_95"] <= 1 and life["alone_years"] >= 0
    assert "Median age at death" in _html(built)
    assert report.headline(result)["lifespans"] == life


def test_year_table_marks_the_dead_with_a_dagger(built):
    _, result, *_ = built
    avg = result.average
    table = report._year_table(avg)
    if (~avg.alive[:, :, 0]).any():
        assert "†" in table
    else:
        assert "†" not in table


def test_money_left_band_stops_at_end_age_and_ignores_ended_futures(built):
    plan, result, *_ = built
    fig = report.money_left_chart(result.simulated, plan)
    xs = fig.data[0].x
    # Ends where the average-future charts do: when the youngest reaches end_age
    # (people[0]'s age on the x axis), not when people[0] does.
    assert xs[-1] == plan.people[0].age + engine.steps(plan)
    assert not any(np.isnan(fig.data[2].y[:3]))


def test_assumptions_list_lifespan_sources(built):
    html = _html(built)
    assert "pid=1310011401" in html and "actuarial-report-32nd" in html
    assert "Survivor spending" in html


def test_report_explains_take_home_pay_and_premiums(built):
    html = _html(built)
    assert "Working years" in html and "take-home pay" in html
    assert "CPP/EI premiums while working" in report._year_table(built[1].average)


def test_report_says_to_set_salaries_when_one_is_missing(built):
    plan, result, baseline, ranked = built
    no_salary = replace(plan, people=tuple(replace(p, salary=None) for p in plan.people))
    html = report._assumptions(no_salary, result, None)
    assert "Set each worker" in html and "salary for an honest view" in html


def test_report_shows_the_refund_check_when_given(built):
    html = _html(built, refunds=(2_000.0, [("2026-03-23", 2_000.0)]))
    assert "Tax refund check" in html and "2026-03-23" in html
    assert "Tax refund check" not in _html(built)


def test_report_says_whether_rrsp_room_is_checked(built):
    plan, result, *_ = built
    unset = report._assumptions(plan, result, None)
    assert "RRSP room" in unset and "Notice of Assessment" in unset
    room = replace(plan, people=tuple(replace(p, rrsp_room=10_000.0) for p in plan.people))
    assert "tracked from" in report._assumptions(room, result, None)


def test_child_benefit_shows_in_the_year_table_when_paid(built):
    _, result, *_ = built
    avg = result.average
    assert (">CCB</th>" in report._year_table(avg)) == bool((avg.income["ccb"][:, 0] > 0.5).any())


def test_median_age_at_death_rounds_a_half_up_like_the_engine(built):
    from types import SimpleNamespace
    plan = built[0]
    fake = SimpleNamespace(inputs=replace(plan, people=plan.people[:1]),
                           simulated=SimpleNamespace(death_ages=np.array([[88, 89]])))
    assert report.lifespans(fake)["people"][0]["median_age_at_death"] == 88


def test_one_person_lifespan_text_has_no_survivor(built):
    plan, result, *_ = built
    single = replace(plan, people=plan.people[:1])
    html = report._assumptions(single, result, None)
    assert "survivor to" not in html and "drawn per future" in html


def test_refund_note_counts_child_care_and_names_the_tax_year(built):
    plan = built[0]
    note = report._refund_check(plan, (99_000.0, [("2026-03-23", 99_000.0)]))
    assert "previous tax year" in note and "childcare" not in note.replace("child care", "")


def test_assumptions_list_the_money_events(built):
    from stockanalysis.retirement.inputs import Event
    plan, result, *_ = built
    p = replace(plan, events=(Event("Car", -40_000.0, year=2030, every=10),
                              Event("Part-time", 30_000.0, kind="income", person="B", age=60, until_age=64)))
    html = report._assumptions(p, result, None)
    assert "Car" in html and "every 10 years" in html and "Part-time" in html and "a year" in html


def test_guardrail_spending_range_is_reported(built):
    plan, result, baseline, ranked = built
    guarded_plan = replace(plan, spending=replace(plan.spending, rule="guardrails"))
    guarded = engine.run(guarded_plan, paths=40, seed=1)
    flex = report.spending_flex(guarded)
    assert 0 < flex["bad_low"] <= flex["typical_low"] <= 2
    html = report.build_report(guarded, baseline, ranked, generated_at="x",
                               today=dt.date(rules.TAX_YEAR, 6, 1))
    assert "Spending with guardrails" in html
    assert "Spending with guardrails" not in _html(built)


def test_saved_scenarios_table_when_given(built):
    rows = [scenarios.SavedRow("Current plan", 0.9, 1e6, 3e5, 80_000.0, (60, 58)),
            scenarios.SavedRow("Retire at 62", 0.95, 1.2e6, 3.1e5, 80_000.0, (62, 58))]
    html = _html(built, saved=rows)
    assert "Saved scenarios" in html and "Retire at 62" in html and "62/58" in html
    assert "Saved scenarios" not in _html(built)


def test_future_scale_compounds_the_average_inflation():
    p = inputs.parse(inputs.TEMPLATE)
    s = report.future_scale(p, 3)
    pi = p.returns.inflation_rate
    np.testing.assert_allclose(s, [1, 1 + pi, (1 + pi) ** 2])


def test_report_has_both_dollar_views_and_a_switch(built):
    html = _html(built)
    assert 'id="dollars"' in html and "dollars-today" in html and "dollars-future" in html
    assert "Future dollars" in html and "Today's dollars" in html


def test_charts_rescale_in_place_rather_than_twice(built):
    _, result, _, _ = built
    html = _html(built)
    assert html.count("class='dollars-chart'") == 3          # income, money left, net worth: once each
    assert html.count("Plotly.newPlot") == 4                  # those three and the gauge
    scale = report.future_scale(result.inputs, len(result.average.years) + 1)
    assert f"const SCALE={json.dumps(np.round(scale, 6).tolist())}" in html


def test_assumptions_name_the_inflation_and_cost_growth(built):
    html = _html(built)
    assert "Costs rising faster than inflation" in html and "Canada&#x27;s CPI" in html


def test_dark_theme_swaps_every_chart_colour_both_ways(built):
    light, dark = set(report.CHART_DARK), set(report.CHART_DARK.values())
    assert len(dark) == len(light) and not light & dark      # theme.js inverts the map
    html = _html(built)
    assert "window.theme" in html and "[data-theme=dark]" in html


def _colours(obj):
    if isinstance(obj, str):
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", obj):
            yield obj.lower()
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _colours(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _colours(v)


def test_every_figure_colour_has_a_dark_twin(built):
    plan, result, *_ = built
    figs = (report.success_meter(0.9, 0.8), report.income_chart(result.average, result.bad_luck),
            report.money_left_chart(result.simulated, plan),
            report.net_worth_chart(result.average, result.bad_luck, plan))
    for fig in figs:                     # the template replaces Plotly's own (unmapped) defaults
        assert set(_colours(fig.to_plotly_json())) <= set(report.CHART_DARK)


def test_mix_label_reads_the_glide():
    assert report.mix_label(((0, 0.8),)) == "80% stocks"
    assert report.mix_label(((45, 0.9), (65, 0.6), (80, 0.4))) == \
        "90% stocks to age 45, gliding to 60% at 65, then to 40% at 80"


def test_worst_stretch_is_the_worst_5_year_real_change():
    r = np.array([0.1, -0.2, -0.3, 0.0, 0.1, -0.1, 0.2])
    assert report.worst_stretch(r) == pytest.approx(min(np.prod(1 + r[t:t + 5]) for t in range(3)) - 1)
    assert report.worst_stretch(np.array([0.1, 0.1])) is None        # shorter than 5 years


def test_assumptions_describe_the_history_model(built):
    plan, result, _, _ = built
    html = report._assumptions(plan, result, None)
    assert "80% stocks" in html and f"FP Canada {rules.RETURN_ASSUMPTIONS.year}" in html
    assert "1928" in html and "Worst stretch" in html
    assert "returns lag it" not in html


def test_assumptions_keep_the_old_lines_for_the_lognormal_model(built):
    plan, result, _, _ = built
    lo = replace(plan, returns=replace(plan.returns, model="lognormal"))
    html = report._assumptions(lo, replace(result, inputs=lo, bad_luck_returns=None), None)
    assert "yearly swings" in html and "returns lag it" in html and "Worst stretch" not in html


def test_rate_of_return_tile_describes_the_history_mix(built):
    plan, result, _, _ = built
    tiles = report._kpis(result)
    assert "80% stocks" in tiles and f"{plan.returns.mean:.0%} average" not in tiles
    lo = replace(plan, returns=replace(plan.returns, model="lognormal"))
    assert f"{plan.returns.mean:.0%} average" in report._kpis(replace(result, inputs=lo))
