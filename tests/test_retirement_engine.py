"""The retirement engine: benefits, returns and the year-by-year model (zero-volatility
cases, so every number is predictable). Invented households only."""
from __future__ import annotations

import numpy as np
import pytest

from dataclasses import replace

from stockanalysis.retirement import engine, rules, tax
from stockanalysis.retirement.inputs import (Account, Home, NonregIncome, Person, PlanInputs,
                                             Returns, Spending, SpendingChange, Withdrawal)


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
    p = replace(p, returns=Returns(0.05, 0.15, 200, 3))
    result = engine.run(p)
    assert result.average_return == engine.median_return(0.05, 0.15)
    assert result.average.paths == 1 and result.simulated.paths == 200
    assert len(result.simulated.years) == engine.life_steps(p)
    assert len(result.average.years) == engine.steps(p)
    R, D = engine.draw_futures(p, 200, 3)
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
    pay = float(tax.income_tax(ordinary=150_000, age=50)) + float(tax.payroll_premiums(150_000))
    assert proj.tax[0, 0] == pytest.approx(pay + due, abs=0.01)        # salary tax + the payout tax
    assert proj.income["nonreg"][0, 0] == pytest.approx(due, abs=0.01)  # sold from the account
    assert proj.investments[1, 0] == pytest.approx(500_000.0 - due + proj.saved[0, 0], abs=0.01)
    low = run_flat(replace(p, people=(replace(worker, salary=40_000.0),)))
    low_pay = float(tax.income_tax(ordinary=40_000, age=50)) + float(tax.payroll_premiums(40_000))
    assert low.tax[0, 0] - low_pay < due                                 # a lower marginal rate


def test_no_payouts_leave_only_salary_tax_in_working_years():
    worker = person(age=50, retire_age=55, salary=150_000.0)
    proj = run_flat(plan(people=[worker], accounts=[Account("A", "nonreg", 500_000.0)],
                         base=40_000.0, end_age=56))
    pay = float(tax.income_tax(ordinary=150_000, age=50)) + float(tax.payroll_premiums(150_000))
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
    R, _ = engine.draw_futures(p, 300, 3)
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
    expected = (float(tax.income_tax(ordinary=100_000.0 - 10_000.0 - own_pension, age=40))
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
