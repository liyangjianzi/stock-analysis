"""Planning tools that search the plan instead of changing one thing at a time.

- :func:`max_spending`: the highest base spending that keeps the chance the money
  lasts at or above a target.
- :func:`earliest_retirement`: the earliest retirement, every person shifted by the
  same number of years, that keeps that chance.
- :func:`best_benefit_ages`: the CPP and OAS start ages that leave the largest
  expected after-tax legacy over drawn lifespans (a steady median return), then
  compared with today's ages on the simulated futures.

Every candidate sees the same random futures, returns and lifespans (common random
numbers, as in :mod:`.scenarios`), so a difference between two candidates is the
change's effect.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from functools import partial
from dataclasses import dataclass, field, replace

import numpy as np

from . import engine, mortality
from .inputs import PlanInputs, limits

SPENDING_STEP = 1_000          # spending answers are rounded down to this, in today's C$


LIFESPAN_DRAWS = 300           # death draws behind each candidate's expected legacy


def _futures(plan: PlanInputs, paths: int, seed: int) -> tuple:
    return engine.draw_futures(plan, paths, seed)


def _success(plan: PlanInputs, F: tuple) -> float:
    return engine.simulate(plan, *F).success


def _expected_legacy(plan: PlanInputs, deaths: np.ndarray) -> float:
    """Mean after-tax legacy over ``deaths`` at a steady median return: what a start
    age is worth once you might not live to collect it."""
    r = plan.returns
    g = engine.median_return(r.mean, r.sd)
    proj = engine.simulate(plan, np.full((engine.life_steps(plan), deaths.shape[1]), g), deaths)
    return float(proj.legacy.mean())


def _with_spending(plan: PlanInputs, base: float) -> PlanInputs:
    return replace(plan, spending=replace(plan.spending, base=float(base)))


def _shift_retirement(plan: PlanInputs, years: int) -> PlanInputs:
    return replace(plan, people=tuple(replace(p, retire_age=p.retire_age + years) for p in plan.people))


# -- spending and retirement age ----------------------------------------------------

@dataclass(frozen=True)
class Affordability:
    target: float
    success: float                  # the plan as it stands
    spending: float                 # its base spending
    max_spending: float | None      # None: even no spending misses the target
    max_spending_success: float | None
    retire_shift: int | None        # years earlier (<0) or later (>0); None: never reaches it
    retire_ages: tuple = ()         # (person name, age) at that shift
    retire_success: float | None = None


def max_spending(plan: PlanInputs, target: float, F: tuple) -> tuple:
    """(highest base spending, rounded down to SPENDING_STEP, with success >= ``target``;
    its success). (None, None) when even zero spending misses the target."""
    def ok(k: int) -> bool:                                     # k steps of SPENDING_STEP
        return _success(_with_spending(plan, k * SPENDING_STEP), F) >= target

    if not ok(0):
        return None, None
    lo, hi = 0, max(int(plan.spending.base // SPENDING_STEP), 50)
    while ok(hi):                                               # find a failing upper bound
        lo, hi = hi, hi * 2
    while hi - lo > 1:                                          # lo passes, hi fails
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    best = float(lo * SPENDING_STEP)
    return best, _success(_with_spending(plan, best), F)


def earliest_retirement(plan: PlanInputs, target: float, F: tuple) -> tuple:
    """(shift in years, its success) for the earliest common shift of everyone's
    retirement that keeps success >= ``target``; (None, None) if none does."""
    lo = max(p.age - p.retire_age for p in plan.people)          # nobody retires in the past
    hi = min(plan.end_age - 1 - p.retire_age for p in plan.people)
    if lo > hi or _success(_shift_retirement(plan, hi), F) < target:
        return None, None
    while lo < hi:                                               # smallest passing shift
        mid = (lo + hi) // 2
        if _success(_shift_retirement(plan, mid), F) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo, _success(_shift_retirement(plan, lo), F)


def affordability(plan: PlanInputs, *, target: float = 0.90, paths: int = 1_000,
                  seed: int | None = None) -> Affordability:
    """How much the household can spend, and how early it can retire, at ``target``."""
    F = _futures(plan, paths, plan.returns.seed if seed is None else seed)
    spend, spend_ok = max_spending(plan, target, F)
    shift, shift_ok = earliest_retirement(plan, target, F)
    ages = () if shift is None else tuple((p.name, p.retire_age + shift) for p in plan.people)
    return Affordability(target, _success(plan, F), plan.spending.base, spend, spend_ok,
                         shift, ages, shift_ok)


# -- CPP and OAS start ages --------------------------------------------------------

@dataclass(frozen=True)
class BenefitChoice:
    name: str
    current: tuple                  # (cpp_start_age, oas_start_age) today
    best: tuple
    by_cpp: dict = field(default_factory=dict)   # CPP age -> legacy, OAS at its best age
    by_oas: dict = field(default_factory=dict)   # OAS age -> legacy, CPP at its best age


@dataclass(frozen=True)
class BenefitAges:
    people: tuple                   # BenefitChoice per person
    legacy: float                   # expected legacy over lifespans, today's ages
    best_legacy: float              # the same, best ages
    success: float                  # chance the money lasts, today's ages
    best_success: float
    plan: PlanInputs                # the plan with the best ages


def _with_ages(plan: PlanInputs, i: int, cpp: int, oas: int) -> PlanInputs:
    people = list(plan.people)
    people[i] = replace(people[i], cpp_start_age=cpp, oas_start_age=oas)
    return replace(plan, people=tuple(people))


def best_benefit_ages(plan: PlanInputs, *, paths: int = 1_000, seed: int | None = None,
                      cpp_ages=None, oas_ages=None, rounds: int = 2,
                      workers: int | None = None) -> BenefitAges:
    """CPP/OAS start ages that maximize the expected after-tax legacy over lifespans.

    Each person's full CPP x OAS grid is searched with the others held fixed, and
    that repeats (up to ``rounds`` passes) until nobody's best ages change. Ties keep
    the current ages. ``cpp_ages`` / ``oas_ages`` default to every legal age. Grid
    points run in a process pool (``workers=1``: in this process); each point is one
    ~0.4 s run over ``LIFESPAN_DRAWS`` lifespans, about 20 s in all on 10 cores.
    """
    lim = limits(plan.province)
    cpp_ages = tuple(cpp_ages or range(lim["cpp_start_age"][0], lim["cpp_start_age"][1] + 1))
    oas_ages = tuple(oas_ages or range(lim["oas_start_age"][0], lim["oas_start_age"][1] + 1))
    s = plan.returns.seed if seed is None else seed
    D = mortality.draw_death_ages(plan.people, plan.start_year, LIFESPAN_DRAWS, s)
    score = partial(_expected_legacy, deaths=D)
    with nullcontext() if workers == 1 else ProcessPoolExecutor(workers) as pool:
        best, grids = _search(plan, cpp_ages, oas_ages, rounds, pool.map if pool else map, score)
    choices = []
    for i, (p, b) in enumerate(zip(plan.people, best.people)):
        grid, pick = grids[i], (b.cpp_start_age, b.oas_start_age)
        choices.append(BenefitChoice(
            p.name, (p.cpp_start_age, p.oas_start_age), pick,
            {c: grid[(c, pick[1])] for c in cpp_ages},
            {o: grid[(pick[0], o)] for o in oas_ages}))
    F = _futures(plan, paths, s)
    return BenefitAges(tuple(choices), score(plan), score(best), _success(plan, F), _success(best, F), best)


def _search(plan: PlanInputs, cpp_ages, oas_ages, rounds: int, run, score) -> tuple:
    """Coordinate search: (the best plan, each person's last legacy grid). ``run`` is
    a map function (the pool's, or the builtin)."""
    best, grids = plan, {}
    for _ in range(rounds):
        moved = False
        for i, p in enumerate(best.people):
            keys = [(c, o) for c in cpp_ages for o in oas_ages]
            grid = dict(zip(keys, run(score, [_with_ages(best, i, *k) for k in keys])))
            now = (p.cpp_start_age, p.oas_start_age)
            pick = max(grid, key=lambda k: (grid[k], k == now))
            grids[i] = grid
            if pick != now:
                best, moved = _with_ages(best, i, *pick), True
        if not moved or len(plan.people) == 1:
            break
    return best, grids
