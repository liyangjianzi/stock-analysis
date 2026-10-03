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
