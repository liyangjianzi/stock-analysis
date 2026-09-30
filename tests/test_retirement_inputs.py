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
