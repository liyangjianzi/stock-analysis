"""What-if suggestions: rerun the plan with exactly one change, and rank the results.

Every variant uses the same seed, so all of them see the same random futures
(common random numbers). A difference in success is then the change's effect,
not noise. Lifetime tax and legacy come from each variant's average future.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import engine
from .inputs import STRATEGIES, STRATEGY_LABELS, PlanInputs, apply_changes


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
    if plan.spending.rule == "guardrails":
        out.append(("spend_bad_market", "Use the single bad-market cut instead of guardrails",
                    replace(plan, spending=replace(plan.spending, rule="bad_market"))))
    else:
        s = plan.spending
        out.append(("spend_guardrails",
                    f"Flex spending with guardrails ({s.guardrail_step:.0%} steps, never below "
                    f"{s.guardrail_floor:.0%} of plan)",
                    replace(plan, spending=replace(plan.spending, rule="guardrails"))))
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
    r = plan.returns
    if r.inflation_shocks and r.inflation is None:
        out.append(("steady_inflation", "Steady inflation (no shocks)",
                    replace(plan, returns=replace(r, inflation_shocks=False))))
    if r.model == "history":
        out.append(("smooth_returns", "Smooth returns (old model)",
                    replace(plan, returns=replace(r, model="lognormal"))))
        more = tuple((age, max(0.0, share - 0.10)) for age, share in r.mix)
        if more != r.mix:
            out.append(("more_bonds", "10 points more bonds", replace(plan, returns=replace(r, mix=more))))
    return out


def evaluate(plan: PlanInputs, *, paths: int, seed: int) -> tuple:
    """(success over ``paths`` futures with drawn lifespans, average-future lifetime tax,
    average-future legacy)."""
    T = engine.steps(plan)
    simulated = engine.simulate(plan, *engine.draw_futures(plan, paths, seed))
    average = engine.simulate(plan, engine.average_returns(plan, T), engine.average_deaths(plan))
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


# -- saved scenarios side by side ------------------------------------------------------

@dataclass(frozen=True)
class SavedRow:
    name: str
    success: float                  # chance the money lasts, shared futures
    legacy: float                   # average future
    lifetime_tax: float             # average future
    spending: float                 # base yearly spending
    retire_ages: tuple


def compare_saved(plan: PlanInputs, *, paths: int, seed: int, baseline: Suggestion | None = None) -> list:
    """The plan and each of ``plan.saved_scenarios``, on the same seed (so the same
    random futures), the plan first. ``baseline`` is the plan's own row from
    :func:`rank` on the same paths and seed, reused instead of rerun."""
    base = replace(plan, saved_scenarios=())
    rows = []
    for name, changes in (("Current plan", {}), *plan.saved_scenarios):
        v = apply_changes(base, changes)
        if not changes and baseline is not None:
            s, t, legacy = baseline.success, baseline.lifetime_tax, baseline.legacy
        else:
            s, t, legacy = evaluate(v, paths=paths, seed=seed)
        rows.append(SavedRow(name, s, legacy, t, v.spending.base, tuple(p.retire_age for p in v.people)))
    return rows
