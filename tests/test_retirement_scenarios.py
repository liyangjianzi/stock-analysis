"""What-if suggestions: each changes one thing; ranked by change in success."""
from __future__ import annotations

from dataclasses import fields

import pytest

from stockanalysis.retirement import inputs, scenarios
from stockanalysis.retirement.inputs import PlanInputs


@pytest.fixture
def plan(tmp_path):
    return inputs.load_inputs(inputs.write_template(tmp_path / "plan.json"))


def test_variant_keys_for_the_template(plan):
    keys = {k for k, _, _ in scenarios.variants(plan)}
    assert keys == {"withdraw_proportional", "withdraw_steady_income", "spend_less",
                    "retire_later_A", "retire_later_B", "downsize_never", "downsize_70",
                    "cheaper_home", "spend_guardrails"}


def test_each_variant_changes_exactly_one_field(plan):
    for key, label, variant in scenarios.variants(plan):
        changed = [f.name for f in fields(PlanInputs)
                   if getattr(variant, f.name) != getattr(plan, f.name)]
        assert len(changed) == 1, (key, changed)
        assert label


def test_spend_less_is_five_percent(plan):
    variant = dict((k, v) for k, _, v in scenarios.variants(plan))["spend_less"]
    assert variant.spending.base == pytest.approx(plan.spending.base * 0.95)


def test_rank_is_sorted_by_change_in_success(plan):
    baseline, ranked = scenarios.rank(plan, paths=30, seed=1)
    assert baseline.key == "baseline" and baseline.delta == 0.0
    deltas = [s.delta for s in ranked]
    assert deltas == sorted(deltas, reverse=True)
    for s in ranked:
        assert s.delta == pytest.approx(s.success - baseline.success)


def test_no_retire_later_suggestion_for_someone_already_retired(plan):
    from dataclasses import replace
    retired = replace(plan, people=(replace(plan.people[0], retire_age=plan.people[0].age),
                                    plan.people[1]))
    keys = {k for k, _, _ in scenarios.variants(retired)}
    assert "retire_later_A" not in keys and "retire_later_B" in keys


def test_retire_later_label_reads_naturally(plan):
    labels = {k: label for k, label, _ in scenarios.variants(plan)}
    assert labels["retire_later_A"] == "Partner A: retire 1 year later"


def test_suggestion_baseline_matches_the_gauge_at_the_same_paths(plan):
    from stockanalysis.retirement import engine
    baseline, _ = scenarios.rank(plan, paths=60, seed=3)
    assert baseline.success == engine.run(plan, paths=60, seed=3).simulated.success


def test_guardrails_are_offered_as_a_what_if_both_ways():
    from dataclasses import replace
    from stockanalysis.retirement import inputs as _inputs
    p = _inputs.parse(_inputs.TEMPLATE)
    keys = [k for k, _, _ in scenarios.variants(p)]
    assert "spend_guardrails" in keys
    guarded = replace(p, spending=replace(p.spending, rule="guardrails"))
    assert "spend_bad_market" in [k for k, _, _ in scenarios.variants(guarded)]


# -- saved scenarios side by side ---------------------------------------------------------

def test_apply_changes_sets_nested_fields(plan):
    v = scenarios.apply_changes(plan, {"people.1.retire_age": 60, "spending.base": 70_000,
                                       "home.downsize_age": None})
    assert v.people[1].retire_age == 60 and v.people[0] == plan.people[0]
    assert v.spending.base == 70_000 and v.home.downsize_age is None and plan.spending.base != 70_000


def test_compare_saved_puts_the_plan_first_on_shared_futures(plan):
    from dataclasses import replace
    p = replace(plan, saved_scenarios=(("Spend less", {"spending.base": plan.spending.base * 0.8}),))
    rows = scenarios.compare_saved(p, paths=20, seed=1)
    assert [r.name for r in rows] == ["Current plan", "Spend less"]
    s0, t0, l0 = scenarios.evaluate(p, paths=20, seed=1)
    assert (rows[0].success, rows[0].lifetime_tax, rows[0].legacy) == (s0, t0, l0)
    assert rows[1].spending == plan.spending.base * 0.8 and rows[1].success >= rows[0].success
