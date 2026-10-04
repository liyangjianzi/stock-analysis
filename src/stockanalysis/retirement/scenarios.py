"""What-if suggestions: rerun the plan with exactly one change, and rank the results.

Every variant uses the same seed, so all of them see the same random futures
(common random numbers). A difference in success is then the change's effect,
not noise. Lifetime tax and legacy come from each variant's average future.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import engine
from .inputs import STRATEGIES, STRATEGY_LABELS, PlanInputs


@dataclass(frozen=True)
class Suggestion:
    key: str
    label: str
    success: float
    delta: float
    lifetime_tax: float
    legacy: float


def variants(plan: PlanInputs) -> list:
    """(key, label, variant plan) for every one-change what-if."""
    out = []
    for strategy in STRATEGIES:
        if strategy != plan.withdrawal.strategy:
            out.append((f"withdraw_{strategy}", STRATEGY_LABELS[strategy],
                        replace(plan, withdrawal=replace(plan.withdrawal, strategy=strategy))))
    out.append(("spend_less", "Reduce retirement spending by 5%",
                replace(plan, spending=replace(plan.spending, base=plan.spending.base * 0.95))))
    for i, p in enumerate(plan.people):
        if p.age < p.retire_age and p.retire_age + 1 < plan.end_age:   # not already retired
            people = list(plan.people)
            people[i] = replace(p, retire_age=p.retire_age + 1)
            out.append((f"retire_later_{p.id}", f"{p.name}: retire 1 year later",
                        replace(plan, people=tuple(people))))
    home = plan.home
    if home is not None:
        for age in plan.scenarios.get("downsize_ages", []):
            if age == home.downsize_age or (age is not None and age < plan.people[0].age):
                continue
            key, label = ("downsize_never", "Never downsize") if age is None else (
                f"downsize_{age}", f"Downsize at {age}")
            out.append((key, label, replace(plan, home=replace(home, downsize_age=age))))
        if home.downsize_age is not None:
            new = home.new_value * plan.scenarios.get("cheaper_home_share", 0.8)
            out.append(("cheaper_home", f"Buy a C${new:,.0f} home instead of C${home.new_value:,.0f}",
                        replace(plan, home=replace(home, new_value=new))))
    return out


def evaluate(plan: PlanInputs, *, paths: int, seed: int) -> tuple:
    """(success over ``paths`` futures with drawn lifespans, average-future lifetime tax,
    average-future legacy)."""
    r, T = plan.returns, engine.steps(plan)
    simulated = engine.simulate(plan, *engine.draw_futures(plan, paths, seed))
    average = engine.simulate(plan, np.full((T, 1), engine.median_return(r.mean, r.sd)),
                              engine.average_deaths(plan))
    return simulated.success, float(average.lifetime_tax[0]), float(average.legacy[0])


def rank(plan: PlanInputs, *, paths: int, seed: int) -> tuple:
    """(baseline, suggestions sorted by change in success, then legacy)."""
    s0, t0, l0 = evaluate(plan, paths=paths, seed=seed)
    baseline = Suggestion("baseline", "Current plan", s0, 0.0, t0, l0)
    ranked = []
    for key, label, variant in variants(plan):
        s, t, legacy = evaluate(variant, paths=paths, seed=seed)
        ranked.append(Suggestion(key, label, s, s - s0, t, legacy))
    ranked.sort(key=lambda x: (-x.delta, -x.legacy))
    return baseline, ranked
