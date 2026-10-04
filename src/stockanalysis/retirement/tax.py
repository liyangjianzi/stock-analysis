"""Canadian personal income tax for one person-year, vectorized over paths.

Inputs are numpy arrays (or scalars) in today's dollars; every rule value comes
from :mod:`rules`. Modelled:
- federal and provincial brackets;
- the basic personal amount (with the federal high-income phase-out);
- the age amount (65+, income-tested) and the pension income amount (65+);
- Alberta's supplemental credit;
- the capital-gains inclusion rate and the OAS recovery tax;
- eligible Canadian dividends: the gross-up into income and the federal and
  provincial dividend tax credits.

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


def total_income(*, ordinary=0.0, pension=0.0, gains=0.0, oas=0.0, dividends=0.0) -> np.ndarray:
    """Total income (line 15000): eligible dividends grossed up, the taxable share of
    capital gains. What income-tested benefits (the student grant) look at."""
    return (np.asarray(ordinary, dtype=float) + pension + oas
            + np.asarray(dividends, dtype=float) * (1 + rules.DIVIDENDS["eligible_gross_up"].value)
            + rules.CAPITAL_GAINS_INCLUSION.value * np.asarray(gains, dtype=float))


def extra_tax(base, *, ordinary=0.0, dividends=0.0, age=0, province="AB") -> np.ndarray:
    """Tax on income added on top of ``base`` ordinary income (e.g. a salary)."""
    return (income_tax(ordinary=base + np.asarray(ordinary, dtype=float), dividends=dividends,
                       age=age, province=province)
            - income_tax(ordinary=base, age=age, province=province))


def payroll_premiums(salary) -> np.ndarray:
    """An employee's yearly CPP, CPP2 and EI premiums on ``salary`` (outside Quebec)."""
    s = np.asarray(salary, dtype=float)
    cpp, cpp2, ei = (rules.PAYROLL[k].value for k in ("cpp", "cpp2", "ei"))
    base = cpp["rate"] * np.clip(s - cpp["exemption"], 0.0, cpp["ympe"] - cpp["exemption"])
    second = cpp2["rate"] * np.clip(s - cpp["ympe"], 0.0, cpp2["yampe"] - cpp["ympe"])
    return base + second + ei["rate"] * np.clip(s, 0.0, ei["max_insurable"])


def child_benefit(income, under_6: int, six_to_17: int) -> np.ndarray:
    """Yearly Canada Child Benefit for the children's count on family net ``income``."""
    c = rules.CCB.value
    n = under_6 + six_to_17
    if n == 0:
        return np.zeros(np.shape(income))
    rate1, base2, rate2 = c["reduction"][min(n, 4)]
    inc = np.asarray(income, dtype=float)
    over1 = np.clip(inc - c["threshold_1"], 0.0, c["threshold_2"] - c["threshold_1"])
    over2 = np.maximum(inc - c["threshold_2"], 0.0)
    cut = np.where(inc > c["threshold_2"], base2 + rate2 * over2, rate1 * over1)
    full = under_6 * c["under_6"] + six_to_17 * c["6_to_17"]
    return np.maximum(full - np.nan_to_num(cut, posinf=np.inf), 0.0)


def childcare_deduction(expenses, kid_ages, earned) -> np.ndarray:
    """Line 21400: ``expenses`` capped per child under 16 and at two-thirds of the
    claimant's ``earned`` income."""
    c = rules.CHILDCARE.value
    limit = sum(c["under_7"] if a < 7 else c["7_to_15"] for a in kid_ages if a < 16)
    return np.minimum(np.minimum(expenses, limit), c["earned_share"] * np.asarray(earned, dtype=float))


def rrsp_new_room(salary, pension_adjustment) -> np.ndarray:
    """A year's new RRSP room: 18% of the salary up to the dollar limit, less the
    pension adjustment (all pension contributions, the employer's included)."""
    r = rules.RRSP_LIMIT.value
    room = np.minimum(r["rate"] * np.asarray(salary, dtype=float), r["dollar_limit"])
    return np.maximum(room - pension_adjustment, 0.0)


def income_tax(*, ordinary=0.0, pension=0.0, gains=0.0, oas=0.0, dividends=0.0, age=0,
               province="AB") -> np.ndarray:
    """Federal + provincial income tax plus the OAS recovery tax for one person.

    ``ordinary``: taxable income that is not eligible pension income (CPP, plain
    RRSP withdrawals, salary, interest, foreign dividends, anything under 65).
    ``pension``: eligible pension income (RRIF/LIF payments at 65+, after any
    split). ``gains``: realized capital gains (the inclusion rate is applied here).
    ``oas``: OAS received. ``dividends``: eligible Canadian dividends *received*;
    the grossed-up amount counts as income (so it also raises the OAS recovery and
    trims the age amount) and earns the dividend tax credits.
    """
    ordinary, pension, gains, oas, dividends = (np.asarray(x, dtype=float)
                                                for x in (ordinary, pension, gains, oas, dividends))
    grossed_up = dividends * (1 + rules.DIVIDENDS["eligible_gross_up"].value)
    gross = total_income(ordinary=ordinary, pension=pension, gains=gains, oas=oas, dividends=dividends)
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
    fed_credits = fed_credits + rules.DIVIDENDS["federal_credit"].value * grossed_up
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
    prov_credits = prov_credits + prov["dividend_credit"].value * grossed_up
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
                      oas=part["oas"], dividends=part.get("dividends", 0.0), age=part["age"],
                      province=province)


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
