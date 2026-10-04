"""The retirement HTML report: sections, the Average/Bad-luck switch, red shortfalls."""
from __future__ import annotations

import datetime as dt
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
    assert ("⚠ Shortfall" in table) == bool((avg.income["shortfall"][:, 0].round() != 0).any())
    assert f"title='{report._money(avg.investments[0, 0])}'" in table
    for always in ("Spending", "Tax", "Total invested"):
        assert f">{always}</th>" in table


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
    assert ("Child benefit" in report._year_table(avg)) == bool((avg.income["ccb"][:, 0] > 0.5).any())


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
