"""The rules table: every statutory value cited, and the lookup helpers."""
from __future__ import annotations

import pytest

from stockanalysis.retirement import rules


def test_every_rule_value_is_cited_with_year_and_https_source():
    found = rules.all_rules()
    assert len(found) >= 20
    for name, rule in found:
        assert isinstance(rule.year, int) and 2016 <= rule.year <= rules.TAX_YEAR, name
        assert rule.source.startswith("https://"), name


def _check_brackets(brackets):
    uppers = [u for u, _ in brackets]
    rates = [r for _, r in brackets]
    assert uppers == sorted(uppers) and len(set(uppers)) == len(uppers)
    assert uppers[-1] == rules.INF
    assert all(0 < r < 1 for r in rates) and rates == sorted(rates)


def test_brackets_ascend_and_rates_are_fractions():
    _check_brackets(rules.FEDERAL["brackets"].value)
    for prov in rules.PROVINCIAL.values():
        _check_brackets(prov["brackets"].value)


def test_rrif_minimum_factors():
    table = rules.RRIF["factors"].value
    assert set(table) == set(range(71, 96))
    assert rules.rrif_min_factor(70) == 1 / 20
    assert rules.rrif_min_factor(71) == 0.0528
    assert rules.rrif_min_factor(99) == 0.20
    values = [table[a] for a in range(71, 96)]
    assert values == sorted(values)


def test_lif_maximum_covers_50_to_89_and_never_undercuts_the_minimum():
    table = rules.LIF["AB"]["max_pct"].value
    assert set(table) == set(range(50, 90))
    assert rules.lif_max_pct("AB", 45) == table[50]
    assert rules.lif_max_pct("AB", 95) == 1.0
    for age in range(50, 90):
        assert rules.lif_max_pct("AB", age) >= rules.rrif_min_factor(age)


def test_top_marginal_rate_alberta():
    assert abs(rules.top_marginal_rate("AB") - 0.48) < 1e-12


def test_is_stale():
    assert not rules.is_stale(rules.TAX_YEAR)
    assert rules.is_stale(rules.TAX_YEAR + 1)


def test_cpp_survivor_rules_are_consistent_with_the_2026_maximums():
    s = rules.CPP["survivor"].value
    max65 = rules.CPP["max_monthly_at_65"].value
    assert s["share_65"] * max65 == pytest.approx(904.59, abs=0.01)        # published 65+ maximum
    assert s["flat_monthly"] + s["share_under_65"] * max65 == pytest.approx(803.54, abs=0.01)
    assert rules.CPP["death_benefit"].value == 2_500.0


def test_payroll_maximums_match_the_published_2026_figures():
    p = rules.PAYROLL
    cpp, cpp2, ei = p["cpp"].value, p["cpp2"].value, p["ei"].value
    assert cpp["rate"] * (cpp["ympe"] - cpp["exemption"]) == pytest.approx(4_230.45, abs=0.01)
    assert cpp2["rate"] * (cpp2["yampe"] - cpp["ympe"]) == pytest.approx(416.0, abs=0.01)
    assert ei["rate"] * ei["max_insurable"] == pytest.approx(1_123.07, abs=0.01)


def test_ccb_second_band_starts_where_the_first_ends():
    c = rules.CCB.value
    span = c["threshold_2"] - c["threshold_1"]
    for n, (rate1, base2, _) in c["reduction"].items():
        assert rate1 * span == pytest.approx(base2, abs=1.0), n      # e.g. 7% x 44,610 = 3,123
    assert rules.RRSP_LIMIT.value["dollar_limit"] == 33_810
    assert rules.CHILDCARE.value["under_7"] == 8_000 and rules.CHILDCARE.value["7_to_15"] == 5_000


def test_cost_growth_rules_are_rates_above_cpi():
    assert set(rules.COST_GROWTH) == {"care", "education", "property_tax", "insurance"}
    for name, rule in rules.COST_GROWTH.items():
        assert -0.05 <= rule.value <= 0.15, name


def test_return_history_covers_the_same_years_as_cpi():
    years = list(range(1928, rules.CPI.year + 1))
    assert sorted(rules.CPI.value) == years
    assert sorted(rules.US_RETURNS.value) == years
    assert sorted(rules.US_CPI.value) == years
    assert rules.US_RETURNS.value[1931] == (-0.4384, -0.0256)    # S&P 500, 10-year Treasury
    assert rules.US_CPI.value[1932] == pytest.approx(-0.09868)
    assert rules.CPI.value[1948] == pytest.approx(0.14263)
    assert rules.CPI.value[1986] == pytest.approx(0.04195)       # stored years untouched


def test_return_assumptions_are_fp_canadas_made_real():
    assert rules.RETURN_ASSUMPTIONS.value == {"inflation": 0.021, "fixed_income": 0.032,
                                              "canadian_equities": 0.063}
    assert rules.real_return("canadian_equities") == pytest.approx(1.063 / 1.021 - 1)
    assert rules.real_return("fixed_income") == pytest.approx(1.032 / 1.021 - 1)


def test_new_series_are_listed_for_the_yearly_refresh():
    names = {n.split(".")[0] for n, _ in rules.all_rules()}
    assert {"us_returns", "us_cpi", "return_assumptions"} <= names
