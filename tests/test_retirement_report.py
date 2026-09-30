"""The retirement HTML report: sections, the Average/Bad-luck switch, red shortfalls."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

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
