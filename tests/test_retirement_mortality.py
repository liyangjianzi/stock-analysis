"""Lifespans from the Statistics Canada Alberta life table with CPP-report
mortality improvement. Offline; invented people."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import mortality, rules
from stockanalysis.retirement.inputs import Person


def person(age=50, sex="female", **kw) -> Person:
    return Person(id="A", name="A", age=age, retire_age=max(age, 60), sex=sex, **kw)


def test_table_covers_ages_0_to_110_and_ends_certain():
    for sex in mortality.SEXES:
        q = mortality.QX.value[sex]
        assert len(q) == mortality.OMEGA == 111
        assert q[-1] == 1.0 and all(0 < x <= 1 for x in q)
    assert mortality.QX.value["female"][65] == 0.00763      # spot value from table 13-10-0114-01
    assert mortality.QX.value["male"][65] == 0.01226


def test_improvement_is_applied_per_year_from_the_table_year():
    base = mortality.QX.value["male"][70]
    assert mortality.qx("male", 70, mortality.TABLE_YEAR) == base
    assert mortality.qx("male", 70, mortality.TABLE_YEAR + 10) == pytest.approx(base * 0.99 ** 10)
    assert mortality.qx("male", 92, mortality.TABLE_YEAR + 5) == pytest.approx(
        mortality.QX.value["male"][92] * 0.994 ** 5)
    assert mortality.qx("male", 110, 2080) == 1.0


def test_no_sex_uses_the_average_of_both_tables():
    f, m = mortality.qx("female", 80, 2030), mortality.qx("male", 80, 2030)
    assert mortality.qx(None, 80, 2030) == pytest.approx((f + m) / 2)


def test_draws_are_seeded_conditional_on_age_and_capped():
    people = (person(50, "female"), person(105, "male"))
    a = mortality.draw_death_ages(people, 2026, 2_000, seed=3)
    b = mortality.draw_death_ages(people, 2026, 2_000, seed=3)
    assert a.shape == (2, 2_000) and a.dtype.kind == "i"
    np.testing.assert_array_equal(a, b)
    assert a[0].min() >= 51 and a[1].min() >= 106                 # alive through this year
    assert a.max() <= mortality.OMEGA


def test_sample_median_matches_median_death_age():
    p = person(48, "male")
    draws = mortality.draw_death_ages((p,), 2026, 20_000, seed=1)[0]
    assert abs(np.median(draws) - mortality.median_death_age(p, 2026)) <= 1


def test_women_live_longer_and_improvement_lengthens_life():
    f, m = person(50, "female"), person(50, "male")
    assert mortality.median_death_age(f, 2026) > mortality.median_death_age(m, 2026)
    later = mortality.median_death_age(m, 2060)
    assert later >= mortality.median_death_age(m, 2026)
