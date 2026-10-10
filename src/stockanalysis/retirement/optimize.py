"""Planning tools that search the plan instead of changing one thing at a time.

- :func:`max_spending`: the highest base spending that keeps the chance the money
  lasts at or above a target.
- :func:`earliest_retirement`: the earliest retirement, every person shifted by the
  same number of years, that keeps that chance.
- :func:`rrsp_drawdown`: every yearly RRSP-draw (``steady_income``) target, with
  expected legacy, lifetime tax and the chance the money lasts; ``best(goal)`` picks.
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


def _lifespan_run(plan: PlanInputs, deaths: np.ndarray):
    """The plan at the average future's steady returns over each of ``deaths``' lifespans."""
    g = engine.average_returns(plan, engine.life_steps(plan))
    return engine.simulate(plan, np.repeat(g, deaths.shape[1], axis=1), deaths)


def _expected_legacy(plan: PlanInputs, deaths: np.ndarray) -> float:
    """Mean after-tax legacy over ``deaths`` at the average future's steady returns: what a start
    age is worth once you might not live to collect it."""
    return float(_lifespan_run(plan, deaths).legacy.mean())


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


# -- how much to draw from RRSPs ----------------------------------------------------

DRAWDOWN_TARGETS = tuple(range(0, 100_001, 5_000))   # steady_income targets tried, C$ per person
GOALS = ("legacy", "tax", "success")


@dataclass(frozen=True)
class DrawdownRow:
    target: float | None            # steady_income target per person; None: the plan as it stands
    label: str
    success: float                  # chance the money lasts, shared futures
    legacy: float                   # expected after-tax legacy over lifespans
    lifetime_tax: float             # expected lifetime tax over the same lifespans
    first_retired_tax: float        # average future, the first year nobody works


@dataclass(frozen=True)
class DrawdownTable:
    rows: tuple                     # one DrawdownRow per target, lowest first
    current: DrawdownRow

    def best(self, goal: str) -> DrawdownRow:
        """The row that does best on ``goal`` (most legacy / least tax / highest
        success); ties keep the lower target, i.e. less drawn."""
        if goal not in GOALS:
            raise ValueError(f"goal must be one of {GOALS}")
        key = {"legacy": lambda r: -r.legacy, "tax": lambda r: r.lifetime_tax,
               "success": lambda r: -r.success}[goal]
        return min(self.rows, key=key)          # min keeps the first (lowest) of equals


def _with_target(plan: PlanInputs, target: float) -> PlanInputs:
    return replace(plan, withdrawal=replace(plan.withdrawal, strategy="steady_income",
                                            steady_income_target=float(target)))


def _drawdown_row(plan: PlanInputs, F: tuple, deaths: np.ndarray, target, label: str) -> DrawdownRow:
    life = _lifespan_run(plan, deaths)
    T = engine.steps(plan)
    avg = engine.simulate(plan, engine.average_returns(plan, T), engine.average_deaths(plan))
    first = float(avg.tax[avg.retire_step, 0]) if avg.retire_step < T else 0.0
    return DrawdownRow(target, label, _success(plan, F), float(life.legacy.mean()),
                       float(life.lifetime_tax.mean()), first)


def _drawdown_point(args) -> DrawdownRow:
    plan, F, deaths, target = args
    return _drawdown_row(_with_target(plan, target), F, deaths, target, f"${target / 1000:.0f}k each")


def rrsp_drawdown(plan: PlanInputs, *, paths: int = 1_000, seed: int | None = None,
                  targets=DRAWDOWN_TARGETS, workers: int | None = None) -> DrawdownTable:
    """Every ``steady_income`` target (each person's taxable income topped up with RRSP
    money, which draws most before CPP, OAS and the RRIF minimums fill the room) and
    the plan as it stands, on the same futures and lifespans. Pick with ``best``."""
    s = plan.returns.seed if seed is None else seed
    F = _futures(plan, paths, s)
    D = mortality.draw_death_ages(plan.people, plan.start_year, LIFESPAN_DRAWS, s)
    w = plan.withdrawal
    now = ("Now: RRSP first" if w.strategy == "rrsp_first" else
           "Now: proportional" if w.strategy == "proportional" else
           f"Now: ${w.steady_income_target / 1000:.0f}k each")
    current = _drawdown_row(plan, F, D, None, now)
    with nullcontext() if workers == 1 else ProcessPoolExecutor(workers) as pool:
        run = pool.map if pool else map
        rows = tuple(run(_drawdown_point, [(plan, F, D, t) for t in targets]))
    return DrawdownTable(rows, current)
