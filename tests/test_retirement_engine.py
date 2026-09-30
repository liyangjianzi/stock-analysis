"""The retirement engine: benefits, returns and the year-by-year model (zero-volatility
cases, so every number is predictable). Invented households only."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import engine
from stockanalysis.retirement.inputs import (Account, Home, Person, PlanInputs, Returns,
                                             Spending, SpendingChange, Withdrawal)


def person(**kw) -> Person:
    base = dict(id="A", name="A", age=60, retire_age=60, cpp_start_age=70, oas_start_age=70,
                cpp_at_65=0.0, years_in_canada_at_65=0.0, rrif_start_age=71,
                lif_start_age=None, unlock_share=0.0, tfsa_room=0.0)
    base.update(kw)
    return Person(**base)


def plan(people=None, accounts=(), base=30_000.0, end_age=63, strategy="rrsp_first",
         home=None, changes=(), **spend_kw) -> PlanInputs:
    spend = dict(base=base, changes=tuple(changes), slow_go_age=200, no_go_age=200,
                 care=0.0, bad_market_cut=0.0)
    spend.update(spend_kw)
    return PlanInputs(province="AB", start_year=2026, end_age=end_age,
                      people=tuple(people or (person(),)), spending=Spending(**spend),
                      home=home, returns=Returns(0.0, 0.0, 1, 1),
                      withdrawal=Withdrawal(strategy, 58_000.0), accounts=tuple(accounts))


# -- benefits & returns ------------------------------------------------------------

def test_cpp_statement_figure_wins_over_the_estimate():
    assert engine.cpp_at_65(person(cpp_at_65=12_000.0)) == 12_000.0


def test_cpp_estimate_from_years_with_general_dropout():
    p = person(cpp_at_65=None, age=50, retire_age=60, cpp_years=20, cpp_earnings_ratio=1.0)
    counted = 47 - min(8, 0.17 * 47)                       # 39.01 years after the dropout
    assert engine.cpp_at_65(p) == pytest.approx(12 * 1_507.65 * 30 / counted)


def test_cpp_start_age_adjustments():
    assert engine.cpp_factor(65) == 1.0
    assert engine.cpp_factor(70) == pytest.approx(1.42)
    assert engine.cpp_factor(60) == pytest.approx(0.64)


def test_oas_amounts_residence_deferral_and_age_75():
    p = person(oas_start_age=65, years_in_canada_at_65=40)
    assert engine.oas_yearly(p, 64) == 0.0
    assert engine.oas_yearly(p, 65) == pytest.approx(9_150.0)
    assert engine.oas_yearly(p, 75) == pytest.approx(10_065.0)
    assert engine.oas_yearly(person(oas_start_age=70, years_in_canada_at_65=40), 70) == pytest.approx(9_150 * 1.36)
    assert engine.oas_yearly(person(oas_start_age=65, years_in_canada_at_65=20), 66) == pytest.approx(4_575.0)
    assert engine.oas_yearly(person(oas_start_age=65, years_in_canada_at_65=9), 66) == 0.0


def test_median_return_is_the_geometric_typical_return():
    assert engine.median_return(0.05, 0.15) == pytest.approx(0.03945, abs=5e-4)
    assert engine.median_return(0.05, 0.0) == pytest.approx(0.05)


def test_draw_returns_is_seeded_and_shaped_years_by_paths():
    a = engine.draw_returns(0.05, 0.15, 4_000, 3, seed=1)
    assert a.shape == (3, 4_000)
    np.testing.assert_array_equal(a, engine.draw_returns(0.05, 0.15, 4_000, 3, seed=1))
    assert a.mean() == pytest.approx(0.05, abs=0.01)


# -- the simulation (zero returns unless noted) -------------------------------------

def run_flat(p, n=1):
    return engine.simulate(p, np.zeros((engine.steps(p), n)))


def test_sources_add_up_to_need_plus_tax_plus_saved():
    p = plan(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0),
                       Account("A", "nonreg", 50_000.0, cost=25_000.0)], base=40_000.0, end_age=66)
    proj = run_flat(p)
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)
    assert proj.max_residual < 1.0 and proj.tax[0, 0] > 0


def test_rrsp_first_draws_registered_before_tfsa():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0)],
                         base=40_000.0))
    assert proj.income["registered"][0, 0] > 40_000 and proj.income["tfsa"][0, 0] == 0.0


def test_proportional_draws_in_proportion():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 100_000.0), Account("A", "tfsa", 100_000.0)],
                         base=20_000.0, strategy="proportional"))
    assert proj.income["registered"][0, 0] == pytest.approx(proj.income["tfsa"][0, 0], rel=1e-9)


def test_steady_income_fills_to_target_and_saves_the_rest():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 1_000_000.0)], base=20_000.0,
                         strategy="steady_income"))
    assert proj.income["registered"][0, 0] == pytest.approx(58_000.0)
    assert proj.saved[0, 0] == pytest.approx(58_000.0 - 20_000.0 - proj.tax[0, 0], abs=2.0)


def test_rrif_minimum_starts_the_year_after_conversion():
    proj = run_flat(plan(people=[person(age=64, rrif_start_age=65)],
                         accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=67))
    assert proj.income["minimums"][0, 0] == 0.0          # 64
    assert proj.income["minimums"][1, 0] == 0.0          # 65: the conversion year
    assert proj.income["minimums"][2, 0] == pytest.approx(0.04 * proj.balances["rrsp"][2, 0])


def test_already_converted_rrif_pays_minimum_immediately():
    proj = run_flat(plan(people=[person(age=72, rrif_start_age=65)],
                         accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=74))
    assert proj.income["minimums"][0, 0] == pytest.approx(5_280.0)


def test_locked_pension_waits_for_age_50_then_unlocks_half():
    proj = run_flat(plan(people=[person(age=48, retire_age=48, unlock_share=0.5)],
                         accounts=[Account("A", "pension", 100_000.0)], base=10_000.0, end_age=52))
    assert proj.income["shortfall"][0, 0] == pytest.approx(10_000.0)
    assert proj.income["shortfall"][1, 0] == pytest.approx(10_000.0)
    assert proj.balances["rrsp"][2, 0] == pytest.approx(50_000.0)
    assert proj.balances["pension"][2, 0] == pytest.approx(50_000.0)
    assert proj.income["shortfall"][2, 0] == pytest.approx(0.0, abs=1.0)


def test_lif_maximum_caps_withdrawals():
    proj = run_flat(plan(people=[person(age=60, retire_age=60, lif_start_age=60)],
                         accounts=[Account("A", "pension", 100_000.0)], base=50_000.0, end_age=62))
    assert proj.income["registered"][0, 0] == pytest.approx(0.0677 * 100_000)
    assert proj.income["shortfall"][0, 0] == pytest.approx(50_000 - 6_770, abs=1.0)


def test_tfsa_withdrawal_is_tax_free_and_room_returns_next_year():
    proj = run_flat(plan(accounts=[Account("A", "tfsa", 50_000.0)], base=10_000.0, end_age=62))
    assert proj.tax[0, 0] == 0.0 and proj.income["tfsa"][0, 0] == pytest.approx(10_000.0)
    assert proj.tfsa_room[1, 0] - proj.tfsa_room[0, 0] == pytest.approx(7_000.0 + 10_000.0)


def test_downsizing_adds_tax_free_money():
    home = Home(value=1_000_000.0, downsize_age=65, new_value=600_000.0, selling_cost=0.04,
                moving_cost=20_000.0)
    proj = run_flat(plan(people=[person(age=64)], base=30_000.0, end_age=67, home=home))
    assert proj.income["shortfall"][0, 0] == pytest.approx(30_000.0)
    assert proj.balances["nonreg"][1, 0] == pytest.approx(340_000.0)
    assert proj.income["nonreg"][1, 0] == pytest.approx(30_000.0) and proj.tax[1, 0] == 0.0
    assert proj.home_value[1] == 600_000.0


def test_single_path_matches_the_same_path_inside_many():
    p = plan(accounts=[Account("A", "rrsp", 500_000.0), Account("A", "tfsa", 100_000.0)],
             base=30_000.0, end_age=70)
    returns = np.random.default_rng(3).normal(0.04, 0.10, (engine.steps(p), 5))
    many, one = engine.simulate(p, returns), engine.simulate(p, returns[:, [3]])
    for s in engine.SOURCES:
        np.testing.assert_allclose(many.income[s][:, 3], one.income[s][:, 0], atol=2.0)
    np.testing.assert_allclose(many.investments[:, 3], one.investments[:, 0], atol=20.0)


def test_rich_plan_always_succeeds_and_impossible_plan_never_does():
    rich = run_flat(plan(accounts=[Account("A", "rrsp", 10_000_000.0)], base=30_000.0), n=3)
    poor = run_flat(plan(base=50_000.0), n=3)
    assert rich.success == 1.0
    assert poor.success == 0.0 and np.all(poor.shortfall_years == engine.steps(plan()))


def test_all_empty_accounts_produce_no_nan():
    for strategy in ("rrsp_first", "proportional", "steady_income"):
        proj = run_flat(plan(base=20_000.0, strategy=strategy), n=2)
        for arr in [proj.tax, proj.need, proj.saved, proj.investments, proj.legacy,
                    *proj.income.values(), *proj.balances.values()]:
            assert np.all(np.isfinite(arr)), strategy


def test_spending_change_before_start_applies_from_step_0():
    proj = run_flat(plan(base=20_000.0, changes=[SpendingChange(2000, -5_000.0)],
                         accounts=[Account("A", "tfsa", 100_000.0)]))
    assert proj.need[0, 0] == pytest.approx(15_000.0)


def test_pension_splitting_equalises_a_one_sided_rrif():
    both = dict(age=70, retire_age=60, rrif_start_age=65)
    one_sided = plan(people=[person(id="A", **both), person(id="B", name="B", **both)],
                     accounts=[Account("A", "rrsp", 1_000_000.0)], base=60_000.0, end_age=72)
    even = plan(people=[person(id="A", **both), person(id="B", name="B", **both)],
                accounts=[Account("A", "rrsp", 500_000.0), Account("B", "rrsp", 500_000.0)],
                base=60_000.0, end_age=72)
    t1, t2 = run_flat(one_sided).tax[0, 0], run_flat(even).tax[0, 0]
    assert t1 == pytest.approx(t2, rel=0.02)


def test_single_person_household_has_single_ages():
    proj = run_flat(plan(accounts=[Account("A", "tfsa", 100_000.0)]))
    assert proj.ages[0] == (60,)


def test_legacy_taxes_registered_money_at_the_top_rate():
    proj = run_flat(plan(people=[person(age=60)], accounts=[Account("A", "rrsp", 100_000.0)],
                         base=0.0, end_age=61))
    assert proj.death_tax[0] == pytest.approx(0.48 * 100_000.0)
    assert proj.legacy[0] == pytest.approx(100_000.0 * 0.52)


# -- run & the two single futures ---------------------------------------------------

def test_bad_luck_is_the_tenth_percentile_path():
    p = plan(accounts=[Account("A", "rrsp", 2_000_000.0)], base=30_000.0, end_age=70)
    returns = np.tile(np.linspace(-0.05, 0.05, 11), (engine.steps(p), 1))   # column j = its own rate
    proj = engine.simulate(p, returns)
    assert engine.bad_luck_index(proj) == 1                                  # 2nd-worst of 11


def test_run_uses_the_median_return_and_replays_the_bad_luck_path():
    p = plan(accounts=[Account("A", "rrsp", 400_000.0)], base=30_000.0, end_age=80)
    from dataclasses import replace
    p = replace(p, returns=Returns(0.05, 0.15, 200, 3))
    result = engine.run(p)
    assert result.average_return == engine.median_return(0.05, 0.15)
    assert result.average.paths == 1 and result.simulated.paths == 200
    k = result.bad_luck_path
    for s in engine.SOURCES:
        np.testing.assert_allclose(result.bad_luck.income[s][:, 0],
                                   result.simulated.income[s][:, k], atol=2.0)


def test_package_exports_the_api():
    import stockanalysis.retirement as retirement
    assert retirement.run is engine.run and retirement.PlanInputs is PlanInputs
