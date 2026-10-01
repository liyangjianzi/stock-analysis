"""Planning tools: highest safe spending, earliest safe retirement, best CPP/OAS ages.
Offline, invented household (inputs.TEMPLATE), small path counts."""
from __future__ import annotations

import pytest

from stockanalysis.retirement import engine, inputs, optimize

PLAN = inputs.parse(inputs.TEMPLATE)
GRID = {"cpp_ages": (60, 65, 70), "oas_ages": (65, 70)}


@pytest.fixture(scope="module")
def R():
    return optimize._returns(PLAN, 60, 7)


def test_max_spending_is_the_last_step_that_meets_the_target(R):
    best, ok = optimize.max_spending(PLAN, 0.9, R)
    assert ok >= 0.9 and best % optimize.SPENDING_STEP == 0
    assert optimize._success(optimize._with_spending(PLAN, best + optimize.SPENDING_STEP), R) < 0.9


def test_earliest_retirement_is_the_first_shift_that_meets_the_target(R):
    shift, ok = optimize.earliest_retirement(PLAN, 0.9, R)
    assert ok >= 0.9
    floor = max(p.age - p.retire_age for p in PLAN.people)
    assert shift == floor or optimize._success(optimize._shift_retirement(PLAN, shift - 1), R) < 0.9


def test_an_unreachable_target_says_so(R):
    assert optimize.max_spending(PLAN, 1.01, R) == (None, None)
    assert optimize.earliest_retirement(PLAN, 1.01, R) == (None, None)
    a = optimize.affordability(PLAN, target=1.01, paths=60)
    assert a.max_spending is None and a.retire_shift is None and a.retire_ages == ()


def test_affordability_reports_the_plan_as_it_stands():
    a = optimize.affordability(PLAN, target=0.9, paths=60)
    assert a.spending == PLAN.spending.base
    assert a.success == engine.simulate(PLAN, optimize._returns(PLAN, 60, PLAN.returns.seed)).success
    assert [name for name, _ in a.retire_ages] == [p.name for p in PLAN.people]


def test_best_benefit_ages_never_lose_legacy():
    b = optimize.best_benefit_ages(PLAN, paths=60, workers=1, **GRID)
    assert b.best_legacy >= b.legacy
    for c, p in zip(b.people, b.plan.people):
        assert c.best == (p.cpp_start_age, p.oas_start_age)
        assert c.best[0] in GRID["cpp_ages"] and c.best[1] in GRID["oas_ages"]
        assert set(c.by_cpp) == set(GRID["cpp_ages"]) and set(c.by_oas) == set(GRID["oas_ages"])
        assert c.by_cpp[c.best[0]] == max(c.by_cpp.values())


def test_ties_keep_the_current_ages():
    # A grid of only today's ages can't move anyone.
    now = {"cpp_ages": (70,), "oas_ages": (70,)}
    b = optimize.best_benefit_ages(PLAN, paths=60, workers=1, **now)
    assert all(c.best == c.current for c in b.people) and b.best_legacy == b.legacy


def test_the_process_pool_gives_the_same_answer():
    serial = optimize.best_benefit_ages(PLAN, paths=60, workers=1, **GRID)
    pooled = optimize.best_benefit_ages(PLAN, paths=60, workers=2, **GRID)
    assert [c.best for c in pooled.people] == [c.best for c in serial.people]
    assert pooled.best_legacy == pytest.approx(serial.best_legacy)
