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
from dataclasses import MISSING, dataclass, field, fields, is_dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import mortality, rules

ACCOUNT_TYPES = ("rrsp", "pension", "tfsa", "nonreg")
LIVING = ("home", "away")
# Yearly cost per student in today's dollars: an editable estimate, not a rule.
# Home ~ Alberta undergraduate tuition, fees and books; away adds residence and food.
EDUCATION_COSTS = {"home": 11_000.0, "away": 25_000.0}
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
    ("pension", re.compile(r"\bLOCKED[- ]?IN\b|\bLRSP\b|\bLRIF\b|\bRLIF\b", re.I)),
    ("rrsp", re.compile(r"\bRRSP\b|\bRRIF\b", re.I)),
    ("tfsa", re.compile(r"\bTFSA\b", re.I)),
    ("pension", re.compile(r"\bLIRA\b|\bLIF\b|\bDCPP\b|\bPENSION\b", re.I)),
)


@dataclass(frozen=True)
class Espp:
    """An employee share purchase plan (the employer's terms, not a tax rule)."""
    rate: float          # share of salary put in
    cap: float           # most put in a year (C$)
    discount: float      # off the market price; the discount is taxed as salary


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
    tfsa_room: float = 0.0      # unused room carried in from past years, before this year's limit
    salary: float | None = None  # gross employment income while working; taxes non-reg payouts then
    contributions: dict = field(default_factory=dict)
    contributions_when_partner_retired: dict | None = None
    sex: str | None = None      # "female" / "male" for the life table; None averages the two
    pension_match: float = 0.0  # employer match as a multiple of your own pension contribution
    espp: Espp | None = None    # shares bought from salary into the non-registered account
    rrsp_room: float | None = None  # "RRSP deduction limit" from the Notice of Assessment; None: not checked


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
    survivor_share: float = 0.70    # a lone survivor's share of the couple's budget
    rule: str = "bad_market"        # "bad_market" (one cut) or "guardrails" (Guyton-Klinger)
    guardrail_band: float = 0.20    # act when the withdrawal rate moves this far from where it began
    guardrail_step: float = 0.10    # each cut or raise
    guardrail_stop_years: int = 15  # no cuts in the plan's last N years
    guardrail_floor: float = 0.75   # never cut below this share of the planned spending
    guardrail_ceiling: float = 1.50  # never raise above this share


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
class NonregIncome:
    """Yearly payouts of the non-registered accounts, as shares of their balance.

    They are part of ``returns.mean`` (not extra growth), are reinvested (so they
    raise the cost base) and are taxed every year: eligible Canadian dividends with
    the gross-up and dividend credits, foreign dividends and interest as ordinary
    income.
    """
    eligible_dividends: float = 0.0
    foreign_dividends: float = 0.0
    interest: float = 0.0

    @property
    def total(self) -> float:
        return self.eligible_dividends + self.foreign_dividends + self.interest


@dataclass(frozen=True)
class Kid:
    name: str
    age: int
    start_age: int = 18
    years: int = 4
    living: str = "away"                    # "home" or "away" while studying
    cesg_received: float | None = None       # grant so far; None: every year's grant collected


@dataclass(frozen=True)
class Education:
    """Children's school, paid from a family RESP; what it can't cover is household spending."""
    kids: tuple = ()
    costs: dict = field(default_factory=lambda: dict(EDUCATION_COSTS))
    resp_balance: float | None = None   # None: the holdings' RESP accounts (or 0 without holdings)
    contributed: float | None = None    # contributions so far; None: grants / grant rate
    grants: float | None = None         # CESG so far; None: the kids' cesg_received (estimated)
    contribute: bool = True             # contribute each January while it still earns the grant
    aip_to_rrsp: bool = True            # leftover growth to the RRSP (up to the limit) first
    student_grant: bool = True          # apply for the Canada Student Grant (income-tested)
    childcare: float = 0.0              # yearly child care paid (already in spending); deducted on line 21400


EVENT_KINDS = ("cash", "income")
SPENDING_RULES = ("bad_market", "guardrails")


@dataclass(frozen=True)
class Event:
    """A one-time or repeating amount (``kind="cash"``: + money in, untaxed; − money
    out, spent) or temporary income (``kind="income"``: a yearly amount taxed like
    salary for ``person``). It starts in calendar ``year`` or at an ``age`` (the
    person's, else people[0]'s), repeats ``every`` N years, and ends at ``until`` /
    ``until_age`` (inclusive); income runs every year in between."""
    label: str
    amount: float
    year: int | None = None
    age: int | None = None
    every: int | None = None
    until: int | None = None
    until_age: int | None = None
    kind: str = "cash"
    person: str | None = None


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
    nonreg_income: NonregIncome = NonregIncome()
    education: Education | None = None
    events: tuple = ()
    saved_scenarios: tuple = ()     # (name, {dotted.path: value}) versions to compare side by side
    holdings: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)


# Invented example household for `stock-analysis retire --init`. Delete
# "balances" to read balances from the holdings workbook instead.
TEMPLATE = {
    "_readme": ("Example household with invented numbers. Amounts are in today's CAD. "
                "Delete 'balances' to read balances from the holdings workbook instead. "
                "tfsa_room is unused room carried in from past years, before this year's limit."),
    "province": "AB",
    "start_year": 2026,
    "end_age": 95,
    "people": [
        {"id": "A", "name": "Partner A", "sex": "female", "age": 50, "retire_age": 60,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 25, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 40,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "salary": 105000,
         "contributions": {"pension": 8000, "tfsa": 7000, "rrsp": 10000, "nonreg": 0},
         "contributions_when_partner_retired": None},
        {"id": "B", "name": "Partner B", "sex": "male", "age": 48, "retire_age": 58,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 20, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 38,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "salary": 90000,
         "contributions": {"pension": 6000, "tfsa": 7000, "rrsp": 8000, "nonreg": 0},
         "contributions_when_partner_retired": None},
    ],
    "spending": {"base": 80000,
                 "changes": [{"year": 2035, "amount": -10000, "label": "Kids leave home"}],
                 "slow_go_age": 75, "slow_go_share": 0.85, "no_go_age": 85, "no_go_share": 0.70,
                 "care": 25000, "bad_market_cut": 0.10, "bad_market_trigger": 0.80,
                 "survivor_share": 0.70},
    "home": {"value": 900000, "downsize_age": 65, "new_value": 600000, "selling_cost": 0.04,
             "moving_cost": 20000, "property_tax": 6000, "insurance": 2000},
    "returns": {"mean": 0.05, "sd": 0.15, "paths": 10000, "seed": 7},
    "withdrawal": {"strategy": "rrsp_first", "steady_income_target": 58000},
    "nonreg_income": {"eligible_dividends": 0.015, "foreign_dividends": 0.01, "interest": 0.0},
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
            people=tuple(_person(p) for p in d["people"]),
            spending=Spending(**spending),
            home=None if d.get("home") is None else Home(**d["home"]),
            returns=Returns(**d.get("returns", {})),
            withdrawal=Withdrawal(**d.get("withdrawal", {})),
            accounts=tuple(Account(**a) for a in d.get("balances") or []),
            nonreg_income=NonregIncome(**(d.get("nonreg_income") or {})),
            education=_education(d.get("education")),
            events=tuple(Event(**e) for e in d.get("events") or []),
            saved_scenarios=tuple((str(s["name"]), dict(s.get("changes") or {}))
                                  for s in d.get("saved_scenarios") or []),
            holdings=dict(d.get("holdings") or {}),
            scenarios=dict(d.get("scenarios") or {}),
        )
    except KeyError as e:
        raise ValueError(f"plan.json is missing the field {e}") from e
    except TypeError as e:
        raise ValueError(f"plan.json has an unexpected or missing field: {e}") from e


def _set(obj, keys: list, value):
    key, rest = keys[0], keys[1:]
    if isinstance(obj, dict):                    # e.g. contributions, education costs
        new = dict(obj)
        new[key] = _set(obj[key], rest, value) if rest else value
        return new
    if isinstance(obj, tuple):
        items = list(obj)
        i = int(key)
        items[i] = _set(items[i], rest, value) if rest else value
        return tuple(items)
    if not is_dataclass(obj) or key not in {f.name for f in fields(obj)}:
        raise KeyError(key)
    return replace(obj, **{key: _set(getattr(obj, key), rest, value) if rest else value})


def apply_changes(plan: PlanInputs, changes: dict) -> PlanInputs:
    """``plan`` with each ``{dotted.path: value}`` set, e.g. ``people.0.retire_age``
    (0-based, as in the GUI). Raises KeyError / IndexError / ValueError on a bad path."""
    for path, value in changes.items():
        try:
            plan = _set(plan, path.split("."), value)
        except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
            raise ValueError(f"no field {path!r}") from e
    return plan


def _person(d: dict) -> Person:
    d = dict(d)
    if d.get("espp") is not None:
        d["espp"] = Espp(**d["espp"])
    return Person(**d)


class PlanError(ValueError):
    """A plan.json value that fails validation; ``field`` is its path, e.g. ``people[0].age``."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field


def _education(d: dict | None) -> Education | None:
    if d is None:
        return None
    d = dict(d)
    kids = tuple(Kid(**k) for k in d.pop("kids", []))
    costs = {**EDUCATION_COSTS, **(d.pop("costs", None) or {})}
    return Education(kids=kids, costs=costs, **d)


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and bool(np.isfinite(x))


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _fail(field_name: str, message: str):
    raise PlanError(field_name, message)


def limits(province: str) -> dict:
    """The statutory age and share limits ``validate`` enforces, read from rules.py
    (the GUI's sliders use the same numbers)."""
    cpp, oas, lif = rules.CPP["adjustment"].value, rules.OAS["deferral"].value, rules.LIF[province]
    cesg, aip = rules.RESP["cesg"].value, rules.RESP["aip"].value
    return {"cpp_start_age": (cpp["min_age"], cpp["max_age"]),
            "oas_start_age": (oas["min_age"], oas["max_age"]),
            "convert_by_age": rules.RRIF["convert_by_age"].value,
            "lif_min_age": lif["min_age"].value,
            "unlock_share": lif["unlock_share"].value,
            "kid_start_age": (15, 30), "kid_years": (1, 10),          # plan bounds, not rules
            "survivor_share": (0.4, 1.0),
            "guardrail_band": (0.05, 0.5), "guardrail_step": (0.02, 0.5),
            "guardrail_floor": (0.3, 1.0), "guardrail_ceiling": (1.0, 3.0),
            "cesg_rate": cesg["rate"], "cesg_lifetime": cesg["lifetime_max"],
            "student_grant_max": rules.STUDENT_GRANT.value["yearly_max"],
            "aip_rrsp_max": aip["rrsp_transfer_max"], "aip_extra_tax": aip["extra_tax"]}


def defaults() -> dict:
    """Defaults for the optional sections the GUI can add, for filling blanks."""
    flags = {f.name: f.default for f in fields(Education) if isinstance(f.default, bool)}
    kid = {f.name: f.default for f in fields(Kid) if f.default not in (MISSING, None)}
    return {"education": {**flags, "costs": dict(EDUCATION_COSTS)}, "kid": kid}


def validate(plan: PlanInputs) -> PlanInputs:
    """Return ``plan`` unchanged, or raise ValueError naming the offending field."""
    if plan.province not in rules.PROVINCIAL or plan.province not in rules.LIF:
        _fail("province", f"{plan.province!r} is not in rules.py (have {sorted(rules.PROVINCIAL)})")
    if not 1 <= len(plan.people) <= 2:
        _fail("people", "need one or two people")
    ids = [p.id for p in plan.people]
    if len(set(ids)) != len(ids):
        _fail("people", "ids must be unique")
    lim = limits(plan.province)
    lif_min, max_unlock, convert_by = lim["lif_min_age"], lim["unlock_share"], lim["convert_by_age"]
    (cpp_lo, cpp_hi), (oas_lo, oas_hi) = lim["cpp_start_age"], lim["oas_start_age"]
    for i, p in enumerate(plan.people):
        f = f"people[{i}]"
        if not 18 <= p.age < plan.end_age:
            _fail(f"{f}.age", f"{p.age} must be 18 or more and below end_age {plan.end_age}")
        if p.age >= mortality.OMEGA - 1:
            _fail(f"{f}.age", f"{p.age} is past the life table (ages up to {mortality.OMEGA - 2})")
        if p.sex is not None and p.sex not in mortality.SEXES:
            _fail(f"{f}.sex", f"{p.sex!r} is not one of {mortality.SEXES} (or leave it out)")
        if p.retire_age < p.age:
            _fail(f"{f}.retire_age", f"{p.retire_age} is below age {p.age}")
        if p.retire_age >= plan.end_age:
            _fail(f"{f}.retire_age", f"{p.retire_age} must be below end_age {plan.end_age}")
        if not cpp_lo <= p.cpp_start_age <= cpp_hi:
            _fail(f"{f}.cpp_start_age", f"CPP starts between {cpp_lo} and {cpp_hi}")
        if not oas_lo <= p.oas_start_age <= oas_hi:
            _fail(f"{f}.oas_start_age", f"OAS starts between {oas_lo} and {oas_hi}")
        if p.rrif_start_age > convert_by:
            _fail(f"{f}.rrif_start_age", f"an RRSP must become a RRIF by {convert_by}")
        if p.lif_start_age is not None and p.lif_start_age < lif_min:
            _fail(f"{f}.lif_start_age", f"a LIF can start at {lif_min} at the earliest")
        if p.lif_start_age is not None and p.lif_start_age > convert_by:
            _fail(f"{f}.lif_start_age", f"locked-in money must become a LIF by {convert_by}")
        if not 0 <= p.unlock_share <= max_unlock:
            _fail(f"{f}.unlock_share", f"between 0 and {max_unlock}")
        if not 0 <= p.cpp_earnings_ratio <= 1:
            _fail(f"{f}.cpp_earnings_ratio", "between 0 and 1")
        for name in ("cpp_years", "years_in_canada_at_65", "tfsa_room"):
            if getattr(p, name) < 0:
                _fail(f"{f}.{name}", "must not be negative")
        if p.cpp_at_65 is not None and p.cpp_at_65 < 0:
            _fail(f"{f}.cpp_at_65", "must not be negative")
        if not p.pension_match >= 0:
            _fail(f"{f}.pension_match", "a multiple of your own contribution, 0 or more")
        if p.rrsp_room is not None and not p.rrsp_room >= 0:
            _fail(f"{f}.rrsp_room", "must not be negative")
        if p.espp is not None:
            if not 0 <= p.espp.rate <= 1:
                _fail(f"{f}.espp.rate", "a share of salary between 0 and 1")
            if not p.espp.cap >= 0:
                _fail(f"{f}.espp.cap", "must not be negative")
            if not 0 <= p.espp.discount < 1:
                _fail(f"{f}.espp.discount", "between 0 and 1 (e.g. 0.15)")
        if p.salary is not None and p.salary < 0:
            _fail(f"{f}.salary", "must not be negative")
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
    lo, hi = lim["survivor_share"]
    if not lo <= s.survivor_share <= hi:
        _fail("spending.survivor_share", f"between {lo} and {hi}")
    if s.rule not in SPENDING_RULES:
        _fail("spending.rule", f"{s.rule!r} is not one of {SPENDING_RULES}")
    for name in ("guardrail_band", "guardrail_step", "guardrail_floor", "guardrail_ceiling"):
        lo, hi = lim[name]
        if not lo <= getattr(s, name) <= hi:
            _fail(f"spending.{name}", f"between {lo} and {hi}")
    if not s.guardrail_stop_years >= 0:
        _fail("spending.guardrail_stop_years", "must not be negative")
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
        if h.downsize_age is not None and h.downsize_age < plan.people[0].age:
            _fail("home.downsize_age", f"{h.downsize_age} has already passed "
                                       f"(people[0] is {plan.people[0].age}); set today's home value")
        if h.downsize_age is not None and h.value * (1 - h.selling_cost) - h.new_value - h.moving_cost < 0:
            _fail("home.new_value", "the sale must cover the new home and the move")
    r = plan.returns
    if r.mean <= -1 or r.sd < 0 or r.paths < 1:
        _fail("returns", "need mean > -1, sd >= 0 and paths >= 1")
    for name in ("eligible_dividends", "foreign_dividends", "interest"):
        if not 0 <= getattr(plan.nonreg_income, name) <= 0.2:
            _fail(f"nonreg_income.{name}", "a yearly share of the balance between 0 and 0.2")
    if plan.withdrawal.strategy not in STRATEGIES:
        _fail("withdrawal.strategy", f"{plan.withdrawal.strategy!r} is not one of {STRATEGIES}")
    if plan.withdrawal.steady_income_target < 0:
        _fail("withdrawal.steady_income_target", "must not be negative")
    e = plan.education
    if e is not None:
        for living, cost in e.costs.items():
            if living not in LIVING or not cost >= 0:
                _fail(f"education.costs.{living}", f"a yearly cost (not negative) for one of {LIVING}")
        if not e.childcare >= 0:
            _fail("education.childcare", "must not be negative")
        for name in ("resp_balance", "contributed", "grants"):
            if getattr(e, name) is not None and not getattr(e, name) >= 0:
                _fail(f"education.{name}", "must not be negative")
        for k, kid in enumerate(e.kids):
            f = f"education.kids[{k}]"
            if not 0 <= kid.age <= 30:
                _fail(f"{f}.age", "between 0 and 30")
            (lo, hi), (ylo, yhi) = lim["kid_start_age"], lim["kid_years"]
            if not lo <= kid.start_age <= hi:
                _fail(f"{f}.start_age", f"between {lo} and {hi}")
            if not ylo <= kid.years <= yhi:
                _fail(f"{f}.years", f"between {ylo} and {yhi}")
            if kid.living not in LIVING:
                _fail(f"{f}.living", f"one of {LIVING}")
            if kid.cesg_received is not None and not (
                    0 <= kid.cesg_received <= rules.RESP["cesg"].value["lifetime_max"]):
                _fail(f"{f}.cesg_received", "between 0 and the lifetime grant maximum")
    owner = {p.id: p for p in plan.people}
    for j, ev in enumerate(plan.events):
        f = f"events[{j}]"
        if not _is_number(ev.amount):
            _fail(f"{f}.amount", "must be a number")
        for name in ("year", "age", "every", "until", "until_age"):
            if getattr(ev, name) is not None and not _is_int(getattr(ev, name)):
                _fail(f"{f}.{name}", "must be a whole number")
        if ev.kind not in EVENT_KINDS:
            _fail(f"{f}.kind", f"{ev.kind!r} is not one of {EVENT_KINDS}")
        if (ev.year is None) == (ev.age is None):
            _fail(f"{f}.year", "give exactly one of year or age")
        if ev.every is not None and ev.every < 1:
            _fail(f"{f}.every", "repeat every 1 year or more")
        if ev.until is not None and ev.year is not None and ev.until < ev.year:
            _fail(f"{f}.until", "must not be before the start year")
        if ev.until_age is not None and ev.age is not None and ev.until_age < ev.age:
            _fail(f"{f}.until_age", "must not be before the start age")
        if ev.kind == "income" and not ev.amount >= 0:
            _fail(f"{f}.amount", "yearly income must not be negative")
        if ev.person is not None and ev.person not in ids:
            _fail(f"{f}.person", f"{ev.person!r} is not one of the people {ids}")
        ref = owner.get(ev.person, plan.people[0])
        start = ev.year if ev.year is not None else plan.start_year + ev.age - ref.age
        if ev.until_age is not None and plan.start_year + ev.until_age - ref.age < start:
            _fail(f"{f}.until_age", "ends before it starts")
        if ev.until is not None and ev.until < start:
            _fail(f"{f}.until", "must not be before the start year")
        if start < plan.start_year and ev.every is None and ev.kind == "cash":
            _fail(f"{f}.year" if ev.year is not None else f"{f}.age", "is before the plan starts")
    for j, a in enumerate(plan.accounts):
        if a.owner not in ids:
            _fail(f"balances[{j}].owner", f"{a.owner!r} is not one of the people {ids}")
        if a.type not in ACCOUNT_TYPES:
            _fail(f"balances[{j}].type", f"{a.type!r} is not one of {ACCOUNT_TYPES}")
        if not (np.isfinite(a.balance) and a.balance >= 0):
            _fail(f"balances[{j}].balance", "must be a number, not negative")
        if a.cost is not None and not (np.isfinite(a.cost) and a.cost >= 0):
            _fail(f"balances[{j}].cost", "must be a number, not negative")
    for j, (name, changes) in enumerate(plan.saved_scenarios):
        try:
            validate(apply_changes(replace(plan, saved_scenarios=()), changes))
        except Exception as e:                   # a bad path, a wrong type, an invalid value
            _fail(f"saved_scenarios[{j}]", f"{name!r}: {e}")
    return plan


def load_inputs(path) -> PlanInputs:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No plan at {path}. Create one with: stock-analysis retire --init --inputs {path}")
    return parse(json.loads(path.read_text(encoding="utf-8")))


def parse(d: dict) -> PlanInputs:
    """A plan.json dict -> validated PlanInputs (ValueError names the offending field)."""
    return validate(_build(d))


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
            if any(re.search(rf"\b{re.escape(w)}\b", name, re.I) for w in words)]
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
    unknown, unowned, broken, totals = [], [], [], {}
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
        if not np.isfinite(rows["value_cad"].to_numpy(dtype=float)).all():
            broken.append(str(name))
            continue
        balance = float(rows["value_cad"].sum())
        cost = float(sum(_cost_cad(r) for _, r in rows.iterrows()))
        b, c = totals.get((who, kind), (0.0, 0.0))
        totals[(who, kind)] = (b + balance, c + cost)
    if broken:
        raise ValueError("A holding has no value (e.g. #N/A in the sheet) in: " + ", ".join(broken)
                         + ". Fix the sheet and refresh, so no money is silently left out.")
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


def resp_from_holdings(frame: pd.DataFrame, mapping: dict) -> float:
    """The holdings' RESP accounts (the ones ``balances_from_holdings`` leaves out)."""
    rows = frame[[_classify(str(a), mapping) == "exclude" for a in frame["account"]]]
    return float(rows["value_cad"].sum())


def with_holdings(plan: PlanInputs, frame: pd.DataFrame) -> PlanInputs:
    """``plan`` with its accounts replaced by the sorted holdings (and, when the plan
    has an education section without a balance, its RESP balance from them)."""
    plan = replace(plan, accounts=balances_from_holdings(frame, plan.holdings, plan.people))
    if plan.education is not None and plan.education.resp_balance is None:
        plan = replace(plan, education=replace(plan.education,
                                               resp_balance=resp_from_holdings(frame, plan.holdings)))
    return validate(plan)
