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
                    "cheaper_home"}


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
