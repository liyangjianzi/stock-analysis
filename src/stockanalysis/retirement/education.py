"""Children's education: the RESP's yearly schedule.

Everything here is the same on every return path, so it is worked out once:
each January's contribution and grant (CESG), and each year's school cost. The
engine walks the RESP balance itself, because that depends on returns.

Contributions follow one rule: put in, each January, exactly what earns the
largest grant still available for each child (nothing once the lifetime grant is
reached or the child is past the last grant year). The start year's
contribution is assumed made and already in the balance, so contributions begin
the year after. Statutory values come from :data:`rules.RESP`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import rules


@dataclass(frozen=True)
class KidPlan:
    name: str
    living: str
    yearly_cost: float
    school_years: tuple          # calendar years in school
    contributions: float         # planned from next January on
    grants: float                # CESG those contributions earn
    grant_left: float            # lifetime grant still unclaimed after them


@dataclass(frozen=True)
class Schedule:
    contribution: np.ndarray     # (T,) household money into the RESP each January
    grant: np.ndarray            # (T,) CESG paid in with it
    cost: np.ndarray             # (T,) school costs that year, before grants and the RESP
    students: np.ndarray         # (T,) children in school that year
    balance: float               # RESP today
    contributed: float           # contributions so far (estimated if not given)
    grants: float                # CESG so far (estimated if not given)
    end_step: int | None         # first step after all school (0 if already over); None if beyond the plan
    kids: tuple                  # KidPlan per child


def estimated_grant_received(birth_year: int, year: int) -> float:
    """The CESG a child has if every year's grant was collected, through ``year``."""
    c = rules.RESP["cesg"].value
    first = max(birth_year, c["room_from_year"])
    last = min(year, birth_year + c["last_age"])
    return float(min(c["lifetime_max"], c["yearly_max"] * max(0, last - first + 1)))


def schedule(plan, steps: int) -> Schedule | None:
    """The plan's education schedule over ``steps`` years, or None without children."""
    e = plan.education
    if e is None or not e.kids:
        return None
    c = rules.RESP["cesg"].value
    limit = rules.RESP["contribution_lifetime_max"].value
    births = [plan.start_year - k.age for k in e.kids]
    received = [k.cesg_received if k.cesg_received is not None
                else estimated_grant_received(b, plan.start_year) for k, b in zip(e.kids, births)]
    grants0 = e.grants if e.grants is not None else float(sum(received))
    contributed0 = e.contributed if e.contributed is not None else grants0 / c["rate"]
    put_in = [contributed0 / len(e.kids)] * len(e.kids)       # lifetime limit is per child

    contribution, grant, cost, students = (np.zeros(steps) for _ in range(4))
    planned_in, planned_grant = [0.0] * len(e.kids), [0.0] * len(e.kids)
    for t in range(steps):
        year = plan.start_year + t
        for i, (kid, born) in enumerate(zip(e.kids, births)):
            age = year - born
            if kid.start_age <= age < kid.start_age + kid.years:
                cost[t] += e.costs[kid.living] * (1 + plan.cost_growth.rate("education")) ** t
                students[t] += 1
            if not e.contribute or t == 0 or age > c["last_age"]:
                continue
            room = estimated_grant_received(born, year) - received[i]     # unused room to date
            g = max(0.0, min(room, c["yearly_max_catch_up"], c["lifetime_max"] - received[i]))
            amount = min(g / c["rate"], max(0.0, limit - put_in[i]))
            g = amount * c["rate"]
            if amount <= 0:
                continue
            contribution[t] += amount
            grant[t] += g
            received[i] += g
            put_in[i] += amount
            planned_in[i] += amount
            planned_grant[i] += g
    # The first January after all school (now, if it's already over).
    end = max(0, max(b + k.start_age + k.years for k, b in zip(e.kids, births)) - plan.start_year)
    kids = tuple(KidPlan(k.name, k.living, e.costs[k.living],
                         tuple(range(b + k.start_age, b + k.start_age + k.years)),
                         planned_in[i], planned_grant[i], c["lifetime_max"] - received[i])
                 for i, (k, b) in enumerate(zip(e.kids, births)))
    return Schedule(contribution, grant, cost, students, float(e.resp_balance or 0.0), float(contributed0),
                    float(grants0), end if end < steps else None, kids)


def student_grant(family_income, family_size: int) -> np.ndarray:
    """Canada Student Grant per full-time student for a year, from the parents' total
    income (line 15000) of the year before. Full below the first threshold, none at
    the cut-off, falling in a straight line between (the program publishes only the
    two thresholds; the line between them is this model's assumption)."""
    spec = rules.STUDENT_GRANT.value
    full, cutoff = spec["thresholds"][min(max(family_size, 1), max(spec["thresholds"]))]
    share = np.clip((cutoff - np.asarray(family_income, dtype=float)) / (cutoff - full), 0.0, 1.0)
    return spec["yearly_max"] * share
