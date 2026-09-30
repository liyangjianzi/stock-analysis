"""The rules table: every statutory value cited, and the lookup helpers."""
from __future__ import annotations

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
