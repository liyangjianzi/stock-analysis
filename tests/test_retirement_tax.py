"""Canadian personal tax, checked against hand-worked 2026 federal + Alberta cases."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import rules, tax


def t(**kw) -> float:
    return float(tax.income_tax(**kw))


def test_no_tax_at_the_basic_personal_amount():
    assert t() == 0.0
    assert t(ordinary=16_452, age=50) == pytest.approx(0.0, abs=1e-6)


def test_mid_income_federal_plus_alberta():
    # Fed: .14*58,523 + .205*1,477 - .14*16,452 = 6,192.725
    # AB:  .08*60,000 - .08*22,769 = 2,978.48
    assert t(ordinary=60_000, age=50) == pytest.approx(9_171.205, abs=0.01)


def test_senior_with_pension_income_gets_age_and_pension_credits():
    # net 49,150. Fed: 6,881 - .14*(16,452 + 8,800.3 + 2,000) = 3,065.678
    # AB: 3,932 - .08*(22,769 + 6,057.6 + 1,753) = 1,485.632
    assert t(pension=40_000, oas=9_150, age=70) == pytest.approx(4_551.31, abs=0.01)


def test_age_amount_phases_out():
    spec = rules.FEDERAL["age_amount"].value
    assert tax.age_amount(np.array([46_432.0]), 70, spec)[0] == 9_208
    assert tax.age_amount(np.array([107_819.0]), 70, spec)[0] == 0.0
    assert tax.age_amount(np.array([20_000.0]), 64, spec)[0] == 0.0


def test_pension_credit_only_from_65():
    assert t(pension=30_000, age=64) == pytest.approx(t(ordinary=30_000, age=64))
    assert t(pension=30_000, age=65) < t(ordinary=30_000, age=65)


def test_only_half_a_capital_gain_is_taxed():
    assert t(gains=20_000, age=50) == pytest.approx(t(ordinary=10_000, age=50))


def test_oas_recovery_starts_at_threshold_and_is_capped():
    assert float(tax.oas_recovery(95_323.0, 9_150.0)) == 0.0
    assert float(tax.oas_recovery(109_150.0, 9_150.0)) == pytest.approx(2_074.05)
    assert float(tax.oas_recovery(500_000.0, 9_150.0)) == 9_150.0


def _part(pension, age, ordinary=0.0):
    z = np.zeros(3)
    return {"ordinary": z + ordinary, "pension": z + pension, "gains": z, "oas": z, "age": age}


def test_best_split_reaches_the_equal_split_tax():
    # Tax is flat from a 45% to a 50% split here (same brackets, no phase-out), so
    # assert the minimum is reached, not which tied share is picked.
    a, b = _part(80_000, 70), _part(0, 70)
    ta, tb, share = tax.couple_tax(a, b)
    np.testing.assert_allclose(ta + tb, tax.household_tax([a, b], 0.5))
    assert np.all(ta + tb < tax.household_tax([a, b], 0.0))
    assert np.all(share > 0)                       # A's pension moves to B
    ta2, tb2, share2 = tax.couple_tax(b, a)
    assert np.all(share2 < 0)                      # mirrored: B's moves to A
    np.testing.assert_allclose(ta + tb, ta2 + tb2)


def test_no_split_before_65():
    a, b = _part(80_000, 60), _part(0, 60)
    _, _, share = tax.couple_tax(a, b)
    assert np.all(share == 0.0)


def test_split_never_costs_more_than_no_split():
    rng = np.random.default_rng(0)
    a = {"ordinary": rng.uniform(0, 50_000, 50), "pension": rng.uniform(0, 90_000, 50),
         "gains": rng.uniform(0, 20_000, 50), "oas": np.full(50, 9_150.0), "age": 72}
    b = {"ordinary": rng.uniform(0, 50_000, 50), "pension": rng.uniform(0, 90_000, 50),
         "gains": np.zeros(50), "oas": np.full(50, 9_150.0), "age": 70}
    ta, tb, _ = tax.couple_tax(a, b)
    assert np.all(ta + tb <= tax.household_tax([a, b], 0.0) + 1e-9)


def test_age_amount_uses_net_income_after_the_oas_repayment():
    # 100,000 incl. 9,150 OAS at 70: repayment .15*(100,000-95,323) = 701.55 is deducted
    # (line 23500), so net and taxable income are 99,298.45 for the brackets AND the age amount.
    # Fed .14*58,523 + .205*40,775.45 - .14*(16,452 + 1,278.0325) = 14,069.982
    # AB  .08*61,200 + .10*38,098.45 - .08*22,769 (age amount fully phased out) = 6,884.325
    assert t(ordinary=90_850, oas=9_150, age=70) == pytest.approx(14_069.982 + 6_884.325 + 701.55, abs=0.01)


def test_eligible_dividends_alone_are_tax_free_up_to_a_point_in_alberta():
    # $50k received -> 69,000 grossed up. Fed: 8,193.22 + .205*10,477 = 10,341.005 against
    # credits .14*16,452 + .150198*69,000 = 12,666.94 -> 0. AB: 4,896 + 780 = 5,676 against
    # .08*22,769 + .0812*69,000 = 7,424.32 -> 0.
    assert t(dividends=50_000, age=50) == pytest.approx(0.0, abs=1e-6)


def test_eligible_dividends_hand_worked():
    # $100k received -> 138,000. Fed: 8,193.22 + .205*58,522 + .26*20,955 = 25,638.53
    #   minus .14*16,452 + .150198*138,000 = 23,030.604 -> 2,607.926. AB credits exceed tax -> 0.
    assert t(dividends=100_000, age=50) == pytest.approx(2_607.926, abs=0.01)


def test_the_gross_up_counts_toward_the_oas_recovery():
    # 80,000 * 1.38 + 9,150 = 119,550 total income: .15 * (119,550 - 95,323) = 3,634.05.
    gross = tax.total_income(dividends=80_000, oas=9_150)
    assert float(gross) == pytest.approx(119_550)
    assert float(tax.oas_recovery(gross, 9_150)) == pytest.approx(3_634.05, abs=0.01)
    # So adding OAS on top costs the recovery plus ordinary tax on the 9,150.
    added = t(dividends=80_000, oas=9_150, age=70) - t(dividends=80_000, age=70)
    assert added > 3_634.05


def test_extra_tax_is_the_tax_on_top_of_a_base():
    assert float(tax.extra_tax(150_000, ordinary=10_000, age=50)) == pytest.approx(
        t(ordinary=160_000, age=50) - t(ordinary=150_000, age=50))


def test_eligible_dividends_are_taxed_below_ordinary_income():
    assert t(dividends=20_000, age=50) < t(ordinary=20_000, age=50)


def test_payroll_premiums_cpp_cpp2_and_ei():
    # Above both ceilings: the three published maximums.
    assert float(tax.payroll_premiums(140_000)) == pytest.approx(4_230.45 + 416.0 + 1_123.07, abs=0.01)
    # $50k: CPP on earnings above the exemption, no CPP2, EI on all of it.
    assert float(tax.payroll_premiums(50_000)) == pytest.approx(0.0595 * 46_500 + 0.0163 * 50_000, abs=0.01)
    assert float(tax.payroll_premiums(0)) == 0.0


# -- child benefit, childcare, RRSP room ---------------------------------------------

def test_child_benefit_in_each_income_band():
    # Two children aged 6-17: the maximum below the first threshold.
    assert float(tax.child_benefit(30_000, 0, 2)) == pytest.approx(2 * 6_883)
    # First band: 13.5% of income over 38,237.
    assert float(tax.child_benefit(60_000, 0, 2)) == pytest.approx(2 * 6_883 - 0.135 * (60_000 - 38_237))
    # Second band, one child under 6: 3,123 + 3.2% over 82,847.
    assert float(tax.child_benefit(150_000, 1, 0)) == pytest.approx(
        max(8_157 - (3_123 + 0.032 * (150_000 - 82_847)), 0.0))
    assert float(tax.child_benefit(500_000, 0, 1)) == 0.0          # never negative
    assert float(tax.child_benefit(40_000, 0, 0)) == 0.0           # no children
    assert float(tax.child_benefit(np.inf, 0, 2)) == 0.0           # unknown income: none


def test_childcare_deduction_limits():
    # Ages 5 and 12: up to 8,000 + 5,000; capped by expenses and 2/3 of earned income.
    assert float(tax.childcare_deduction(20_000, (5, 12), 100_000)) == 13_000
    assert float(tax.childcare_deduction(6_000, (5, 12), 100_000)) == 6_000
    assert float(tax.childcare_deduction(20_000, (5, 12), 9_000)) == pytest.approx(6_000)
    assert float(tax.childcare_deduction(20_000, (16, 17), 100_000)) == 0.0    # 16+: none
    assert float(tax.childcare_deduction(20_000, (12,), 0.0)) == 0.0           # no earned income


def test_new_rrsp_room_is_18_percent_less_the_pension_adjustment():
    assert float(tax.rrsp_new_room(140_000, 13_680)) == pytest.approx(0.18 * 140_000 - 13_680)
    assert float(tax.rrsp_new_room(300_000, 0)) == 33_810                       # the dollar limit
    assert float(tax.rrsp_new_room(50_000, 20_000)) == 0.0                      # never negative


def test_salary_gets_payroll_credits_the_enhanced_cpp_deduction_and_the_employment_amount():
    # $100k salary, age 40, AB (T4127 2026): base CPP 4.95% and EI earn credits at the
    # lowest rates; enhanced CPP (1.00%) + CPP2 are deducted; the Canada employment
    # amount is a federal credit.
    pensionable = 74_600 - 3_500
    base_cpp, ei = 0.0495 * pensionable, 1_123.07
    deduct = 0.01 * pensionable + 0.04 * (85_000 - 74_600)
    net = 100_000 - deduct
    fed = tax.bracket_tax(net, rules.FEDERAL["brackets"].value) - 0.14 * (16_452 + base_cpp + ei + 1_501)
    ab = tax.bracket_tax(net, rules.PROVINCIAL["AB"]["brackets"].value) - 0.08 * (22_769 + base_cpp + ei)
    assert float(tax.income_tax(salary=100_000, age=40)) == pytest.approx(float(fed + ab), abs=0.01)


def test_deductions_lower_net_income():
    with_ded = float(tax.income_tax(ordinary=80_000, deductions=10_000, age=40))
    assert with_ded == pytest.approx(float(tax.income_tax(ordinary=70_000, age=40)))


def test_unknown_income_means_no_child_benefit():
    assert float(tax.child_benefit(np.nan, 0, 1)) == 0.0
