"""The retirement engine: benefits, returns and the year-by-year model (zero-volatility
cases, so every number is predictable). Invented households only."""
from __future__ import annotations

import numpy as np
import pytest

from dataclasses import replace

from stockanalysis.retirement import engine, rules, tax
from stockanalysis.retirement.inputs import (Account, CostGrowth, Education, Espp, Event, Home, Kid,
                                             NonregIncome, Person, PlanInputs, Returns, Spending,
                                             SpendingChange, Withdrawal)


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
                      home=home, returns=Returns(0.0, 0.0, 1, 1, inflation=0.0),
                      cost_growth=CostGrowth(0.0, 0.0, 0.0, 0.0, 0.0),
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
    p = replace(p, returns=Returns(0.05, 0.15, 200, 3))
    result = engine.run(p)
    assert result.average_return == engine.median_return(0.05, 0.15)
    assert result.average.paths == 1 and result.simulated.paths == 200
    assert len(result.simulated.years) == engine.life_steps(p)
    assert len(result.average.years) == engine.steps(p)
    R, D, _ = engine.draw_futures(p, 200, 3)
    replay = engine.simulate(p, R[:engine.steps(p), [result.bad_luck_path]], engine.average_deaths(p))
    for s in engine.SOURCES:
        np.testing.assert_allclose(result.bad_luck.income[s], replay.income[s])
    np.testing.assert_array_equal(result.simulated.death_ages, D)


def test_package_exports_the_api():
    import stockanalysis.retirement as retirement
    assert retirement.run is engine.run and retirement.PlanInputs is PlanInputs


# -- final-review fixes -------------------------------------------------------------

def test_workers_registered_money_is_not_drawn_before_retirement():
    for strategy in ("steady_income", "rrsp_first"):
        p = plan(people=[person(age=50, retire_age=60)], accounts=[Account("A", "rrsp", 500_000.0)],
                 base=30_000.0, end_age=55, strategy=strategy)
        proj = run_flat(p)
        assert np.all(proj.income["registered"] == 0.0), strategy
        assert np.all(proj.balances["rrsp"][:, 0] == 500_000.0), strategy


def test_projection_runs_until_the_youngest_reaches_end_age():
    p = plan(people=[person(id="A", age=60), person(id="B", name="B", age=50)], end_age=63)
    assert engine.steps(p) == 13
    from stockanalysis.retirement import mortality
    assert engine.life_steps(p) == mortality.OMEGA - min(q.age for q in p.people)
    assert run_flat(p).ages[-1] == (72, 62)


def test_lif_already_running_at_start_pays_its_minimum_and_is_not_unlocked_again():
    proj = run_flat(plan(people=[person(age=75, retire_age=60, lif_start_age=60, unlock_share=0.5)],
                         accounts=[Account("A", "pension", 100_000.0)], base=0.0, end_age=77))
    assert proj.balances["rrsp"][0, 0] == 0.0 and proj.balances["pension"][0, 0] == 100_000.0
    assert proj.income["minimums"][0, 0] == pytest.approx(rules.rrif_min_factor(74) * 100_000.0)


def test_unlocked_lif_money_is_not_in_that_years_rrif_minimum_base():
    proj = run_flat(plan(people=[person(age=70, retire_age=70, rrif_start_age=65, unlock_share=0.5)],
                         accounts=[Account("A", "rrsp", 100_000.0), Account("A", "pension", 100_000.0)],
                         base=0.0, end_age=72))
    lif_min = 0.0                                   # the LIF's first year has no minimum
    assert proj.income["minimums"][0, 0] == pytest.approx(100_000.0 / 21 + lif_min)


# -- non-registered payouts -------------------------------------------------------

def test_a_retirees_payouts_are_taxed_every_year():
    p = plan(accounts=[Account("A", "nonreg", 1_000_000.0, cost=1_000_000.0)], base=40_000.0)
    without = run_flat(p)
    taxed = run_flat(replace(p, nonreg_income=NonregIncome(foreign_dividends=0.03)))
    assert taxed.tax[0, 0] > without.tax[0, 0] + 1_000
    assert taxed.investments[-1, 0] < without.investments[-1, 0]
    total = sum(taxed.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, taxed.need + taxed.tax + taxed.saved, atol=2.0)


def test_eligible_dividends_cost_less_tax_than_foreign_ones():
    p = plan(accounts=[Account("A", "nonreg", 1_000_000.0, cost=1_000_000.0)], base=40_000.0)
    eligible = run_flat(replace(p, nonreg_income=NonregIncome(eligible_dividends=0.03)))
    foreign = run_flat(replace(p, nonreg_income=NonregIncome(foreign_dividends=0.03)))
    assert eligible.tax[0, 0] < foreign.tax[0, 0]


def test_a_workers_payouts_are_taxed_on_top_of_salary_and_sold_from_the_account():
    worker = person(age=50, retire_age=55, salary=150_000.0)
    p = replace(plan(people=[worker], accounts=[Account("A", "nonreg", 500_000.0)], base=40_000.0,
                     end_age=56), nonreg_income=NonregIncome(foreign_dividends=0.02))
    proj = run_flat(p)
    due = float(tax.income_tax(ordinary=160_000, age=50) - tax.income_tax(ordinary=150_000, age=50))
    pay = float(tax.income_tax(salary=150_000, age=50)) + float(tax.payroll_premiums(150_000))
    assert proj.tax[0, 0] == pytest.approx(pay + due, abs=0.01)        # salary tax + the payout tax
    assert proj.income["nonreg"][0, 0] == pytest.approx(due, abs=0.01)  # sold from the account
    assert proj.investments[1, 0] == pytest.approx(500_000.0 - due + proj.saved[0, 0], abs=0.01)
    low = run_flat(replace(p, people=(replace(worker, salary=40_000.0),)))
    low_pay = float(tax.income_tax(salary=40_000, age=50)) + float(tax.payroll_premiums(40_000))
    assert low.tax[0, 0] - low_pay < due                                 # a lower marginal rate


def test_no_payouts_leave_only_salary_tax_in_working_years():
    worker = person(age=50, retire_age=55, salary=150_000.0)
    proj = run_flat(plan(people=[worker], accounts=[Account("A", "nonreg", 500_000.0)],
                         base=40_000.0, end_age=56))
    pay = float(tax.income_tax(salary=150_000, age=50)) + float(tax.payroll_premiums(150_000))
    assert proj.tax[0, 0] == pytest.approx(pay, abs=0.01) and proj.income["nonreg"][0, 0] == 0.0
    assert proj.investments[1, 0] == pytest.approx(500_000.0 + proj.saved[0, 0])


# -- lifespans: the alive mask ------------------------------------------------------

def couple(**kw):
    a = person(id="A", name="A", age=60, retire_age=60)
    b = person(id="B", name="B", age=60, retire_age=60)
    return plan(people=[a, b], **kw)


def test_no_deaths_given_means_everyone_lives_the_whole_horizon():
    p = couple(accounts=[Account("A", "rrsp", 300_000.0), Account("B", "tfsa", 200_000.0)],
               base=40_000.0, end_age=75)
    returns = np.random.default_rng(5).normal(0.03, 0.1, (engine.steps(p), 4))
    implicit = engine.simulate(p, returns)
    explicit = engine.simulate(p, returns, engine.fixed_deaths(p, engine.steps(p), 4))
    for s in engine.SOURCES:
        np.testing.assert_array_equal(implicit.income[s], explicit.income[s])
    np.testing.assert_array_equal(implicit.legacy, explicit.legacy)
    assert implicit.alive.all() and (implicit.end_step == engine.steps(p)).all()


def test_after_the_last_death_nothing_is_spent_or_short_and_balances_are_nan():
    p = couple(accounts=[Account("A", "tfsa", 50_000.0)], base=40_000.0, end_age=80)
    deaths = np.array([[63], [65]])                     # A lives 3 years, B 5
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)
    assert proj.end_step[0] == 5
    assert (proj.need[5:, 0] == 0).all() and (proj.income["shortfall"][5:, 0] == 0).all()
    assert np.isnan(proj.investments[6:, 0]).all() and not np.isnan(proj.investments[5, 0])
    assert proj.final_investments[0] == proj.investments[5, 0]


def test_legacy_is_valued_at_the_last_death():
    p = plan(people=[person(age=60)], accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=90)
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), np.array([[61]]))
    assert proj.death_tax[0] == pytest.approx(0.48 * 100_000.0)
    assert proj.legacy[0] == pytest.approx(52_000.0)


def test_a_worker_who_dies_stops_earning_and_the_retired_partner_draws_instead():
    a = person(id="A", name="A", age=50, retire_age=60)                # works
    b = person(id="B", name="B", age=62, retire_age=60)                # retired
    p = plan(people=[a, b], accounts=[Account("B", "tfsa", 500_000.0)], base=30_000.0, end_age=70)
    deaths = np.array([[52], [80]])                                    # A dies after 2 years
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)
    assert proj.income["earned"][1, 0] == 30_000.0 and proj.income["earned"][2, 0] == 0.0
    assert proj.income["tfsa"][2, 0] > 0


# -- lifespans: survivor years ------------------------------------------------------

def widowed(a_kw=None, b_kw=None, accounts=(), base=40_000.0, end_age=80, death_a=63, **kw):
    """A couple where A dies at ``death_a`` and B lives past the horizon; zero returns."""
    a = person(**{"id": "A", "name": "A", "age": 60, "retire_age": 60, **(a_kw or {})})
    b = person(**{"id": "B", "name": "B", "age": 60, "retire_age": 60, **(b_kw or {})})
    p = plan(people=[a, b], accounts=accounts, base=base, end_age=end_age, **kw)
    deaths = np.array([[death_a], [p.end_age]])
    return p, engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)


def test_rollover_moves_everything_to_the_survivor_untaxed():
    # No spending, no returns, no RRIF yet (rrif_start_age 71): nothing should move but owners.
    p, proj = widowed(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0),
                                Account("B", "tfsa", 10_000.0)], base=0.0)
    assert proj.investments[3, 0] == pytest.approx(260_000.0)          # the January after A's death
    assert proj.investments[10, 0] == pytest.approx(260_000.0)         # still all there, now B's
    assert proj.balances["rrsp"][3, 0] == pytest.approx(200_000.0)     # still registered, untaxed
    assert (proj.tax[:, 0] == 0).all()


def test_the_survivor_can_draw_the_dead_partners_rrsp():
    # All the money is A's RRSP; after A dies, B must live on it (a dead person's
    # RRSP is never drawn, so without the rollover B would run short).
    p, proj = widowed(accounts=[Account("A", "rrsp", 500_000.0)], base=20_000.0)
    assert proj.income["registered"][3, 0] > 0
    assert proj.shortfall_years[0] == 0


def test_survivor_spends_the_survivor_share_and_care_stays_whole():
    p, proj = widowed(base=40_000.0, accounts=[Account("B", "tfsa", 2_000_000.0)],
                      care=5_000.0, no_go_age=60, no_go_share=1.0)
    assert proj.need[2, 0] == pytest.approx(45_000.0)
    assert proj.need[3, 0] == pytest.approx(40_000.0 * 0.70 + 5_000.0)


def test_cpp_survivor_pension_at_65_is_60_percent_capped_by_the_combined_maximum():
    s = rules.CPP["survivor"].value
    p, proj = widowed(a_kw=dict(age=66, cpp_at_65=12_000.0, cpp_start_age=65),
                      b_kw=dict(age=66, cpp_at_65=6_000.0, cpp_start_age=65),
                      accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=68, end_age=75)
    assert proj.income["cpp"][1, 0] == pytest.approx(18_000.0)
    assert proj.income["cpp"][2, 0] == pytest.approx(6_000.0 + 0.60 * 12_000.0)
    big = widowed(a_kw=dict(age=66, cpp_at_65=18_000.0, cpp_start_age=65),
                  b_kw=dict(age=66, cpp_at_65=17_000.0, cpp_start_age=65),
                  accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=68, end_age=75)[1]
    assert big.income["cpp"][2, 0] == pytest.approx(12 * s["combined_max_monthly"])


def test_a_survivor_under_65_gets_the_flat_rate_plus_37_5_percent():
    s = rules.CPP["survivor"].value
    p, proj = widowed(a_kw=dict(age=55, cpp_at_65=12_000.0), b_kw=dict(age=55, cpp_at_65=0.0),
                      accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=57, end_age=70)
    assert proj.income["cpp"][2, 0] == pytest.approx(12 * s["flat_monthly"] + 0.375 * 12_000.0)


def test_no_survivor_pension_when_the_partner_never_paid_into_cpp():
    p, proj = widowed(a_kw=dict(age=55, cpp_at_65=0.0), b_kw=dict(age=55, cpp_at_65=0.0),
                      accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=57, end_age=70)
    assert (proj.income["cpp"][:, 0] == 0).all()


def test_no_pension_splitting_after_the_first_death():
    # All the RRIF is A's; B dies at 72. With no spending, A's only income is the RRIF
    # minimum, so A must be taxed exactly as a single filer on it: no split to B.
    both = dict(age=70, retire_age=60, rrif_start_age=65)
    p = plan(people=[person(id="A", name="A", **both), person(id="B", name="B", **both)],
             accounts=[Account("A", "rrsp", 1_000_000.0)], base=0.0, end_age=80)
    zeros = np.zeros((engine.steps(p), 1))
    alone = engine.simulate(p, zeros, np.array([[80], [72]]))
    together = engine.simulate(p, zeros)
    pension = alone.income["minimums"][3, 0]
    assert alone.tax[3, 0] == pytest.approx(float(tax.income_tax(pension=pension, age=73)), abs=1.0)
    assert together.tax[3, 0] < alone.tax[3, 0]                      # splitting helped while both lived


def test_the_death_benefit_is_paid_once_to_the_survivor():
    benefit = rules.CPP["death_benefit"].value
    p, with_cpp = widowed(a_kw=dict(cpp_at_65=10_000.0), base=0.0)
    _, without = widowed(a_kw=dict(cpp_at_65=0.0), base=0.0)
    gap = with_cpp.investments[3, 0] - without.investments[3, 0]
    assert gap == pytest.approx(benefit, abs=1.0)


def test_one_person_plan_ignores_the_survivor_share():
    p = plan(people=[person(age=60)], accounts=[Account("A", "tfsa", 500_000.0)], base=30_000.0,
             end_age=80, survivor_share=0.5)
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), np.array([[70]]))
    assert proj.need[5, 0] == 30_000.0 and proj.end_step[0] == 10


def test_average_future_loses_the_earlier_median_death_first_and_the_survivor_reaches_end_age():
    from stockanalysis.retirement import mortality
    a = person(id="A", name="A", age=60, sex="male")
    b = person(id="B", name="B", age=60, sex="female")
    p = plan(people=[a, b], end_age=95)
    d = engine.average_deaths(p)
    assert d[0, 0] == min(mortality.median_death_age(a, 2026), a.age + engine.steps(p))
    assert d[1, 0] == b.age + engine.steps(p)
    single = plan(people=[person(age=60)], end_age=95)
    assert engine.average_deaths(single)[0, 0] == 95


def test_success_counts_only_years_someone_is_alive():
    p = plan(accounts=[Account("A", "tfsa", 100_000.0)], base=30_000.0, end_age=95)
    R = np.zeros((engine.life_steps(p), 2))
    proj = engine.simulate(p, R, np.array([[62, 100]]))
    assert proj.shortfall_years[0] == 0 and proj.shortfall_years[1] > 0


def test_bad_luck_path_is_ranked_on_returns_not_on_lifespans():
    # Ranked on drawn deaths, a household that died early has little money left and
    # looks "unlucky" though its returns weren't; rank on shared fixed deaths instead.
    p = plan(people=[person(age=60)], accounts=[Account("A", "rrsp", 900_000.0)],
             base=30_000.0, end_age=95)
    p = replace(p, returns=Returns(0.05, 0.15, 300, 3))
    result = engine.run(p)
    R, _, _ = engine.draw_futures(p, 300, 3)
    T = engine.steps(p)
    fixed = engine.simulate(p, R[:T], np.repeat(engine.average_deaths(p), 300, axis=1))
    assert result.bad_luck_path == engine.bad_luck_index(fixed)


# -- working years: salary drives the cash --------------------------------------------

def worker(**kw):
    return person(**{"age": 40, "retire_age": 50, "salary": 100_000.0, **kw})


def test_earned_income_is_gross_salary_and_tax_includes_salary_tax_and_premiums():
    w = worker(contributions={"rrsp": 10_000.0, "pension": 5_500.0}, pension_match=1.75)
    proj = run_flat(plan(people=[w], base=50_000.0, end_age=60))
    own_pension = 5_500.0 / 2.75                                   # 2,000 of your own; 3,500 employer
    expected = (float(tax.income_tax(salary=100_000.0, deductions=10_000.0 + own_pension, age=40))
                + float(tax.payroll_premiums(100_000.0)))
    assert proj.income["earned"][0, 0] == 100_000.0
    assert proj.tax[0, 0] == pytest.approx(expected, abs=1.0)


def test_take_home_surplus_is_saved_and_the_employer_match_goes_straight_in():
    w = worker(contributions={"rrsp": 10_000.0, "pension": 5_500.0}, pension_match=1.75)
    proj = run_flat(plan(people=[w], base=50_000.0, end_age=60))
    assert proj.saved[0, 0] == pytest.approx(100_000.0 - proj.tax[0, 0] - 50_000.0, abs=1.0)
    surplus = proj.saved[0, 0] - 10_000.0 - 2_000.0                # beyond your own contributions
    assert surplus > 0
    assert proj.investments[1, 0] == pytest.approx(10_000.0 + 5_500.0 + surplus, abs=1.0)


def test_a_take_home_shortfall_is_drawn_from_savings():
    w = worker(salary=60_000.0)
    proj = run_flat(plan(people=[w], accounts=[Account("A", "tfsa", 1_000_000.0)], base=80_000.0,
                         end_age=60))
    assert proj.income["earned"][0, 0] == 60_000.0
    assert proj.income["tfsa"][0, 0] > 0 and (proj.income["shortfall"][:10, 0] == 0).all()


def test_the_employer_match_never_reduces_take_home():
    matched = run_flat(plan(people=[worker(contributions={"pension": 5_500.0}, pension_match=1.75)],
                            base=50_000.0, end_age=60))
    own_only = run_flat(plan(people=[worker(contributions={"pension": 2_000.0})], base=50_000.0,
                             end_age=60))
    assert matched.tax[0, 0] == pytest.approx(own_only.tax[0, 0])
    assert matched.saved[0, 0] == pytest.approx(own_only.saved[0, 0])
    assert matched.investments[1, 0] - own_only.investments[1, 0] == pytest.approx(3_500.0)


def test_without_a_salary_earned_income_still_just_covers_spending():
    proj = run_flat(plan(people=[worker(salary=None)], base=50_000.0, end_age=60))
    assert proj.income["earned"][0, 0] == 50_000.0 and proj.tax[0, 0] == 0.0


def test_sources_add_up_with_a_salaried_worker_and_a_retired_partner():
    a = worker(id="A", name="A", contributions={"rrsp": 10_000.0, "tfsa": 7_000.0, "nonreg": 5_000.0})
    b = person(id="B", name="B", age=62, retire_age=60)
    p = plan(people=[a, b], accounts=[Account("B", "rrsp", 300_000.0), Account("B", "tfsa", 50_000.0)],
             base=90_000.0, end_age=70)
    proj = run_flat(p)
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


def test_lifetime_tax_leaves_out_cpp_and_ei_premiums():
    proj = run_flat(plan(people=[worker()], base=50_000.0, end_age=60))
    premiums = 10 * float(tax.payroll_premiums(100_000.0))           # ten working years
    assert proj.lifetime_tax[0] == pytest.approx(proj.tax[:, 0].sum() - premiums + proj.death_tax[0], abs=1.0)


# -- ESPP --------------------------------------------------------------------------



def test_espp_buys_discounted_shares_into_nonreg_and_the_discount_is_taxed():
    espp = Espp(rate=0.25, cap=25_000.0, discount=0.15)
    with_plan = run_flat(plan(people=[worker(salary=140_000.0, espp=espp)], base=50_000.0, end_age=60))
    without = run_flat(plan(people=[worker(salary=140_000.0)], base=50_000.0, end_age=60))
    fmv = 25_000.0 / 0.85                                       # 25% of 140k is capped at 25k
    benefit = fmv - 25_000.0
    assert with_plan.income["earned"][0, 0] == pytest.approx(140_000.0 + benefit)
    expected_tax = (float(tax.income_tax(salary=140_000.0, ordinary=benefit, age=40))
                    + float(tax.payroll_premiums(140_000.0)))
    assert with_plan.tax[0, 0] == pytest.approx(expected_tax, abs=1.0)
    gain = with_plan.investments[1, 0] - without.investments[1, 0]
    assert gain == pytest.approx(fmv - 25_000.0 - (with_plan.tax[0, 0] - without.tax[0, 0]), abs=1.0)


def test_espp_purchase_is_rate_times_salary_below_the_cap():
    espp = Espp(rate=0.10, cap=25_000.0, discount=0.15)
    proj = run_flat(plan(people=[worker(salary=100_000.0, espp=espp)], base=50_000.0, end_age=60))
    assert proj.income["earned"][0, 0] == pytest.approx(100_000.0 + 10_000.0 / 0.85 - 10_000.0)


def test_sources_add_up_with_an_espp():
    espp = Espp(rate=0.25, cap=25_000.0, discount=0.15)
    p = plan(people=[worker(salary=140_000.0, espp=espp, contributions={"rrsp": 10_000.0})],
             accounts=[Account("A", "tfsa", 100_000.0)], base=60_000.0, end_age=60)
    proj = run_flat(p)
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


def test_no_salary_means_no_espp():
    espp = Espp(rate=0.25, cap=25_000.0, discount=0.15)
    proj = run_flat(plan(people=[worker(salary=None, espp=espp)], base=50_000.0, end_age=60))
    assert proj.income["earned"][0, 0] == 50_000.0 and proj.investments[1, 0] == 0.0


# -- child benefit, childcare, RRSP room ------------------------------------------------



def with_kids(p, ages, childcare=0.0):
    kids = tuple(Kid(name=f"K{i}", age=a) for i, a in enumerate(ages))
    return replace(p, education=Education(kids=kids, resp_balance=0.0, contributed=0.0, grants=0.0,
                                          contribute=False, student_grant=False, childcare=childcare))


def test_child_benefit_is_paid_on_last_years_income_until_18():
    p = with_kids(plan(accounts=[Account("A", "tfsa", 1_000_000.0)], base=40_000.0, end_age=65),
                  ages=(10, 16))
    proj = run_flat(p)
    assert proj.income["ccb"][0, 0] == 0.0                        # a retired household's last income is unknown
    assert proj.income["ccb"][1, 0] == pytest.approx(2 * 6_883)   # TFSA draws: no income last year
    assert proj.income["ccb"][2, 0] == pytest.approx(6_883)       # the older one turned 18
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


def test_childcare_is_deducted_by_the_lower_earner_while_both_work():
    a = worker(id="A", name="A", salary=140_000.0)
    b = worker(id="B", name="B", salary=60_000.0, retire_age=42)        # stops working after 2 years
    base = plan(people=[a, b], base=60_000.0, end_age=50)
    none = run_flat(with_kids(base, ages=(10,)))
    some = run_flat(with_kids(base, ages=(10,), childcare=6_000.0))
    # 6,000 paid, but a child aged 7-15 allows 5,000.
    saving = float(tax.income_tax(salary=60_000.0, age=40) - tax.income_tax(salary=60_000.0, deductions=5_000.0, age=40))
    assert none.tax[0, 0] - some.tax[0, 0] == pytest.approx(saving, abs=1.0)
    assert some.tax[2, 0] == pytest.approx(none.tax[2, 0])               # B retired: no earned income


def test_rrsp_contributions_are_capped_by_room_and_the_excess_goes_to_the_tfsa():
    w = worker(salary=100_000.0, contributions={"rrsp": 20_000.0}, rrsp_room=5_000.0)
    proj = run_flat(plan(people=[w], base=40_000.0, end_age=50))
    assert proj.balances["rrsp"][1, 0] == pytest.approx(5_000.0)
    assert proj.balances["tfsa"][1, 0] == pytest.approx(7_000.0)          # this year's TFSA limit
    expected = float(tax.income_tax(salary=100_000.0, deductions=5_000.0, age=40)) + float(tax.payroll_premiums(100_000.0))
    assert proj.tax[0, 0] == pytest.approx(expected, abs=1.0)
    # Next year: 18% of the salary is new room.
    assert proj.balances["rrsp"][2, 0] == pytest.approx(5_000.0 + 18_000.0)


def test_without_rrsp_room_contributions_are_not_capped():
    w = worker(salary=100_000.0, contributions={"rrsp": 20_000.0})
    proj = run_flat(plan(people=[w], base=40_000.0, end_age=50))
    assert proj.balances["rrsp"][1, 0] == pytest.approx(20_000.0)


def test_child_benefit_tests_net_income_after_rrsp_and_pension_deductions():
    w = worker(salary=100_000.0, contributions={"rrsp": 20_000.0})
    proj = run_flat(with_kids(plan(people=[w], base=40_000.0, end_age=50), ages=(10,)))
    assert proj.income["ccb"][1, 0] == pytest.approx(float(tax.child_benefit(80_000.0, 0, 1)))


def test_contributions_take_home_cannot_fund_are_cut_not_counted_as_short():
    # 70k salary, 50k spending, 15k RRSP + 7k TFSA planned and no savings: the household
    # contributes what's left, it doesn't run short.
    w = worker(salary=70_000.0, contributions={"rrsp": 15_000.0, "tfsa": 7_000.0})
    proj = run_flat(plan(people=[w], base=50_000.0, end_age=50))
    assert (proj.income["shortfall"][:, 0] == 0).all() and proj.success == 1.0
    assert 0 < proj.balances["rrsp"][1, 0] + proj.balances["tfsa"][1, 0] < 22_000.0
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


def test_a_workers_withdrawals_are_taxed_on_top_of_the_salary():
    w = worker(salary=100_000.0)
    p = plan(people=[w], accounts=[Account("A", "nonreg", 500_000.0, cost=0.0)], base=120_000.0,
             end_age=50)
    proj = run_flat(p)
    drawn = proj.income["nonreg"][0, 0]
    assert drawn > 0
    extra = float(tax.income_tax(salary=100_000.0, gains=drawn, age=40) - tax.income_tax(salary=100_000.0, age=40))
    pay = float(tax.income_tax(salary=100_000.0, age=40)) + float(tax.payroll_premiums(100_000.0))
    assert proj.tax[0, 0] == pytest.approx(pay + extra, abs=2.0)


# -- clean-up: deaths and the household's events ---------------------------------------

def _resp_plan(order):
    """A retired couple; A has a RRIF, B nothing; one child finishing school in year 2
    with RESP growth left over."""
    a = person(id="A", name="A", age=70, retire_age=60, rrif_start_age=65)
    b = person(id="B", name="B", age=70, retire_age=60, rrif_start_age=65)
    people = [a, b] if order == "AB" else [b, a]
    kid = Kid(name="K", age=19)
    p = plan(people=people, accounts=[Account("B", "rrsp", 600_000.0), Account("B", "tfsa", 300_000.0)],
             base=40_000.0, end_age=76)
    return replace(p, education=Education(kids=(kid,), resp_balance=150_000.0, contributed=20_000.0,
                                          grants=0.0, contribute=False, student_grant=False))


def test_leftover_resp_money_goes_to_the_living_parent_whatever_the_order():
    # A dies after year 0 in both orders; the leftover growth must be taxed as B's income.
    ab = _resp_plan("AB")
    ba = _resp_plan("BA")
    d_ab = np.array([[71], [90]])          # (A, B)
    d_ba = np.array([[90], [71]])          # (B, A)
    one = engine.simulate(ab, np.zeros((engine.steps(ab), 1)), d_ab)
    two = engine.simulate(ba, np.zeros((engine.steps(ba), 1)), d_ba)
    end = one.school.end_step
    assert one.tax[end, 0] == pytest.approx(two.tax[end, 0], abs=1.0)


def test_the_bad_market_cut_starts_when_the_only_worker_dies():
    a = person(id="A", name="A", age=50, retire_age=60)                 # works, dies after year 0
    b = person(id="B", name="B", age=62, retire_age=60)
    p = plan(people=[a, b], accounts=[Account("B", "tfsa", 2_000_000.0)], base=40_000.0, end_age=70,
             bad_market_cut=0.10, bad_market_trigger=0.80)
    returns = np.full((engine.steps(p), 1), -0.30)
    returns[0] = 0.0
    proj = engine.simulate(p, returns, np.array([[51], [90]]))
    assert proj.need[2, 0] == pytest.approx(40_000.0 * 0.70 * 0.90)     # survivor share, then the cut


def test_year_0_child_benefit_estimates_last_years_net_income():
    w = worker(salary=100_000.0, contributions={"rrsp": 20_000.0})
    proj = run_flat(with_kids(plan(people=[w], base=40_000.0, end_age=50), ages=(10,)))
    assert proj.income["ccb"][0, 0] == pytest.approx(float(tax.child_benefit(80_000.0, 0, 1)))


def test_leftover_resp_growth_into_the_rrsp_respects_rrsp_room():
    p = plan(people=[person(age=60, rrsp_room=10_000.0)], base=0.0, end_age=62)
    p = replace(p, education=Education(kids=(Kid(name="K", age=21),), resp_balance=150_000.0,
                                       contributed=20_000.0, grants=0.0, contribute=False,
                                       student_grant=False))
    proj = run_flat(p)
    assert proj.balances["rrsp"][1, 0] == pytest.approx(10_000.0)


# -- one-time money events ----------------------------------------------------------



def test_money_in_is_saved_untaxed():
    p = replace(plan(base=0.0, end_age=65), events=(Event("Inheritance", 100_000.0, year=2027),))
    proj = run_flat(p)
    assert proj.income["other"][1, 0] == 100_000.0 and proj.tax[1, 0] == 0.0
    assert proj.investments[2, 0] - proj.investments[1, 0] == pytest.approx(100_000.0)


def test_money_out_repeats_every_n_years_until_the_end_year():
    car = Event("Car", -40_000.0, year=2027, every=3, until=2032)
    proj = run_flat(replace(plan(accounts=[Account("A", "tfsa", 2_000_000.0)], base=30_000.0, end_age=70),
                            events=(car,)))
    assert [proj.need[t, 0] for t in (0, 1, 2, 4, 7)] == [30_000.0, 70_000.0, 30_000.0, 70_000.0, 30_000.0]


def test_temporary_income_is_taxed_like_salary_and_stops():
    job = Event("Part-time", 30_000.0, kind="income", year=2027, until=2028)
    proj = run_flat(replace(plan(base=0.0, end_age=65), events=(job,)))
    assert proj.income["earned"][1, 0] == 30_000.0 and proj.income["earned"][3, 0] == 0.0
    expected = float(tax.income_tax(salary=30_000.0, age=61)) + float(tax.payroll_premiums(30_000.0))
    assert proj.tax[1, 0] == pytest.approx(expected, abs=1.0)


def test_income_by_age_belongs_to_its_person_and_stops_at_death():
    a = person(id="A", name="A", age=60)
    b = person(id="B", name="B", age=60)
    job = Event("B works", 20_000.0, kind="income", person="B", age=62, until_age=65)
    p = replace(plan(people=[a, b], accounts=[Account("A", "tfsa", 1_000_000.0)], base=30_000.0,
                     end_age=70), events=(job,))
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), np.array([[90], [64]]))
    assert [proj.income["earned"][t, 0] for t in (1, 2, 3, 4)] == [0.0, 20_000.0, 20_000.0, 0.0]


def test_sources_add_up_with_events():
    events = (Event("In", 50_000.0, year=2027), Event("Out", -20_000.0, year=2028),
              Event("Job", 25_000.0, kind="income", year=2026, until=2029))
    p = replace(plan(accounts=[Account("A", "rrsp", 300_000.0), Account("A", "tfsa", 50_000.0)],
                     base=40_000.0, end_age=70), events=events)
    proj = run_flat(p)
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


# -- guardrail spending ---------------------------------------------------------------

def _guarded(rate_path, end_age=100, **kw):
    p = plan(people=[person(age=60)], accounts=[Account("A", "tfsa", 1_000_000.0)], base=40_000.0,
             end_age=end_age, rule="guardrails", bad_market_cut=0.10, bad_market_trigger=0.80, **kw)
    return engine.simulate(p, np.full((engine.steps(p), 1), rate_path))


def test_guardrails_cut_spending_in_steps_when_the_withdrawal_rate_climbs():
    proj = _guarded(-0.25)
    # 4% to start; after one -25% year the rate is 5.6% (> 4.8%): cut 10%, and again.
    assert proj.need[0, 0] == 40_000.0
    assert proj.need[1, 0] == pytest.approx(36_000.0)           # the bad-market cut doesn't stack
    assert proj.need[2, 0] == pytest.approx(32_400.0)
    assert proj.spend_adjust[2, 0] == pytest.approx(0.81)


def test_guardrails_raise_spending_after_strong_years():
    proj = _guarded(0.30)
    assert proj.need[1, 0] == pytest.approx(40_000.0)            # 3.21%: not yet below 3.2%
    assert proj.need[2, 0] == pytest.approx(44_000.0)


def test_no_guardrail_cuts_in_the_last_years():
    proj = _guarded(-0.25, end_age=70)                           # every year is within the last 15
    assert proj.need[1, 0] == pytest.approx(40_000.0) and proj.need[2, 0] == pytest.approx(40_000.0)


def test_the_default_rule_never_adjusts_spending():
    proj = run_flat(plan(accounts=[Account("A", "tfsa", 1_000_000.0)], base=40_000.0, end_age=70))
    assert (proj.spend_adjust == 1.0).all()


# -- review fixes: events, premiums, guardrails ------------------------------------------

def test_a_one_off_cost_is_paid_from_savings_even_without_salaries():
    w = worker(salary=None, retire_age=50)
    base = plan(people=[w], accounts=[Account("A", "tfsa", 500_000.0)], base=40_000.0, end_age=55)
    with_car = run_flat(replace(base, events=(Event("Car", -100_000.0, year=2027),)))
    without = run_flat(base)
    assert without.investments[2, 0] - with_car.investments[2, 0] == pytest.approx(100_000.0, abs=1.0)
    assert with_car.income["earned"][1, 0] == without.income["earned"][1, 0]


def test_side_income_premiums_combine_with_the_salary_and_stop_cpp_at_70():
    w = worker(salary=105_000.0)
    job = Event("Consulting", 20_000.0, kind="income", year=2026, until=2026)
    base = plan(people=[w], base=40_000.0, end_age=50)
    with_job, without = run_flat(replace(base, events=(job,))), run_flat(base)
    assert with_job.premiums[0, 0] == pytest.approx(without.premiums[0, 0])     # already past every maximum
    old = plan(people=[person(age=72)], base=0.0, end_age=75)
    p = run_flat(replace(old, events=(Event("Part-time", 30_000.0, kind="income", year=2026, until=2026),)))
    assert p.premiums[0, 0] == pytest.approx(0.0163 * 30_000.0)                    # EI only at 72


def test_guardrails_wait_for_a_portfolio_before_setting_the_starting_rate():
    p = replace(plan(people=[person(age=60)], base=30_000.0, end_age=100, rule="guardrails"),
                events=(Event("Inheritance", 500_000.0, year=2028),))
    proj = run_flat(p)
    assert (proj.spend_adjust[:, 0] <= 1.0 + 1e-9).all()                        # never the endless raise


def test_guardrail_cuts_stop_at_the_floor_and_raises_at_the_ceiling():
    down = _guarded(-0.25)
    assert down.spend_adjust[:, 0].min() == pytest.approx(0.75)    # 0.9^3 = 0.729 → held at the floor
    up = _guarded(0.30)
    assert up.spend_adjust[:, 0].max() <= 1.5 + 1e-9


# -- TFSA top-up from non-registered money --------------------------------------------

LIMIT = rules.TFSA["annual_limit"].value


def _no_top_up(p):
    return replace(p, withdrawal=replace(p.withdrawal, tfsa_top_up=False))


def test_a_retiree_moves_new_tfsa_room_out_of_non_registered():
    p = plan(accounts=[Account("A", "nonreg", 500_000.0, cost=500_000.0)], base=0.0)
    proj = run_flat(p)
    assert proj.tfsa_top_up[0, 0] == LIMIT and proj.tfsa_top_up[1, 0] == LIMIT
    assert proj.balances["tfsa"][1, 0] == LIMIT and proj.balances["nonreg"][1, 0] == 500_000 - LIMIT
    assert proj.investments[1, 0] == pytest.approx(500_000.0)    # a transfer, not spending
    assert proj.tax[0, 0] == pytest.approx(0.0, abs=1e-6)          # no gain: nothing to tax
    off = run_flat(_no_top_up(p))
    assert off.tfsa_top_up[0, 0] == 0.0 and off.balances["tfsa"][1, 0] == 0.0


def test_moving_shares_in_realizes_their_gain():
    # Half the non-registered value is gain, so a 7,000 move realizes 3,500 of gain.
    p = plan(accounts=[Account("A", "nonreg", 500_000.0, cost=250_000.0)], base=0.0,
             people=[person(age=60)])
    proj, off = run_flat(p), run_flat(_no_top_up(p))
    expected = float(tax.income_tax(gains=LIMIT / 2, age=60))
    assert proj.tax[0, 0] - off.tax[0, 0] == pytest.approx(expected, abs=1.0)


def test_unused_room_carried_in_is_filled_too():
    p = plan(people=[person(tfsa_room=20_000.0)], base=0.0,
             accounts=[Account("A", "nonreg", 500_000.0, cost=500_000.0)])
    assert run_flat(p).tfsa_top_up[0, 0] == 20_000.0 + LIMIT


def test_a_partners_non_registered_money_fills_the_room():
    a, b = person(id="A", name="A"), person(id="B", name="B")
    p = plan(people=[a, b], base=0.0, accounts=[Account("B", "nonreg", 500_000.0, cost=500_000.0)])
    proj = run_flat(p)
    assert proj.tfsa_top_up[0, 0] == 2 * LIMIT                      # both TFSAs, from B's money


def test_no_top_up_while_working():
    p = plan(people=[person(age=55, retire_age=60)], base=0.0, end_age=62,
             accounts=[Account("A", "nonreg", 500_000.0, cost=500_000.0)])
    proj = run_flat(p)
    assert proj.tfsa_top_up[:5, 0].sum() == 0.0 and proj.tfsa_top_up[5, 0] > 0


# -- spousal RRSP ---------------------------------------------------------------------

def _couple(a_kw=None, b_kw=None, **plan_kw):
    a = person(id="A", name="A", **(a_kw or {}))
    b = person(id="B", name="B", **(b_kw or {}))
    return plan(people=[a, b], **plan_kw)


def test_a_spousal_contribution_lands_in_the_partners_rrsp():
    worker = {"age": 50, "retire_age": 55, "salary": 120_000.0, "contributions": {"spousal_rrsp": 10_000.0}}
    p = _couple(worker, {"age": 50, "retire_age": 55}, base=0.0, end_age=56)
    proj = run_flat(p)
    assert proj.balances["rrsp"][1, 0] == pytest.approx(10_000.0)       # B's RRSP holds it
    assert proj.spousal_rrsp[0, 0, 0] == 10_000.0 and proj.spousal_rrsp[0, 1, 0] == 0.0
    own = _couple({**worker, "contributions": {"rrsp": 10_000.0}}, {"age": 50, "retire_age": 55},
                  base=0.0, end_age=56)
    assert proj.tax[0, 0] == pytest.approx(run_flat(own).tax[0, 0], abs=1.0)   # same deduction for A


def test_spousal_and_own_contributions_share_the_contributors_room():
    worker = {"age": 50, "retire_age": 55, "salary": 120_000.0, "rrsp_room": 12_000.0,
              "contributions": {"rrsp": 8_000.0, "spousal_rrsp": 8_000.0}}
    proj = run_flat(_couple(worker, {"age": 50, "retire_age": 55}, base=0.0, end_age=56))
    # 16,000 planned against 12,000 of room: own RRSP trimmed first, 4,000 to the TFSA.
    assert proj.balances["rrsp"][1, 0] == pytest.approx(12_000.0)
    assert proj.balances["tfsa"][1, 0] == pytest.approx(4_000.0)


def _parts_pair():
    zero = np.zeros(1)
    mk = lambda age: {"ordinary": np.array([30_000.0]), "pension": zero.copy(), "gains": zero,
                      "dividends": zero, "oas": zero, "age": age}
    return [mk(60), mk(60)]


def test_attribution_moves_recent_spousal_withdrawals_to_the_contributor():
    parts = _parts_pair()
    draw = np.array([[0.0], [30_000.0]])
    bal = np.array([[0.0], [50_000.0]])
    spousal = np.array([[0.0], [40_000.0]])            # B has 10,000 of own money
    recent = np.zeros((3, 2, 1)); recent[1, 1] = 15_000.0
    alive = np.ones((2, 1), dtype=bool)
    engine._attribute(parts, [60, 60], [False, False], draw, bal, spousal, recent, alive)
    # 30,000 drawn: 10,000 own money first, 20,000 spousal, of which 15,000 is recent.
    assert parts[1]["ordinary"][0] == pytest.approx(15_000.0)
    assert parts[0]["ordinary"][0] == pytest.approx(45_000.0)


def test_no_attribution_when_own_money_covers_the_draw_or_the_contributor_died():
    draw = np.array([[0.0], [10_000.0]])
    bal = np.array([[0.0], [50_000.0]])
    spousal = np.array([[0.0], [20_000.0]])
    recent = np.zeros((3, 2, 1)); recent[0, 1] = 20_000.0
    parts = _parts_pair()
    engine._attribute(parts, [60, 60], [False, False], draw, bal, spousal, recent,
                      np.ones((2, 1), dtype=bool))
    assert parts[0]["ordinary"][0] == parts[1]["ordinary"][0] == 30_000.0
    parts = _parts_pair()
    engine._attribute(parts, [60, 60], [False, False], np.array([[0.0], [50_000.0]]), bal, spousal,
                      recent, np.array([[False], [True]]))
    assert parts[0]["ordinary"][0] == parts[1]["ordinary"][0] == 30_000.0



# -- inflation ------------------------------------------------------------------------------

def test_inflation_erodes_the_cost_base_so_gains_are_nominal():
    # A retiree sells non-registered money bought at today's prices; with 3% inflation
    # its book value is 3% lower in today's dollars each year, so selling it later
    # realizes a (nominal) gain and costs tax even with flat real returns.
    p = plan(accounts=[Account("A", "nonreg", 6_000_000.0, cost=6_000_000.0)], base=400_000.0,
             end_age=75)
    p = replace(p, withdrawal=replace(p.withdrawal, tfsa_top_up=False))
    flat = run_flat(p)
    hot = run_flat(replace(p, returns=replace(p.returns, inflation=0.03)))
    assert flat.tax.sum() == pytest.approx(0.0, abs=1.0)              # no real gain, no tax
    assert hot.tax.sum() > 10_000
    # Money left at death carries the same nominal gain into the tax at death.
    q = replace(p, spending=replace(p.spending, base=150_000.0))
    assert run_flat(replace(q, returns=replace(q.returns, inflation=0.03))).death_tax[0] > run_flat(q).death_tax[0]


def test_default_inflation_is_canadas_40_year_average():
    assert Returns().inflation is None
    assert Returns().inflation_rate == pytest.approx(rules.historical_inflation())
    assert 0.02 < rules.historical_inflation() < 0.03
    assert min(rules.CPI.value) == 1950 and max(rules.CPI.value) == rules.CPI.year
    assert rules.historical_inflation() == pytest.approx(0.0242, abs=5e-5)   # still the last 40 years


# -- inflation paths and the return lag ----------------------------------------------------

def _shocky(**ret):
    p = plan()
    return replace(p, returns=replace(p.returns, **{"inflation": None, "inflation_shocks": True, **ret}))


def _centered_history():
    hist = np.array([rules.CPI.value[y] for y in sorted(rules.CPI.value)])
    whole = rules.historical_inflation(len(hist))
    return list((1 + hist) * (1 + rules.historical_inflation()) / (1 + whole) - 1)


def test_inflation_paths_are_5_year_blocks_of_history():
    hist = _centered_history()
    I = engine.draw_inflation(_shocky(), paths=50, years=12, seed=3)
    assert I.shape == (12, 50)
    for n in range(50):
        for start in (0, 5):                      # each full block is a run of history (wrapping)
            block = list(I[start:start + 5, n])
            assert any(np.allclose((hist + hist)[k:k + 5], block, rtol=0, atol=1e-12)
                       for k in range(len(hist)))
    assert np.array_equal(I, engine.draw_inflation(_shocky(), 50, 12, 3))   # seeded


def test_inflation_paths_reach_the_1970s_but_centre_on_the_last_40_years():
    I = engine.draw_inflation(_shocky(), paths=2_000, years=40, seed=4)
    assert I.max() > 0.10                 # 1981's 12.5%, centred: about 11.3%
    assert (I > 0.08).sum(axis=0).max() >= 5   # a run of high-inflation years in one future
    assert np.exp(np.log1p(I).mean()) - 1 == pytest.approx(rules.historical_inflation(), abs=1e-3)


def test_paths_cover_horizons_longer_than_the_history():
    I = engine.draw_inflation(_shocky(), paths=3, years=73, seed=1)
    assert I.shape == (73, 3) and np.isfinite(I).all()


def test_a_fixed_inflation_rate_means_no_shocks():
    I = engine.draw_inflation(_shocky(inflation=0.03), paths=4, years=6, seed=1)
    assert np.all(I == 0.03)
    off = replace(_shocky(), returns=replace(_shocky().returns, inflation_shocks=False))
    assert np.allclose(engine.draw_inflation(off, 4, 6, 1), rules.historical_inflation())


def test_returns_lag_inflation_only_in_the_short_run():
    avg = 0.025
    R = np.array([[0.05], [0.05], [0.05]])
    I = np.array([[avg], [0.068], [0.003]])
    lagged = engine.lag_returns(R, I, avg)
    assert lagged[0, 0] == pytest.approx(0.05)
    assert lagged[1, 0] < 0.05 and lagged[2, 0] > 0.05


def test_inflation_draws_leave_returns_and_lifespans_alone():
    p = _shocky()
    R1, D1, I1 = engine.draw_futures(p, 30, 5)
    steady = replace(p, returns=replace(p.returns, inflation_shocks=False))
    R0, D0, I0 = engine.draw_futures(steady, 30, 5)
    assert np.array_equal(D1, D0)
    unlagged = (1 + R1) * (1 + I1) / (1 + rules.historical_inflation()) - 1
    assert np.allclose(unlagged, R0)


def test_price_level_and_nominal_cost_base_follow_each_path():
    p = plan(accounts=[Account("A", "nonreg", 1_000_000.0, cost=1_000_000.0)], base=0.0, end_age=63)
    p = replace(p, withdrawal=replace(p.withdrawal, tfsa_top_up=False))
    T = engine.steps(p)
    infl = np.array([[0.10], [0.0], [0.0]])[:T]
    proj = engine.simulate(p, np.zeros((T, 1)), None, infl)
    assert proj.price_level[0, 0] == 1.0 and proj.price_level[1, 0] == pytest.approx(1.10)
    assert proj.inflation[0, 0] == 0.10



# -- costs that grow faster (or slower) than inflation --------------------------------------

def _grow(p, **rates):
    return replace(p, cost_growth=CostGrowth(**rates))


def test_zero_growth_and_steady_inflation_change_nothing():
    p = plan(accounts=[Account("A", "rrsp", 500_000.0)], base=40_000.0, end_age=66,
             home=Home(value=800_000.0, property_tax=6_000.0, insurance=2_000.0))
    zero = _grow(p, base=0.0, care=0.0, education=0.0, property_tax=0.0, insurance=0.0)
    np.testing.assert_allclose(run_flat(zero).need[:, 0], 40_000.0)


def test_base_and_home_costs_grow_at_their_own_rates():
    p = plan(base=40_000.0, end_age=66, home=Home(value=800_000.0, property_tax=6_000.0, insurance=2_000.0),
             accounts=[Account("A", "tfsa", 2_000_000.0)])
    g = _grow(p, base=0.01, property_tax=0.05, insurance=0.0, care=0.0, education=0.0)
    need = run_flat(g).need[:, 0]
    t = 4
    expected = (40_000 - 8_000) * 1.01 ** t + 6_000 * 1.05 ** t + 2_000
    assert need[t] == pytest.approx(expected)


def test_downsizing_scales_the_grown_home_costs():
    home = Home(value=800_000.0, downsize_age=62, new_value=400_000.0, property_tax=6_000.0, insurance=2_000.0)
    p = plan(base=40_000.0, end_age=66, home=home, accounts=[Account("A", "tfsa", 2_000_000.0)])
    g = _grow(p, base=0.0, property_tax=0.05, insurance=0.0, care=0.0, education=0.0)
    need = run_flat(g).need[:, 0]
    t = 3                                            # after downsizing at 62 (age 60 at t=0)
    expected = 32_000 + (6_000 * 1.05 ** t + 2_000) * 0.5
    assert need[t] == pytest.approx(expected)


def test_spending_can_lag_inflation():
    p = plan(base=40_000.0, end_age=66, accounts=[Account("A", "tfsa", 2_000_000.0)])
    need = run_flat(_grow(p, base=-0.01, care=0.0)).need[:, 0]
    assert need[5] == pytest.approx(40_000 * 0.99 ** 5)


def test_cost_growth_without_a_home():
    p = plan(base=40_000.0, end_age=63, accounts=[Account("A", "tfsa", 1_000_000.0)])
    need = run_flat(_grow(p, property_tax=0.1, insurance=0.1, base=0.0)).need[:, 0]
    np.testing.assert_allclose(need, 40_000.0)


def test_care_costs_grow():
    p = plan(base=0.0, end_age=66, accounts=[Account("A", "tfsa", 2_000_000.0)],
             no_go_age=60, care=10_000.0)
    need = run_flat(_grow(p, care=0.02, base=0.0)).need[:, 0]
    assert need[3] == pytest.approx(10_000 * 1.02 ** 3)


def test_fixed_amounts_shrink_in_the_engine():
    p = plan(people=[person(age=70, rrif_start_age=65)], accounts=[Account("A", "rrsp", 400_000.0)],
             base=30_000.0, end_age=75)
    p = _grow(p, base=0.0, care=0.0, education=0.0, property_tax=0.0, insurance=0.0)
    T = engine.steps(p)
    steady = engine.simulate(p, np.zeros((T, 1)), None, np.zeros((T, 1)))
    hot = engine.simulate(p, np.zeros((T, 1)), None, np.full((T, 1), 0.10))
    assert hot.tax[4, 0] > steady.tax[4, 0]              # a smaller pension credit


def test_shock_paths_average_the_history_so_the_lag_is_unbiased():
    I = engine.draw_inflation(_shocky(), paths=20_000, years=40, seed=2)
    lag = np.log((1 + rules.historical_inflation()) / (1 + I))
    assert abs(lag.mean()) < 3e-4        # every year of history weighs the same
