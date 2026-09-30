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
