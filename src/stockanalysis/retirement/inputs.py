"""Plan inputs for the retirement planner: plan.json, validated in plain Python.

``plan.json`` describes the household: people, savings, spending, home and
assumptions. Account balances come from its ``balances`` list when present;
otherwise they come live from the holdings workbook, sorted into
(owner, account type) by :func:`balances_from_holdings`.

Nothing here is personal: the owner's plan lives in the gitignored
``retirement/`` folder, and :data:`TEMPLATE` uses invented values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import rules

ACCOUNT_TYPES = ("rrsp", "pension", "tfsa", "nonreg")
STRATEGIES = ("rrsp_first", "proportional", "steady_income")
STRATEGY_LABELS = {
    "rrsp_first": "Withdraw from RRSP/RRIF first",
    "proportional": "Withdraw proportionally from all account types",
    "steady_income": "Keep taxable income steady (fill the lowest bracket)",
}
# Account-name keywords, checked in order. Word boundaries keep "RESP" apart
# from "RRSP", and "Lifeco" apart from "LIF".
KEYWORDS = (
    ("exclude", re.compile(r"\bRESP\b", re.I)),
    ("rrsp", re.compile(r"\bRRSP\b|\bRRIF\b", re.I)),
    ("tfsa", re.compile(r"\bTFSA\b", re.I)),
    ("pension", re.compile(r"\bLIRA\b|\bLIF\b|\bDCPP\b|\bPENSION\b", re.I)),
)


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    age: int
    retire_age: int
    cpp_start_age: int = 70
    oas_start_age: int = 70
    cpp_at_65: float | None = None
    cpp_years: float = 0.0
    cpp_earnings_ratio: float = 1.0
    years_in_canada_at_65: float = 40.0
    rrif_start_age: int = 65
    lif_start_age: int | None = None
    unlock_share: float = 0.5
    tfsa_room: float = 0.0
    contributions: dict = field(default_factory=dict)
    contributions_when_partner_retired: dict | None = None


@dataclass(frozen=True)
class SpendingChange:
    year: int
    amount: float
    label: str = ""


@dataclass(frozen=True)
class Spending:
    base: float
    changes: tuple = ()
    slow_go_age: int = 75
    slow_go_share: float = 0.85
    no_go_age: int = 85
    no_go_share: float = 0.70
    care: float = 0.0
    bad_market_cut: float = 0.10
    bad_market_trigger: float = 0.80


@dataclass(frozen=True)
class Home:
    value: float
    downsize_age: int | None = None
    new_value: float = 0.0
    selling_cost: float = 0.04
    moving_cost: float = 0.0
    property_tax: float = 0.0
    insurance: float = 0.0


@dataclass(frozen=True)
class Returns:
    mean: float = 0.05
    sd: float = 0.15
    paths: int = 10_000
    seed: int = 7


@dataclass(frozen=True)
class Withdrawal:
    strategy: str = "rrsp_first"
    steady_income_target: float = 58_000.0


@dataclass(frozen=True)
class Account:
    owner: str
    type: str
    balance: float
    cost: float | None = None


@dataclass(frozen=True)
class PlanInputs:
    province: str
    start_year: int
    end_age: int
    people: tuple
    spending: Spending
    home: Home | None
    returns: Returns
    withdrawal: Withdrawal
    accounts: tuple = ()
    holdings: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)


# Invented example household for `stock-analysis retire --init`. Delete
# "balances" to read balances from the holdings workbook instead.
TEMPLATE = {
    "province": "AB",
    "start_year": 2026,
    "end_age": 95,
    "people": [
        {"id": "A", "name": "Partner A", "age": 50, "retire_age": 60,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 25, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 40,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "contributions": {"pension": 8000, "tfsa": 7000, "rrsp": 10000, "nonreg": 0},
         "contributions_when_partner_retired": None},
        {"id": "B", "name": "Partner B", "age": 48, "retire_age": 58,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 20, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 38,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "contributions": {"pension": 6000, "tfsa": 7000, "rrsp": 8000, "nonreg": 0},
         "contributions_when_partner_retired": None},
    ],
    "spending": {"base": 80000,
                 "changes": [{"year": 2035, "amount": -10000, "label": "Kids leave home"}],
                 "slow_go_age": 75, "slow_go_share": 0.85, "no_go_age": 85, "no_go_share": 0.70,
                 "care": 25000, "bad_market_cut": 0.10, "bad_market_trigger": 0.80},
    "home": {"value": 900000, "downsize_age": 65, "new_value": 600000, "selling_cost": 0.04,
             "moving_cost": 20000, "property_tax": 6000, "insurance": 2000},
    "returns": {"mean": 0.05, "sd": 0.15, "paths": 10000, "seed": 7},
    "withdrawal": {"strategy": "rrsp_first", "steady_income_target": 58000},
    "balances": [
        {"owner": "A", "type": "rrsp", "balance": 400000},
        {"owner": "A", "type": "tfsa", "balance": 100000},
        {"owner": "A", "type": "nonreg", "balance": 50000, "cost": 40000},
        {"owner": "B", "type": "rrsp", "balance": 300000},
        {"owner": "B", "type": "tfsa", "balance": 90000},
        {"owner": "B", "type": "pension", "balance": 80000},
    ],
    "holdings": {"owners": {"A": ["Partner A"], "B": ["Partner B"]}, "accounts": {}, "ignore": []},
    "scenarios": {"downsize_ages": [None, 65, 70], "cheaper_home_share": 0.8},
}


def _build(d: dict) -> PlanInputs:
    try:
        spending = dict(d["spending"])
        spending["changes"] = tuple(SpendingChange(**c) for c in spending.get("changes", []))
        return PlanInputs(
            province=d["province"], start_year=int(d["start_year"]), end_age=int(d["end_age"]),
            people=tuple(Person(**p) for p in d["people"]),
            spending=Spending(**spending),
            home=None if d.get("home") is None else Home(**d["home"]),
            returns=Returns(**d.get("returns", {})),
            withdrawal=Withdrawal(**d.get("withdrawal", {})),
            accounts=tuple(Account(**a) for a in d.get("balances") or []),
            holdings=dict(d.get("holdings") or {}),
            scenarios=dict(d.get("scenarios") or {}),
        )
    except KeyError as e:
        raise ValueError(f"plan.json is missing the field {e}") from e
    except TypeError as e:
        raise ValueError(f"plan.json has an unexpected or missing field: {e}") from e


def _fail(field_name: str, message: str):
    raise ValueError(f"{field_name}: {message}")


def validate(plan: PlanInputs) -> PlanInputs:
    """Return ``plan`` unchanged, or raise ValueError naming the offending field."""
    if plan.province not in rules.PROVINCIAL or plan.province not in rules.LIF:
        _fail("province", f"{plan.province!r} is not in rules.py (have {sorted(rules.PROVINCIAL)})")
    if not 1 <= len(plan.people) <= 2:
        _fail("people", "need one or two people")
    ids = [p.id for p in plan.people]
    if len(set(ids)) != len(ids):
        _fail("people", "ids must be unique")
    lif_min = rules.LIF[plan.province]["min_age"].value
    max_unlock = rules.LIF[plan.province]["unlock_share"].value
    for i, p in enumerate(plan.people):
        f = f"people[{i}]"
        if not 18 <= p.age < plan.end_age:
            _fail(f"{f}.age", f"{p.age} must be 18 or more and below end_age {plan.end_age}")
        if p.retire_age < p.age:
            _fail(f"{f}.retire_age", f"{p.retire_age} is below age {p.age}")
        if p.retire_age >= plan.end_age:
            _fail(f"{f}.retire_age", f"{p.retire_age} must be below end_age {plan.end_age}")
        if not 60 <= p.cpp_start_age <= 70:
            _fail(f"{f}.cpp_start_age", "CPP starts between 60 and 70")
        if not 65 <= p.oas_start_age <= 70:
            _fail(f"{f}.oas_start_age", "OAS starts between 65 and 70")
        if p.rrif_start_age > rules.RRIF["convert_by_age"].value:
            _fail(f"{f}.rrif_start_age", "an RRSP must become a RRIF by 71")
        if p.lif_start_age is not None and p.lif_start_age < lif_min:
            _fail(f"{f}.lif_start_age", f"a LIF can start at {lif_min} at the earliest")
        if not 0 <= p.unlock_share <= max_unlock:
            _fail(f"{f}.unlock_share", f"between 0 and {max_unlock}")
        if not 0 <= p.cpp_earnings_ratio <= 1:
            _fail(f"{f}.cpp_earnings_ratio", "between 0 and 1")
        for name in ("cpp_years", "years_in_canada_at_65", "tfsa_room"):
            if getattr(p, name) < 0:
                _fail(f"{f}.{name}", "must not be negative")
        if p.cpp_at_65 is not None and p.cpp_at_65 < 0:
            _fail(f"{f}.cpp_at_65", "must not be negative")
        for label, amounts in (("contributions", p.contributions),
                               ("contributions_when_partner_retired",
                                p.contributions_when_partner_retired or {})):
            for kind, amount in amounts.items():
                if kind not in ACCOUNT_TYPES:
                    _fail(f"{f}.{label}.{kind}", f"unknown account type (use {ACCOUNT_TYPES})")
                if amount < 0:
                    _fail(f"{f}.{label}.{kind}", "must not be negative")
    s = plan.spending
    if s.base < 0:
        _fail("spending.base", "must not be negative")
    for name in ("slow_go_share", "no_go_share", "bad_market_cut", "bad_market_trigger"):
        if not 0 <= getattr(s, name) <= 1:
            _fail(f"spending.{name}", "must be between 0 and 1")
    if s.slow_go_age > s.no_go_age:
        _fail("spending.slow_go_age", "must not be after no_go_age")
    if s.care < 0:
        _fail("spending.care", "must not be negative")
    h = plan.home
    if h is not None:
        if h.value <= 0:
            _fail("home.value", "must be positive")
        if not 0 <= h.selling_cost < 1:
            _fail("home.selling_cost", "between 0 and 1")
        for name in ("moving_cost", "property_tax", "insurance", "new_value"):
            if getattr(h, name) < 0:
                _fail(f"home.{name}", "must not be negative")
        if h.downsize_age is not None and h.value * (1 - h.selling_cost) - h.new_value - h.moving_cost < 0:
            _fail("home.new_value", "the sale must cover the new home and the move")
    r = plan.returns
    if r.mean <= -1 or r.sd < 0 or r.paths < 1:
        _fail("returns", "need mean > -1, sd >= 0 and paths >= 1")
    if plan.withdrawal.strategy not in STRATEGIES:
        _fail("withdrawal.strategy", f"{plan.withdrawal.strategy!r} is not one of {STRATEGIES}")
    if plan.withdrawal.steady_income_target < 0:
        _fail("withdrawal.steady_income_target", "must not be negative")
    for j, a in enumerate(plan.accounts):
        if a.owner not in ids:
            _fail(f"balances[{j}].owner", f"{a.owner!r} is not one of the people {ids}")
        if a.type not in ACCOUNT_TYPES:
            _fail(f"balances[{j}].type", f"{a.type!r} is not one of {ACCOUNT_TYPES}")
        if a.balance < 0:
            _fail(f"balances[{j}].balance", "must not be negative")
    return plan


def load_inputs(path) -> PlanInputs:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No plan at {path}. Create one with: stock-analysis retire --init --inputs {path}")
    return validate(_build(json.loads(path.read_text(encoding="utf-8"))))


def write_template(path) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; not overwriting it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
    return path


def _cost_cad(row) -> float:
    """Cost base in CAD: value_cad scaled by (shares x unit cost / value)."""
    try:
        ratio = float(row["shares"]) * float(row["cost"]) / float(row["value"])
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        return float(row["value_cad"])
    return float(row["value_cad"]) * ratio if np.isfinite(ratio) and ratio > 0 else float(row["value_cad"])


def _classify(name: str, mapping: dict) -> str | None:
    explicit = (mapping.get("accounts") or {}).get(name, {})
    if "type" in explicit:
        return explicit["type"]
    if name in (mapping.get("ignore") or []):
        return "exclude"
    for kind, pattern in KEYWORDS:
        if pattern.search(name):
            return kind
    return None


def _owner(name: str, mapping: dict, people) -> str | None:
    explicit = (mapping.get("accounts") or {}).get(name, {})
    if "owner" in explicit:
        return explicit["owner"]
    hits = [pid for pid, words in (mapping.get("owners") or {}).items()
            if any(w.lower() in name.lower() for w in words)]
    if len(hits) == 1:
        return hits[0]
    if len(people) == 1:
        return people[0].id
    return None


def balances_from_holdings(frame: pd.DataFrame, mapping: dict, people) -> tuple:
    """Sort holdings rows (``holdings.load()["holdings"]``) into Accounts.

    Type comes from an explicit ``mapping["accounts"][name]["type"]`` first, then
    keywords (RESP excluded; RRSP/RRIF; TFSA; LIRA/LIF/DCPP/pension). Anything
    else must be listed, so money is never silently dropped.
    """
    unknown, unowned, totals = [], [], {}
    for name, rows in frame.groupby("account", sort=True):
        kind = _classify(str(name), mapping)
        if kind is None:
            unknown.append(str(name))
            continue
        if kind == "exclude":
            continue
        who = _owner(str(name), mapping, people)
        if who is None:
            unowned.append(str(name))
            continue
        balance = float(rows["value_cad"].sum())
        cost = float(sum(_cost_cad(r) for _, r in rows.iterrows()))
        b, c = totals.get((who, kind), (0.0, 0.0))
        totals[(who, kind)] = (b + balance, c + cost)
    if unknown:
        raise ValueError("Can't tell the account type of: " + ", ".join(unknown)
                         + ". Add each to plan.json holdings.accounts (type rrsp / pension / "
                           "tfsa / nonreg) or to holdings.ignore.")
    if unowned:
        raise ValueError("Can't tell whose account this is: " + ", ".join(unowned)
                         + ". Add an owner keyword to plan.json holdings.owners or an "
                           "explicit owner in holdings.accounts.")
    return tuple(Account(owner=o, type=k, balance=b, cost=c if k == "nonreg" else None)
                 for (o, k), (b, c) in sorted(totals.items()))


def with_holdings(plan: PlanInputs, frame: pd.DataFrame) -> PlanInputs:
    """``plan`` with its accounts replaced by the sorted holdings."""
    return validate(replace(plan, accounts=balances_from_holdings(frame, plan.holdings, plan.people)))
