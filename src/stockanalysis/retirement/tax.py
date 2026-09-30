"""Canadian personal income tax for one person-year, vectorized over paths.

Inputs are numpy arrays (or scalars) in today's dollars; every rule value comes
from :mod:`rules`. Modelled:
- federal and provincial brackets;
- the basic personal amount (with the federal high-income phase-out);
- the age amount (65+, income-tested) and the pension income amount (65+);
- Alberta's supplemental credit;
- the capital-gains inclusion rate and the OAS recovery tax.

Nothing else (other credits, deductions) is modelled, so tax is overstated a
little: the conservative side.

Pension splitting: ``household_tax`` taxes a couple at a given split share, and
``best_split`` searches shares in 5% steps (0 first, so ties keep no split)
for the lowest combined tax.
"""
from __future__ import annotations

import numpy as np

from . import rules

# Split shares tried: 0 first so ties keep "no split", then -50%..+50% in 5% steps.
SPLIT_STEPS = [0.0] + [round(s, 2) for s in np.linspace(-0.5, 0.5, 21) if abs(s) > 1e-9]


def bracket_tax(income, brackets) -> np.ndarray:
    income = np.asarray(income, dtype=float)
    tax = np.zeros_like(income)
    lower = 0.0
    for upper, rate in brackets:
        tax = tax + rate * np.clip(income - lower, 0.0, upper - lower)
        lower = upper
    return tax


def age_amount(net, age, spec: dict) -> np.ndarray:
    """The age amount for a person ``age`` (65+ only), reduced as net income rises."""
    amount = np.maximum(0.0, spec["amount"] - spec["rate"] * np.maximum(0.0, np.asarray(net) - spec["threshold"]))
    return np.where(np.asarray(age) >= 65, amount, 0.0)


def oas_recovery(net, oas) -> np.ndarray:
    """OAS recovery tax: 15% of net income above the threshold, at most the OAS received."""
    r = rules.OAS["recovery"].value
    return np.minimum(np.asarray(oas, dtype=float),
                      r["rate"] * np.maximum(0.0, np.asarray(net, dtype=float) - r["threshold"]))


def income_tax(*, ordinary=0.0, pension=0.0, gains=0.0, oas=0.0, age=0, province="AB") -> np.ndarray:
    """Federal + provincial income tax plus the OAS recovery tax for one person.

    ``ordinary``: taxable income that is not eligible pension income (CPP, plain
    RRSP withdrawals, anything under 65). ``pension``: eligible pension income
    (RRIF/LIF payments at 65+, after any split). ``gains``: realized capital
    gains (the inclusion rate is applied here). ``oas``: OAS received.
    """
    ordinary, pension, gains, oas = (np.asarray(x, dtype=float) for x in (ordinary, pension, gains, oas))
    gross = ordinary + pension + oas + rules.CAPITAL_GAINS_INCLUSION.value * gains
    recovery = oas_recovery(gross, oas)
    net = gross - recovery           # line 23600: after deducting the OAS repayment
    over_65 = np.asarray(age) >= 65

    fed = rules.FEDERAL
    bpa = fed["bpa"].value
    phase = np.clip((net - bpa["phase_start"]) / (bpa["phase_end"] - bpa["phase_start"]), 0.0, 1.0)
    fed_bpa = bpa["max"] - (bpa["max"] - bpa["min"]) * phase
    fed_pension = np.where(over_65, np.minimum(pension, fed["pension_amount"].value), 0.0)
    fed_credits = fed["credit_rate"].value * (fed_bpa + age_amount(net, age, fed["age_amount"].value)
                                              + fed_pension)
    federal = np.maximum(0.0, bracket_tax(net, fed["brackets"].value) - fed_credits)

    prov = rules.PROVINCIAL[province]
    prov_pension = np.where(over_65, np.minimum(pension, prov["pension_amount"].value), 0.0)
    prov_credits = prov["credit_rate"].value * (prov["bpa"].value
                                                + age_amount(net, age, prov["age_amount"].value)
                                                + prov_pension)
    supplemental = prov.get("supplemental_credit")
    if supplemental is not None:
        s = supplemental.value
        prov_credits = prov_credits + s["rate"] * np.maximum(0.0, prov_credits - s["floor"])
    provincial = np.maximum(0.0, bracket_tax(net, prov["brackets"].value) - prov_credits)

    return federal + provincial + recovery


def _split(a: dict, b: dict, share):
    """Eligible pension income of A and B after moving ``share`` (A->B if positive)."""
    share = np.asarray(share, dtype=float)
    min_age = rules.PENSION_SPLIT.value["min_age"]
    from_a = np.where((share > 0) & (np.asarray(a["age"]) >= min_age), share * a["pension"], 0.0)
    from_b = np.where((share < 0) & (np.asarray(b["age"]) >= min_age), -share * b["pension"], 0.0)
    return a["pension"] - from_a + from_b, b["pension"] + from_a - from_b


def _person(part: dict, pension, province: str) -> np.ndarray:
    return income_tax(ordinary=part["ordinary"], pension=pension, gains=part["gains"],
                      oas=part["oas"], age=part["age"], province=province)


def household_tax(parts: list, share, province: str = "AB") -> np.ndarray:
    """Tax for a one- or two-person household at pension-split ``share``."""
    if len(parts) == 1:
        return _person(parts[0], parts[0]["pension"], province)
    a, b = parts
    pa, pb = _split(a, b, share)
    return _person(a, pa, province) + _person(b, pb, province)


def best_split(parts: list, province: str = "AB") -> np.ndarray:
    """The split share (per path) with the lowest combined tax; ties keep 0."""
    best_total = best_share = None
    for s in SPLIT_STEPS:
        total = household_tax(parts, s, province)
        if best_total is None:
            best_total, best_share = total, np.full(np.shape(total), s)
            continue
        better = total < best_total - 1e-6
        best_total = np.where(better, total, best_total)
        best_share = np.where(better, s, best_share)
    return best_share


def couple_tax(a: dict, b: dict, province: str = "AB"):
    """(tax_a, tax_b, share) at the tax-minimising pension split."""
    share = best_split([a, b], province)
    pa, pb = _split(a, b, share)
    return _person(a, pa, province), _person(b, pb, province), share
