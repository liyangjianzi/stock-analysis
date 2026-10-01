"""Canadian retirement rules: the ONLY place statutory values live.

Every value is a :class:`Rule` recording the tax or benefit year it applies to
and the official page it was read from. Projections hold these flat in today's
dollars (brackets, credits and benefits are indexed to inflation), so the model
never grows them.

Update this file every January: CRA publishes the indexed amounts in the T4127
payroll formulas and the TD1 forms, and Service Canada updates OAS every
quarter. ``tests/test_retirement_rules.py`` fails if an entry loses its citation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TAX_YEAR = 2026
INF = math.inf


@dataclass(frozen=True)
class Rule:
    value: object
    year: int
    source: str


_T4127 = ("https://www.canada.ca/en/revenue-agency/services/forms-publications/payroll/"
          "t4127-payroll-deductions-formulas/t4127-jan/"
          "t4127-jan-payroll-deductions-formulas-computer-programs.html")
_TD1 = "https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/td1/td1-26e.pdf"
_TD1AB = "https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/td1ab/td1ab-26e.pdf"
_PENSION_AMOUNT = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
                   "about-your-tax-return/tax-return/completing-a-tax-return/"
                   "deductions-credits-expenses/line-31400-pension-income-amount.html")
_SPLIT = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
          "pension-income-splitting.html")
_CAP_GAINS = ("https://www.pm.gc.ca/en/news/news-releases/2025/03/21/"
              "prime-minister-mark-carney-cancels-proposed-capital-gains-tax-increase")
_OAS_AMOUNTS = "https://www.canada.ca/en/services/benefits/publicpensions/old-age-security/payments.html"
_OAS_RECOVERY = ("https://www.canada.ca/en/services/benefits/publicpensions/old-age-security/"
                 "recovery-tax.html")
_OAS_START = "https://www.canada.ca/en/services/benefits/publicpensions/old-age-security/when-start.html"
_CPP_AMOUNT = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-benefit/amount.html"
_CPP_START = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/when-start.html"
_CPP_HOW_MUCH = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/amount.html"
_RRIF_FACTORS = ("https://www.canada.ca/en/revenue-agency/services/tax/businesses/topics/"
                 "completing-slips-summaries/t4rsp-t4rif-information-returns/payments/"
                 "chart-prescribed-factors.html")
_RRSP_71 = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
            "rrsps-related-plans/rrsp-options-when-you-turn-71.html")
_TFSA = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
         "tax-free-savings-account/contributing/calculate-room.html")
_AB_LIF_TABLE = ("https://open.alberta.ca/dataset/f0c27086-6b12-4aa3-881a-b895498221fa/resource/"
                 "613dd0f1-130b-41fb-a271-4b03b0750cf4/download/"
                 "tbf-superintendent-of-pensions-interest-rate-tables-2026.pdf")
_AB_LIF_GUIDE = ("https://open.alberta.ca/dataset/623fa691-3296-4bf4-ae01-ebd3cd657f99/resource/"
                 "f3497e09-0666-4975-851a-2d1c8c716637/download/ig-18-life-income-funds-lifs.pdf")
_AB_UNLOCK = "https://www.alberta.ca/pensions-individuals"

FEDERAL = {
    "brackets": Rule(((58_523, 0.14), (117_045, 0.205), (181_440, 0.26),
                      (258_482, 0.29), (INF, 0.33)), 2026, _T4127),
    "credit_rate": Rule(0.14, 2026, _T4127),
    # Basic personal amount: the max, falling to the min across the phase range.
    "bpa": Rule({"max": 16_452, "min": 14_829,
                 "phase_start": 181_440, "phase_end": 258_482}, 2026, _T4127),
    # 65+; reduced by 15% of net income above the threshold (zero at $107,819).
    "age_amount": Rule({"amount": 9_208, "threshold": 46_432, "rate": 0.15}, 2026, _TD1),
    "pension_amount": Rule(2_000, 2026, _PENSION_AMOUNT),
}

PROVINCIAL = {
    "AB": {
        "brackets": Rule(((61_200, 0.08), (154_259, 0.10), (185_111, 0.12),
                          (246_813, 0.13), (370_220, 0.14), (INF, 0.15)), 2026, _T4127),
        "credit_rate": Rule(0.08, 2026, _T4127),
        "bpa": Rule(22_769, 2026, _T4127),
        # 65+; reduced by 15% of net income above the threshold (zero at $89,534).
        "age_amount": Rule({"amount": 6_345, "threshold": 47_234, "rate": 0.15}, 2026, _TD1AB),
        "pension_amount": Rule(1_753, 2026, _TD1AB),
        # Alberta supplemental credit (T4127 K5P): 25% of credits above $4,896.
        "supplemental_credit": Rule({"floor": 4_896.00, "rate": 0.25}, 2026, _T4127),
    },
}

# The proposed two-thirds rate was cancelled on 2025-03-21; one half stays.
CAPITAL_GAINS_INCLUSION = Rule(0.5, 2025, _CAP_GAINS)

# Up to 50% of eligible pension income (RRIF/LIF payments when the transferor
# is 65+, NOT plain RRSP withdrawals) can be allocated to a spouse.
PENSION_SPLIT = Rule({"max_share": 0.5, "min_age": 65}, 2026, _SPLIT)

OAS = {
    # October-December 2026 maximum monthly payments.
    "monthly": Rule({"65-74": 762.50, "75+": 838.75}, 2026, _OAS_AMOUNTS),
    "recovery": Rule({"threshold": 95_323, "rate": 0.15}, 2026, _OAS_RECOVERY),
    "deferral": Rule({"per_month": 0.006, "min_age": 65, "max_age": 70}, 2026, _OAS_START),
    "residence": Rule({"full_years": 40, "min_years": 10}, 2026, _OAS_AMOUNTS),
}

CPP = {
    "max_monthly_at_65": Rule(1_507.65, 2026, _CPP_AMOUNT),
    "adjustment": Rule({"early_per_month": 0.006, "late_per_month": 0.007,
                        "min_age": 60, "max_age": 70}, 2026, _CPP_START),
    # General drop-out: up to 17% of the contributory period (max 8 years);
    # the period runs from 18 to a 65 start = 47 years.
    "dropout": Rule({"share": 0.17, "max_years": 8, "contributory_years": 47}, 2026, _CPP_HOW_MUCH),
}

RRIF = {
    "convert_by_age": Rule(71, 2026, _RRSP_71),
    # Prescribed factors on the January 1 value; 1/(90 - age) below 71.
    "factors": Rule({71: 0.0528, 72: 0.0540, 73: 0.0553, 74: 0.0567, 75: 0.0582,
                     76: 0.0598, 77: 0.0617, 78: 0.0636, 79: 0.0658, 80: 0.0682,
                     81: 0.0708, 82: 0.0738, 83: 0.0771, 84: 0.0808, 85: 0.0851,
                     86: 0.0899, 87: 0.0955, 88: 0.1021, 89: 0.1099, 90: 0.1192,
                     91: 0.1306, 92: 0.1449, 93: 0.1634, 94: 0.1879, 95: 0.2000},
                    2026, _RRIF_FACTORS),
}

TFSA = {
    # Withdrawals come back as room on January 1 of the following year.
    "annual_limit": Rule(7_000, 2026, _TFSA),
}

LIF = {
    "AB": {
        "min_age": Rule(50, 2016, _AB_LIF_GUIDE),
        # Alberta maximum % of the January 1 balance (Table A); the yearly max is
        # the greater of this and last year's investment return in the LIF.
        "max_pct": Rule({50: 0.0627, 51: 0.0631, 52: 0.0635, 53: 0.0640, 54: 0.0645,
                         55: 0.0651, 56: 0.0657, 57: 0.0663, 58: 0.0670, 59: 0.0677,
                         60: 0.0685, 61: 0.0694, 62: 0.0704, 63: 0.0714, 64: 0.0726,
                         65: 0.0738, 66: 0.0752, 67: 0.0767, 68: 0.0783, 69: 0.0802,
                         70: 0.0822, 71: 0.0845, 72: 0.0871, 73: 0.0900, 74: 0.0934,
                         75: 0.0971, 76: 0.1015, 77: 0.1066, 78: 0.1125, 79: 0.1196,
                         80: 0.1282, 81: 0.1387, 82: 0.1519, 83: 0.1690, 84: 0.1919,
                         85: 0.2240, 86: 0.2723, 87: 0.3529, 88: 0.5146, 89: 1.0000},
                        2026, _AB_LIF_TABLE),
        # One-time unlocking of up to 50% when a LIF starts, age 50+.
        "unlock_share": Rule(0.5, 2026, _AB_UNLOCK),
    },
}


def rrif_min_factor(age_jan1: int) -> float:
    """RRIF (and LIF) minimum as a share of the January 1 balance."""
    if age_jan1 < 71:
        return 1.0 / (90 - age_jan1)
    return RRIF["factors"].value[min(age_jan1, 95)]


def lif_max_pct(province: str, age_jan1: int) -> float:
    """The province's LIF maximum % for ``age_jan1``, clamped to the table's range."""
    table = LIF[province]["max_pct"].value
    return table[min(max(age_jan1, min(table)), max(table))]


def top_marginal_rate(province: str) -> float:
    """Combined top rate, used for tax on registered money at the second death."""
    return FEDERAL["brackets"].value[-1][1] + PROVINCIAL[province]["brackets"].value[-1][1]


def all_rules() -> list[tuple[str, Rule]]:
    """Every Rule with a dotted name, e.g. ``("provincial.AB.bpa", Rule(...))``."""
    found: list[tuple[str, Rule]] = []

    def walk(prefix: str, obj) -> None:
        if isinstance(obj, Rule):
            found.append((prefix, obj))
        elif isinstance(obj, dict):
            for key, value in obj.items():
                walk(f"{prefix}.{key}", value)

    for name in ("FEDERAL", "PROVINCIAL", "CAPITAL_GAINS_INCLUSION", "PENSION_SPLIT",
                 "OAS", "CPP", "RRIF", "TFSA", "LIF"):
        walk(name.lower(), globals()[name])
    return found


def is_stale(year: int) -> bool:
    """True once the calendar has moved past the rules' tax year."""
    return TAX_YEAR < year
