"""Children's education and the family RESP. Invented households, flat returns."""
from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from stockanalysis.retirement import education, engine, inputs, rules
from stockanalysis.retirement.inputs import Account, CostGrowth, NonregIncome

CESG = rules.RESP["cesg"].value


def plan_with(kids, **edu):
    """The template household (no payouts, cost growth or inflation, to isolate the RESP)."""
    d = copy.deepcopy(inputs.TEMPLATE)
    d["education"] = {"kids": kids, **edu}
    p = inputs.parse(d)
    return replace(p, nonreg_income=NonregIncome(), cost_growth=CostGrowth(0.0, 0.0, 0.0, 0.0, 0.0),
                   returns=replace(p.returns, inflation=0.0))


def flat(p):
    return engine.simulate(p, np.zeros((engine.steps(p), 1)))


# -- the schedule -------------------------------------------------------------------

def test_grant_estimate_assumes_every_year_was_collected():
    assert education.estimated_grant_received(2014, 2026) == 13 * 500       # 2014..2026
    assert education.estimated_grant_received(2010, 2026) == CESG["lifetime_max"]
    assert education.estimated_grant_received(2000, 2026) == (2017 - 2007 + 1) * 500  # room from 2007, to 17


def test_contributions_only_while_they_earn_the_grant():
    p = plan_with([{"name": "Old", "age": 15}, {"name": "Young", "age": 11}], resp_balance=100_000)
    s = education.schedule(p, engine.steps(p))
    assert s.contribution[0] == 0.0                     # this year's is already in the balance
    old, young = s.kids
    # Born 2011: 2011..2026 is 16 years at 500 = 8,000, so the 7,200 lifetime is reached.
    assert old.contributions == 0.0 and old.grant_left == 0.0
    # Born 2015: 12 years = 6,000 so far, 1,200 left: 2,500 (500), 2,500 (500), then 1,000 (200).
    assert young.grants == pytest.approx(1_200.0) and young.contributions == pytest.approx(6_000.0)
    np.testing.assert_allclose(s.contribution[1:4], [2_500.0, 2_500.0, 1_000.0])
    np.testing.assert_allclose(s.grant, s.contribution * CESG["rate"])


def test_unused_room_is_caught_up_at_up_to_1000_a_year():
    p = plan_with([{"name": "K", "age": 10, "cesg_received": 0}], resp_balance=0)
    s = education.schedule(p, engine.steps(p))
    assert s.grant[1] == CESG["yearly_max_catch_up"] and s.contribution[1] == 5_000
    # Ages 11..17 at up to 1,000 a year: 7 years catch up 7,000 of the 7,200 lifetime.
    assert s.kids[0].grants == pytest.approx(7_000) and s.kids[0].grant_left == pytest.approx(200)


def test_no_contributions_when_turned_off():
    p = plan_with([{"name": "K", "age": 10, "cesg_received": 0}], contribute=False)
    assert education.schedule(p, engine.steps(p)).contribution.sum() == 0.0


def test_school_years_and_costs_follow_living_choice():
    p = plan_with([{"name": "K", "age": 15, "living": "home"}], costs={"home": 10_000, "away": 30_000})
    s = education.schedule(p, engine.steps(p))
    assert s.kids[0].school_years == (2029, 2030, 2031, 2032)
    np.testing.assert_array_equal(np.nonzero(s.cost)[0], [3, 4, 5, 6])
    assert s.cost[3] == 10_000 and s.end_step == 7


# -- the engine ---------------------------------------------------------------------

def test_the_resp_pays_school_first_and_the_household_pays_the_rest():
    kids = [{"name": "K", "age": 18, "years": 2, "living": "away"}]
    covered = flat(plan_with(kids, resp_balance=100_000, contribute=False))
    short = flat(plan_with(kids, resp_balance=30_000, contribute=False))
    assert covered.education[0, 0] == 0.0 and covered.education[1, 0] == 0.0
    assert short.education[0, 0] == pytest.approx(0.0) and short.education[1, 0] == pytest.approx(20_000)
    for proj in (covered, short):
        total = sum(proj.income[s] for s in engine.SOURCES)
        np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)


def test_leftover_contributions_come_back_and_growth_goes_to_the_rrsp():
    p = plan_with([{"name": "K", "age": 22, "years": 1}], resp_balance=80_000,
                  contributed=50_000, grants=5_000, contribute=False)
    without = flat(replace(p, education=None))
    proj = flat(p)
    # Age 22 with start 18 and 4 years: school is over, so the leftover moves now.
    # 50,000 contributions back + 25,000 growth to the RRSP; 5,000 grants repaid.
    gained = proj.investments[1, 0] - without.investments[1, 0]
    assert gained == pytest.approx(75_000, abs=1.0)
    assert proj.tax[0, 0] == pytest.approx(without.tax[0, 0], abs=1.0)


def test_growth_beyond_the_rrsp_limit_is_taxed_with_the_extra_20_percent():
    p = plan_with([{"name": "K", "age": 22, "years": 1}], resp_balance=120_000,
                  contributed=50_000, grants=0, contribute=False)
    proj, without = flat(p), flat(replace(p, education=None))
    extra_tax = proj.tax[0, 0] - without.tax[0, 0]
    assert extra_tax > rules.RESP["aip"].value["extra_tax"] * 20_000      # income tax on top
    assert proj.resp[1, 0] == 0.0


def test_holdings_supply_the_resp_balance():
    frame = pd.DataFrame([["Family RESP", "cash", np.nan, np.nan, 42_000.0, 42_000.0],
                          ["Partner A RRSP", "cash", np.nan, np.nan, 1_000.0, 1_000.0]],
                         columns=["account", "kind", "shares", "cost", "value", "value_cad"])
    p = plan_with([{"name": "K", "age": 10}])
    assert inputs.with_holdings(p, frame).education.resp_balance == 42_000.0


def test_education_validation_names_the_field():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["education"] = {"kids": [{"name": "K", "age": 10, "living": "dorm"}]}
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "education.kids[0].living"


def test_no_education_section_changes_nothing():
    p = inputs.parse(inputs.TEMPLATE)
    assert p.education is None and flat(p).education is None


# -- Canada Student Grant -----------------------------------------------------------

SG = rules.STUDENT_GRANT.value


def test_student_grant_phases_out_between_the_thresholds():
    full, cutoff = SG["thresholds"][4]
    assert education.student_grant(full - 1, 4) == SG["yearly_max"]
    assert education.student_grant(cutoff, 4) == 0.0
    assert education.student_grant((full + cutoff) / 2, 4) == pytest.approx(SG["yearly_max"] / 2)
    assert education.student_grant(0, 12) == SG["yearly_max"]          # 7 or more
    assert education.student_grant(np.inf, 4) == 0.0


def _retired_family(kind, **edu):
    """Retired couple, school starting next year, all their money in one ``kind`` of account."""
    p = plan_with([{"name": "K", "age": 17, "years": 2}], resp_balance=0, contribute=False, **edu)
    people = tuple(replace(x, age=55, retire_age=55) for x in p.people)
    return replace(p, people=people, accounts=(Account("A", kind, 2_000_000.0),), home=None)


def test_a_low_income_family_gets_the_full_grant_after_the_first_year():
    proj = flat(_retired_family("tfsa"))
    # Year 0: last year's income is unknown for a retired household, so no grant.
    assert proj.student_grant[0, 0] == 0.0
    assert proj.student_grant[1, 0] == SG["yearly_max"] and proj.student_grant[2, 0] == SG["yearly_max"]
    off = flat(_retired_family("tfsa", student_grant=False))
    assert off.education[1, 0] - proj.education[1, 0] == pytest.approx(SG["yearly_max"])


def test_rrsp_withdrawals_count_as_family_income():
    proj = flat(_retired_family("rrsp"))
    assert proj.student_grant[2, 0] < SG["yearly_max"]


def test_workers_without_a_salary_get_no_grant():
    p = plan_with([{"name": "K", "age": 18, "years": 2}], resp_balance=0, contribute=False)
    people = tuple(replace(x, salary=None) for x in p.people)
    assert flat(replace(p, people=people)).student_grant.sum() == 0.0


def test_a_salary_above_the_cutoff_gets_no_grant_while_working():
    p = plan_with([{"name": "K", "age": 18, "years": 2}], resp_balance=0, contribute=False)
    # The template's two salaries (105k + 90k) are above the family-of-4 cut-off.
    assert flat(p).student_grant.sum() == 0.0


def test_a_retirees_leftover_resp_growth_is_taxed_with_their_other_income():
    def leftover_tax(account):
        p = plan_with([{"name": "K", "age": 22, "years": 1}], resp_balance=150_000,
                      contributed=50_000, grants=0, contribute=False, aip_to_rrsp=False)
        people = tuple(replace(x, age=60, retire_age=60) for x in p.people)
        p = replace(p, people=people, home=None, accounts=(Account("A", account, 3_000_000.0),))
        q = replace(p, education=None)
        return flat(p).tax[0, 0] - flat(q).tax[0, 0]
    # The same 100,000 of growth costs more tax on top of large RRSP withdrawals
    # than for a household living off its TFSA.
    assert leftover_tax("rrsp") > leftover_tax("tfsa")


def test_education_costs_grow_with_their_rate():
    p = plan_with([{"name": "K", "age": 15, "living": "home"}], costs={"home": 10_000, "away": 30_000})
    s = education.schedule(replace(p, cost_growth=CostGrowth(education=0.02)), engine.steps(p))
    assert s.cost[3] == pytest.approx(10_000 * 1.02 ** 3)


def test_the_student_grant_shrinks_with_prices():
    p = _retired_family("tfsa")
    T = engine.steps(p)
    steady = engine.simulate(p, np.zeros((T, 1)), None, np.zeros((T, 1)))
    hot = engine.simulate(p, np.zeros((T, 1)), None, np.full((T, 1), 0.10))
    assert hot.student_grant[1, 0] == pytest.approx(steady.student_grant[1, 0] / 1.10)
