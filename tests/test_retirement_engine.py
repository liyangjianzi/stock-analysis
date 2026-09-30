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
