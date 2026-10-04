"""plan.json loading/validation and sorting holdings accounts (invented numbers)."""
from __future__ import annotations

import copy
import json
import re

import numpy as np
import pandas as pd
import pytest

from stockanalysis.retirement import inputs


@pytest.fixture
def plan(tmp_path):
    return inputs.load_inputs(inputs.write_template(tmp_path / "plan.json"))


def test_template_round_trip(plan):
    assert [p.id for p in plan.people] == ["A", "B"]
    assert plan.withdrawal.strategy == "rrsp_first"
    assert {a.type for a in plan.accounts} == {"rrsp", "tfsa", "pension", "nonreg"}
    assert plan.spending.changes[0].amount == -10_000
    assert plan.home.downsize_age == 65


def test_write_template_refuses_to_overwrite(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("{}")
    with pytest.raises(FileExistsError, match="already exists"):
        inputs.write_template(path)


def test_missing_plan_names_the_init_hint(tmp_path):
    with pytest.raises(FileNotFoundError, match="--init"):
        inputs.load_inputs(tmp_path / "nope.json")


@pytest.mark.parametrize("edit, field", [
    (lambda d: d["people"][0].update(retire_age=40), "people[0].retire_age"),
    (lambda d: d["people"][0].update(cpp_start_age=58), "people[0].cpp_start_age"),
    (lambda d: d["people"][1].update(lif_start_age=45), "people[1].lif_start_age"),
    (lambda d: d["withdrawal"].update(strategy="yolo"), "withdrawal.strategy"),
    (lambda d: d.update(province="ZZ"), "province"),
    (lambda d: d["spending"].update(slow_go_share=1.5), "spending.slow_go_share"),
    (lambda d: d["home"].update(new_value=2_000_000), "home.new_value"),
    (lambda d: d["balances"][0].update(owner="Z"), "balances[0].owner"),
])
def test_validation_names_the_field(tmp_path, edit, field):
    d = copy.deepcopy(inputs.TEMPLATE)
    edit(d)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match=re.escape(field)):
        inputs.load_inputs(path)


def _frame(rows):
    return pd.DataFrame(rows, columns=["account", "kind", "shares", "cost", "value", "value_cad"])


MAPPING = {"owners": {"A": ["Partner A"], "B": ["Partner B"]},
           "accounts": {"Partner B Company": {"owner": "B", "type": "pension"},
                        "Partner A Lifeco shares": {"type": "nonreg"}},
           "ignore": []}


def test_holdings_sorted_by_keywords_and_mapping(plan):
    frame = _frame([
        ["Partner A RRSP", "stock", 10, 50.0, 1_000.0, 1_000.0],
        ["Partner B TFSA CAD", "cash", np.nan, np.nan, 500.0, 500.0],
        ["Family RESP", "stock", 5, 10.0, 100.0, 100.0],
        ["Partner B Company", "other", np.nan, np.nan, 2_000.0, 2_000.0],
        ["Partner A Lifeco shares", "stock", 10, 5.0, 100.0, 100.0],
    ])
    accts = {(a.owner, a.type): a for a in inputs.balances_from_holdings(frame, MAPPING, plan.people)}
    assert accts[("A", "rrsp")].balance == 1_000
    assert accts[("B", "tfsa")].balance == 500
    assert accts[("B", "pension")].balance == 2_000
    assert accts[("A", "nonreg")].balance == 100 and accts[("A", "nonreg")].cost == 50
    assert sum(a.balance for a in accts.values()) == 3_600      # the RESP is excluded


def test_keyword_collisions_are_not_misread(plan):
    # "Lifeco" must not be read as a LIF, and nothing unmapped is silently dropped.
    frame = _frame([["Partner A Lifeco", "stock", 1, 1.0, 10.0, 10.0],
                    ["Partner A RESP", "cash", np.nan, np.nan, 5.0, 5.0]])
    with pytest.raises(ValueError, match="Partner A Lifeco"):
        inputs.balances_from_holdings(frame, {"owners": MAPPING["owners"]}, plan.people)


def test_account_with_no_owner_raises(plan):
    frame = _frame([["Joint RRSP", "cash", np.nan, np.nan, 5.0, 5.0]])
    with pytest.raises(ValueError, match="whose"):
        inputs.balances_from_holdings(frame, {"owners": MAPPING["owners"]}, plan.people)


def test_with_holdings_replaces_the_balances(plan):
    frame = _frame([["Partner A RRSP", "cash", np.nan, np.nan, 7.0, 7.0]])
    out = inputs.with_holdings(plan, frame)
    assert out.accounts == (inputs.Account(owner="A", type="rrsp", balance=7.0, cost=None),)


def test_a_non_finite_holding_stops_the_run(plan):
    # A #N/A cell in the sheet arrives as NaN: it must not be silently dropped.
    frame = _frame([["Partner A RRSP", "stock", 10, 5.0, np.nan, np.nan],
                    ["Partner A RRSP", "cash", np.nan, np.nan, 100.0, 100.0]])
    with pytest.raises(ValueError, match="Partner A RRSP"):
        inputs.balances_from_holdings(frame, MAPPING, plan.people)


def test_validation_rejects_a_nan_cost(tmp_path):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["balances"][2]["cost"] = float("nan")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match=re.escape("balances[2].cost")):
        inputs.load_inputs(path)


# -- deferred minors ------------------------------------------------------------------

def _write(tmp_path, edit):
    d = copy.deepcopy(inputs.TEMPLATE)
    edit(d)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    return path


def test_a_downsize_age_already_passed_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=re.escape("home.downsize_age")):
        inputs.load_inputs(_write(tmp_path, lambda d: d["home"].update(downsize_age=40)))


def test_a_lif_must_start_by_71(tmp_path):
    with pytest.raises(ValueError, match=re.escape("people[0].lif_start_age")):
        inputs.load_inputs(_write(tmp_path, lambda d: d["people"][0].update(lif_start_age=72)))


def test_locked_in_rrsp_names_are_pension_money(plan):
    frame = _frame([["Partner A Locked-in RRSP", "cash", np.nan, np.nan, 10.0, 10.0],
                    ["Partner B LRSP", "cash", np.nan, np.nan, 5.0, 5.0]])
    accts = inputs.balances_from_holdings(frame, MAPPING, plan.people)
    assert {(a.owner, a.type) for a in accts} == {("A", "pension"), ("B", "pension")}


def test_owner_keywords_match_whole_words(plan):
    frame = _frame([["Personal RRSP", "cash", np.nan, np.nan, 5.0, 5.0]])
    with pytest.raises(ValueError, match="whose"):
        inputs.balances_from_holdings(frame, {"owners": {"A": ["Al"], "B": ["Bo"]}}, plan.people)


def test_template_explains_balances_versus_holdings(plan):
    assert "holdings" in inputs.TEMPLATE["_readme"] and "balances" in inputs.TEMPLATE["_readme"]


def test_nonreg_income_defaults_to_none_and_is_bounded():
    d = copy.deepcopy(inputs.TEMPLATE)
    d.pop("nonreg_income")
    assert inputs.parse(d).nonreg_income.total == 0.0
    d["nonreg_income"] = {"eligible_dividends": 0.5}
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "nonreg_income.eligible_dividends"


def test_salary_must_not_be_negative():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0]["salary"] = -1
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[0].salary"


def test_sex_and_survivor_share_default_and_round_trip():
    d = copy.deepcopy(inputs.TEMPLATE)
    plan = inputs.parse(d)
    assert [p.sex for p in plan.people] == ["female", "male"]
    assert plan.spending.survivor_share == 0.70
    for p in d["people"]:
        p.pop("sex")
    d["spending"].pop("survivor_share")
    plan = inputs.parse(d)
    assert [p.sex for p in plan.people] == [None, None] and plan.spending.survivor_share == 0.70


def test_a_misspelled_sex_names_the_field():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][1]["sex"] = "M"
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[1].sex"


@pytest.mark.parametrize("share", [0.39, 1.01])
def test_survivor_share_is_bounded(share):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["spending"]["survivor_share"] = share
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "spending.survivor_share"
    assert inputs.limits("AB")["survivor_share"] == (0.4, 1.0)


def test_pension_match_defaults_to_zero_and_must_not_be_negative():
    assert inputs.parse(copy.deepcopy(inputs.TEMPLATE)).people[0].pension_match == 0.0
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0]["pension_match"] = -0.5
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[0].pension_match"


def test_espp_parses_and_validates():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0]["espp"] = {"rate": 0.25, "cap": 25_000, "discount": 0.15}
    e = inputs.parse(d).people[0].espp
    assert (e.rate, e.cap, e.discount) == (0.25, 25_000, 0.15)
    assert inputs.parse(copy.deepcopy(inputs.TEMPLATE)).people[0].espp is None
    for bad in ({"rate": 1.5, "cap": 1, "discount": 0.1}, {"rate": 0.1, "cap": -1, "discount": 0.1},
                {"rate": 0.1, "cap": 1, "discount": 1.0}):
        d["people"][0]["espp"] = bad
        with pytest.raises(inputs.PlanError) as err:
            inputs.parse(d)
        assert err.value.field.startswith("people[0].espp")


def test_rrsp_room_and_childcare_are_optional_and_not_negative():
    plan = inputs.parse(copy.deepcopy(inputs.TEMPLATE))
    assert plan.people[0].rrsp_room is None
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0]["rrsp_room"] = -1
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[0].rrsp_room"
    d = copy.deepcopy(inputs.TEMPLATE)
    d["education"] = {"kids": [{"name": "K", "age": 10}], "childcare": -5}
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "education.childcare"


def test_ages_past_the_life_table_are_rejected():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["end_age"] = 115
    d["home"] = None
    d["people"][0]["age"] = 111
    d["people"][0]["retire_age"] = 111
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[0].age"


# -- one-time money events -------------------------------------------------------

def test_events_parse_and_default_to_none():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["events"] = [{"label": "Car", "amount": -40_000, "year": 2030, "every": 10},
                   {"label": "Part-time", "kind": "income", "amount": 30_000, "person": "B",
                    "age": 60, "until_age": 64}]
    plan = inputs.parse(d)
    car, work = plan.events
    assert (car.label, car.amount, car.year, car.every, car.kind) == ("Car", -40_000, 2030, 10, "cash")
    assert (work.kind, work.person, work.age, work.until_age) == ("income", "B", 60, 64)
    no_events = copy.deepcopy(inputs.TEMPLATE)
    no_events.pop("events", None)
    assert inputs.parse(no_events).events == ()


@pytest.mark.parametrize("bad, field", [
    ({"label": "x", "amount": 1}, "events[0].year"),                                  # no start
    ({"label": "x", "amount": 1, "year": 2030, "age": 60}, "events[0].year"),          # both starts
    ({"label": "x", "amount": 1, "year": 2030, "every": 0}, "events[0].every"),
    ({"label": "x", "amount": 1, "year": 2030, "until": 2029}, "events[0].until"),
    ({"label": "x", "amount": 1, "year": 2030, "kind": "gift"}, "events[0].kind"),
    ({"label": "x", "amount": -1, "year": 2030, "kind": "income"}, "events[0].amount"),
    ({"label": "x", "amount": 1, "year": 2030, "kind": "income", "person": "Z"}, "events[0].person"),
])
def test_bad_events_name_the_field(bad, field):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["events"] = [bad]
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == field
