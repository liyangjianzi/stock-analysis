"""Year-by-year Canadian retirement projection over many return paths.

State is kept per person and per path, in today's dollars:
- ``rrsp``: an RRSP, which becomes a RRIF in the year the person reaches
  ``rrif_start_age``;
- ``pension``: locked-in money, a LIRA and then an Alberta LIF from
  ``lif_start_age``;
- ``tfsa``;
- ``nonreg``, with its cost base.

Each step is one calendar year:
1. January 1: TFSA room (the annual limit plus last year's withdrawals); the LIF
   start, with Alberta's one-time unlocking moved to the RRSP; downsizing.
2. The household's after-tax spending need: base, dated changes, stage share,
   the bad-market cut and care costs. While anyone still works, earned income
   covers it.
3. Guaranteed income: CPP, OAS, and the RRIF / LIF minimums.
4. Top-up withdrawals by strategy, iterated with the couple's tax (the best
   pension-splitting share included) until the two agree within TOL dollars.
5. Surplus saved (TFSA room, then non-registered); shortfall recorded.
6. Growth, then the year's contributions from whoever still works.

Pure: no I/O. Every statutory number comes from :mod:`rules`.
"""
from __future__ import annotations

import numpy as np

from . import rules


# -- returns ----------------------------------------------------------------------

def lognormal_params(mean: float, sd: float) -> tuple[float, float]:
    """(mu, s) of a lognormal gross return with arithmetic ``mean`` and ``sd``."""
    s = float(np.sqrt(np.log(1 + sd ** 2 / (1 + mean) ** 2)))
    return float(np.log(1 + mean) - s ** 2 / 2), s


def draw_returns(mean: float, sd: float, paths: int, years: int, seed: int) -> np.ndarray:
    """(years, paths) real yearly returns, seeded."""
    mu, s = lognormal_params(mean, sd)
    rng = np.random.default_rng(seed)
    return (np.exp(rng.normal(mu, s, (paths, years))) - 1).T


def median_return(mean: float, sd: float) -> float:
    """The typical (median, geometric) yearly return: what a steady 'average future' earns."""
    return float(np.exp(lognormal_params(mean, sd)[0]) - 1)


# -- government benefits ----------------------------------------------------------

def cpp_at_65(person) -> float:
    """Yearly CPP at 65 in today's dollars. Uses the My Service Canada statement
    figure when given; otherwise estimates from contributory years (with the
    general dropout) times an earnings ratio."""
    if person.cpp_at_65 is not None:
        return float(person.cpp_at_65)
    d = rules.CPP["dropout"].value
    counted = d["contributory_years"] - min(d["max_years"], d["share"] * d["contributory_years"])
    years = person.cpp_years + max(0, person.retire_age - person.age)
    return (12 * rules.CPP["max_monthly_at_65"].value * min(1.0, years / counted)
            * person.cpp_earnings_ratio)


def cpp_factor(start_age: int) -> float:
    """Early (-0.6%/month) or late (+0.7%/month) start adjustment."""
    a = rules.CPP["adjustment"].value
    months = (start_age - 65) * 12
    return 1 + (a["late_per_month"] if months > 0 else a["early_per_month"]) * months


def oas_yearly(person, age: int) -> float:
    """OAS paid in the year the person reaches ``age``: 0 before the start age,
    years/40 residence share (none under 10 years), +0.6% per month deferred,
    and the higher 75+ rate."""
    if age < person.oas_start_age:
        return 0.0
    residence = rules.OAS["residence"].value
    years = person.years_in_canada_at_65
    share = 0.0 if years < residence["min_years"] else min(1.0, years / residence["full_years"])
    monthly = rules.OAS["monthly"].value["75+" if age >= 75 else "65-74"]
    deferral = rules.OAS["deferral"].value
    months = (person.oas_start_age - deferral["min_age"]) * 12
    return 12 * monthly * share * (1 + deferral["per_month"] * months)
