"""The plan as a to-do list (retirement.actions). Invented households only."""
from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np
import pytest

from stockanalysis.retirement import actions, engine, inputs, report, scenarios


@pytest.fixture(scope="module")
def result():
    return engine.run(inputs.parse(inputs.TEMPLATE), paths=40)


def whats(result):
    return [a.what for a in actions.plan_actions(result)]


def test_the_plan_lists_its_milestones_in_order(result):
    acts = actions.plan_actions(result)
    assert [a.year for a in acts] == sorted(a.year for a in acts)
    text = " | ".join(whats(result))
    for needle in ("Retire", "Convert the RRSP to a RRIF", "Start CPP", "Start OAS",
                   "pension income splitting", "Sell the home"):
        assert needle in text


def test_tfsa_top_up_is_listed_only_when_on(result):
    assert any("into TFSAs" in w for w in whats(result))
    p = result.inputs
    off = engine.run(replace(p, withdrawal=replace(p.withdrawal, tfsa_top_up=False)), paths=40)
    assert not any("into TFSAs" in w for w in whats(off))


def test_runs_split_into_phases():
    years = list(range(2030, 2040))
    values = np.array([0, 40_000, 41_000, 39_000, 90_000, 92_000, 91_000, 4_000, 4_500, 0], dtype=float)
    assert actions._runs(years, values) == [(2031, 2033, 40_000.0), (2034, 2036, 91_000.0),
                                            (2037, 2038, 4_250.0)]


def test_resp_actions_follow_the_schedule():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["education"] = {"kids": [{"name": "Older", "age": 15}, {"name": "Younger", "age": 11}],
                      "resp_balance": 50_000}
    acts = actions.plan_actions(engine.run(inputs.parse(d), paths=40))
    resp = [a for a in acts if a.who == "RESP"]
    assert any("No more RESP contributions needed for Older" in a.what for a in resp)
    assert any(a.what.startswith("Contribute C$2,500") for a in resp)
    assert any("Close the RESP" in a.what for a in resp)


def test_missing_inputs_name_what_to_find():
    missing = actions.missing_inputs(inputs.parse(inputs.TEMPLATE))
    assert any("Notice of Assessment" in m for m in missing)
    assert any("My Service Canada" in m for m in missing)


def test_report_has_the_action_plan(result):
    baseline, ranked = scenarios.rank(result.inputs, paths=20, seed=1)
    html = report.build_report(result, baseline, ranked, generated_at="now")
    assert f'id="{report.ACTIONS_ID}"' in html and "What to do" in html


def test_lif_line_only_for_someone_with_locked_in_money():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["balances"] = [b for b in d["balances"] if not (b["type"] == "pension" and b["owner"] == "B")]
    for person in d["people"]:
        person["contributions"].pop("pension", None)
    d["balances"].append({"owner": "A", "type": "pension", "balance": 50_000})
    acts = actions.plan_actions(engine.run(inputs.parse(d), paths=40))
    lif = [a.who for a in acts if "into a LIF" in a.what]
    assert lif == ["Partner A"]


def test_spousal_rrsp_contributions_and_the_attribution_warning():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0]["contributions"]["spousal_rrsp"] = 6_000
    acts = actions.plan_actions(engine.run(inputs.parse(d), paths=40))
    lines = [(a.who, a.what) for a in acts if "spousal RRSP" in a.what]
    assert any(who == "Partner A" and what.startswith("Contribute about C$6,000 a year") for who, what in lines)
    assert any(who == "Partner B" and what.startswith("Leave the spousal RRSP alone until") for who, what in lines)
