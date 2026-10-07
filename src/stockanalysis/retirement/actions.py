"""The plan as a to-do list: what the household has to do, and when, for the
projection to come true.

The engine assumes a lot of behaviour: moving money into new TFSA room, taking
RRSP money out, converting to a RRIF, splitting pension income, applying for
benefits. None of it happens unless someone does it. :func:`plan_actions` reads
it from what the average future recorded (``Projection.tfsa_top_up``,
``lif_steps``, ``split_share``, ``downsize_step``...), never re-deriving an
engine decision, with years and amounts in today's dollars (``Action.text`` can
scale them to each year's dollars). :func:`missing_inputs`
lists what the plan still guesses at.

Pure: no I/O. Amounts come from the projection; any rule quoted in the text is
read from :mod:`rules`, never typed in.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import rules
from .engine import PlanResult

MIN_AMOUNT = 500.0           # yearly amounts below this aren't worth an action line
MIN_SHIFT = 2_000.0          # a phase changes only when the amount moves at least this much


@dataclass(frozen=True)
class Action:
    year: int                # first (or only) calendar year
    until: int | None        # last year of a recurring action, None if once
    who: str
    what: str                # may hold {} placeholders, one per amount
    why: str
    amounts: tuple = ()      # today's dollars, in placeholder order

    def text(self, factor: float = 1.0) -> str:
        """``what`` with its amounts filled in, scaled by ``factor`` (see future_factor)."""
        return self.what.format(*(_money(a * factor) for a in self.amounts))


def future_factor(plan, year: int) -> float:
    """Today's dollars → that year's dollars on the average inflation path."""
    return (1 + plan.returns.inflation_rate) ** (year - plan.start_year)


def _money(x: float) -> str:
    return f"C${x:,.0f}"


def _runs(years: list, values: np.ndarray, change: float = 0.4) -> list:
    """Phases of a yearly amount: [(first, until, typical amount)], with ``until``
    None for a one-year phase. A phase ends when the amount drops below MIN_AMOUNT
    or moves more than ``change`` (and MIN_SHIFT) from the phase's median, so "40k
    a year, then 90k, then 5k" stays three lines."""
    out, phase = [], []

    def close():
        if phase:
            first, last = years[phase[0]], years[phase[-1]]
            out.append((first, last if last != first else None, float(np.median(values[phase]))))

    for k, v in enumerate(values):
        m = np.median(values[phase]) if phase else 0.0
        if v < MIN_AMOUNT or (phase and abs(v - m) > max(change * m, MIN_SHIFT)):
            close()
            phase = []
        if v >= MIN_AMOUNT:
            phase.append(k)
    close()
    return out


def _amount(until) -> str:
    """The placeholder for a phase's typical amount."""
    return "about {} a year" if until else "{}"


def plan_actions(result: PlanResult) -> list:
    """Dated actions read from what the average future actually did, earliest first."""
    plan, avg = result.inputs, result.average
    people, years = plan.people, avg.years
    year_of = lambda p, age: plan.start_year + (age - p.age)        # noqa: E731
    everyone = " and ".join(p.name for p in people)
    acts: list = []

    for p in people:
        if p.retire_age > p.age:
            acts.append(Action(year_of(p, p.retire_age), None, p.name, "Retire",
                               "the plan's working years end here; contributions stop"))

    for first, until, typical in _runs(years, avg.tfsa_top_up[:, 0]):
        acts.append(Action(first, until, everyone,
                           f"In January, move {_amount(until)} from non-registered "
                           "accounts into TFSAs",
                           "fills new TFSA room: later growth and payouts are tax-free and less "
                           "is taxed at death (the gain on shares moved is taxed that year)",
                           (typical,)))

    for first, until, typical in _runs(years, avg.income["registered"][:, 0]):
        acts.append(Action(first, until, everyone,
                           f"Withdraw {_amount(until)} from RRSPs/RRIFs/LIFs (household)",
                           "the plan's withdrawal order spends registered money here, in lower "
                           "brackets, rather than leaving it to be taxed at the top rate at death",
                           (typical,)))

    for p, lif in zip(people, avg.lif_steps):
        convert = year_of(p, p.rrif_start_age) - plan.start_year
        if 0 <= convert < len(years) and avg.balances["rrsp"][convert, 0] > 0:
            acts.append(Action(years[convert], None, p.name,
                               f"Convert the RRSP to a RRIF by December 31 (age {p.rrif_start_age})",
                               "RRIF payments at 65+ get the pension credit and can be split with a "
                               "spouse; the minimum withdrawal starts the next year"))
        has_locked_in = (any(a.owner == p.id and a.type == "pension" and a.balance > 0
                             for a in plan.accounts) or p.contributions.get("pension", 0) > 0)
        if lif is not None and lif >= 0 and has_locked_in:
            unlock = (f" and unlock up to {p.unlock_share:.0%} of it into the RRSP (one time)"
                      if p.unlock_share > 0 else "")
            acts.append(Action(years[lif], None, p.name,
                               f"Move the locked-in pension (LIRA) into a LIF{unlock}",
                               "the plan starts LIF income here; the one-time unlock is Alberta's"))
        acts.append(Action(year_of(p, p.cpp_start_age), None, p.name,
                           f"Start CPP at {p.cpp_start_age} (apply up to a year ahead)",
                           "the plan's CPP start age; each year later pays more for life"))
        acts.append(Action(year_of(p, p.oas_start_age), None, p.name,
                           f"Start OAS at {p.oas_start_age} (apply up to 11 months ahead)",
                           "the plan's OAS start age; deferring adds "
                           f"{rules.OAS['deferral'].value['per_month']:.1%} a month"))

    if len(people) == 2 and avg.spousal_rrsp is not None:
        for i, p in enumerate(people):
            spouse = people[1 - i].name
            for first, until, typical in _runs(years, avg.spousal_rrsp[:, i, 0]):
                acts.append(Action(first, until, p.name,
                                   f"Contribute {_amount(until)} to a spousal RRSP for {spouse}",
                                   f"you take the deduction now; {spouse} is taxed on it later, "
                                   "usually at a lower rate", (typical,)))
            made = np.flatnonzero(avg.spousal_rrsp[:, i, 0] >= MIN_AMOUNT)
            if made.size:
                free = years[made[-1]] + 3
                acts.append(Action(years[made[-1]], None, spouse,
                                   f"Leave the spousal RRSP alone until {free}: draw your own "
                                   "RRSP first",
                                   f"spousal money taken out within 3 calendar years of a "
                                   f"contribution is taxed to {p.name} instead"))

    split = np.flatnonzero(avg.split_share[:, 0])
    if split.size:
        acts.append(Action(years[split[0]], None, everyone,
                           "Elect pension income splitting on both tax returns every year (Form T1032)",
                           "the plan's tax uses the best split of RRIF/LIF income between you"))

    if avg.downsize_step is not None:
        h = plan.home
        acts.append(Action(years[avg.downsize_step], None, everyone,
                           "Sell the home and buy one for about {}; invest the {} freed up",
                           "the plan's downsizing; a principal residence sells tax-free",
                           (h.new_value, h.released)))

    acts += _education_actions(plan, avg)
    return sorted(acts, key=lambda a: (a.year, a.who, a.what))


def _education_actions(plan, avg) -> list:
    s = avg.school
    if s is None:
        return []
    acts = []
    for t, (c, g) in enumerate(zip(s.contribution, s.grant)):
        if c > 0:
            acts.append(Action(plan.start_year + t, None, "RESP",
                               "Contribute {} to the RESP in January",
                               f"earns {_money(g)} of government grant (20%); after that there is "
                               "no grant left to earn", (c,)))
    last_age = rules.RESP["cesg"].value["last_age"]
    for planned, kid in zip(s.kids, plan.education.kids):
        if plan.education.contribute and planned.contributions == 0 and kid.age < last_age:
            acts.append(Action(plan.start_year + 1, None, "RESP",
                               f"No more RESP contributions needed for {kid.name}",
                               "the lifetime grant looks used up, so more contributions earn "
                               "nothing (confirm the grant received on the RESP statement)"))
    if plan.education.student_grant and avg.student_grant is not None:
        for first, until, typical in _runs(avg.years, avg.student_grant[:, 0]):
            acts.append(Action(first, until, "Students",
                               f"Apply for the Canada Student Grant ({_amount(until)} expected)",
                               "tested on last year's family income; it isn't paid unless applied for",
                               (typical,)))
    if s.end_step is not None:
        acts.append(Action(plan.start_year + s.end_step, None, "RESP",
                           "Close the RESP: take contributions back, move growth to the RRSP "
                           "(up to the limit, if there is room)",
                           "leftover growth is otherwise taxed as income plus 20%"))
    return acts


def missing_inputs(plan) -> list:
    """Plan inputs still guessed at, as plain-language to-dos."""
    out = []
    for p in plan.people:
        if p.rrsp_room is None and p.retire_age > p.age:
            out.append(f"{p.name}: the RRSP deduction limit from the latest Notice of Assessment "
                       "(people[].rrsp_room)")
        if p.cpp_at_65 is None:
            out.append(f"{p.name}: the CPP estimate at 65 from My Service Canada (people[].cpp_at_65)")
    e = plan.education
    if e is not None:
        for k in e.kids:
            if k.cesg_received is None:
                out.append(f"{k.name}: the grant (CESG) received so far, from the RESP statement")
        if e.contributed is None:
            out.append("RESP: total contributions so far, from the RESP statement")
    return out
