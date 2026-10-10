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
_CPP_SURVIVOR = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-survivor-pension.html"
_CPP_MAXIMUMS = ("https://www.canada.ca/en/employment-social-development/programs/pensions/pension/"
                 "statistics/2026-quarterly-july-september.html")
_CPP_DEATH = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-death-benefit.html"
_PAYROLL = "https://www.canada.ca/en/revenue-agency/services/tax/businesses/topics/payroll/"
_CPP_PREMIUMS = (_PAYROLL + "payroll-deductions-contributions/canada-pension-plan-cpp/"
                 "cpp-contribution-rates-maximums-exemptions.html")
_CPP2_PREMIUMS = (_PAYROLL + "calculating-deductions/making-deductions/"
                  "second-additional-cpp-contribution-rates-maximums.html")
_EI_PREMIUMS = (_PAYROLL + "payroll-deductions-contributions/employment-insurance-ei/"
                "ei-premium-rates-maximums.html")
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
_DIVIDENDS_FED = "https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5000-d1/5000-d1-25e.pdf"
_CESG = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
         "registered-education-savings-plans-resps/canada-education-savings-programs-cesp/"
         "canada-education-savings-grant-cesg.html")
_RESP_LIMIT = ("https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/"
               "registered-education-savings-plans-resps/resp-contributions.html")
_RESP_AIP = "https://www.canada.ca/en/services/benefits/education/education-savings/managing-plan.html"
_STUDENT_GRANT = "https://www.canada.ca/en/services/benefits/education/student-aid/grants-loans/full-time.html"
_DIVIDENDS_AB = "https://www.canada.ca/content/dam/cra-arc/formspubs/pbg/5009-d/5009-d-25e.pdf"

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
    # Canada employment amount (T4127 table 8.2): a credit on employment income.
    "employment_amount": Rule(1_501, 2026, _T4127),
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
        # Worksheet AB428 line 61520: 8.12% of the taxable (grossed-up) eligible dividends.
        "dividend_credit": Rule(0.0812, 2025, _DIVIDENDS_AB),
    },
}

# Eligible dividends from Canadian corporations (Federal Worksheet 5000-D1): the
# taxable amount is 138% of the dividend received; the federal credit (line 40425)
# is 15.0198% of that taxable amount. Foreign dividends get neither.
DIVIDENDS = {
    "eligible_gross_up": Rule(0.38, 2025, _DIVIDENDS_FED),
    "federal_credit": Rule(0.150198, 2025, _DIVIDENDS_FED),
}

# The proposed two-thirds rate was cancelled on 2025-03-21; one half stays.
CAPITAL_GAINS_INCLUSION = Rule(0.5, 2025, _CAP_GAINS)

RESP = {
    # Basic CESG: 20% of contributions, up to $500 a child a year ($1,000 with unused
    # room from earlier years), $7,200 lifetime, through the year the child turns 17.
    # Room accrues $500 a year from birth (from 2007 on).
    "cesg": Rule({"rate": 0.20, "yearly_max": 500, "yearly_max_catch_up": 1_000,
                  "lifetime_max": 7_200, "last_age": 17, "room_from_year": 2007}, 2026, _CESG),
    "contribution_lifetime_max": Rule(50_000, 2026, _RESP_LIMIT),
    # Leftover growth (an accumulated income payment) is taxed as income plus 20%,
    # unless up to $50,000 goes to the subscriber's RRSP (room needed).
    "aip": Rule({"extra_tax": 0.20, "rrsp_transfer_max": 50_000}, 2026, _RESP_AIP),
}

# Canada Student Grant for Full-Time Students: up to $4,200 a school year (the rate
# announced to the end of 2026-27; held flat like every other rule). Full grant
# below the first threshold of gross family income, none at the cut-off, by
# family size (7 = 7 or more); thresholds effective August 1, 2026.
STUDENT_GRANT = Rule({
    "yearly_max": 4_200,
    "thresholds": {1: (38_474, 69_987), 2: (54_412, 98_017), 3: (66_641, 117_317),
                   4: (76_952, 129_769), 5: (86_033, 141_180), 6: (94_245, 151_937),
                   7: (101_797, 161_321)},
}, 2026, _STUDENT_GRANT)

_CCB = ("https://www.canada.ca/en/revenue-agency/services/child-family-benefits/"
        "canada-child-benefit-overview/canada-child-benefit-we-calculate-your-ccb.html")
_RRSP_LIMITS = ("https://www.canada.ca/en/revenue-agency/services/tax/registered-plans-administrators/"
                "pspa/mp-rrsp-dpsp-tfsa-limits-ympe.html")
_CHILDCARE = ("https://www.canada.ca/en/revenue-agency/services/tax/technical-information/income-tax/"
              "income-tax-folios-index/series-1-individuals/folio-3-family-unit-issues/"
              "income-tax-folio-s1-f3-c1-child-care-expense-deduction.html")

# Canada Child Benefit, July 2026 - June 2027 (on 2025 family net income). Reduction
# per number of children (4 = 4 or more): (rate in the first band, the amount at
# the second threshold, rate above it).
CCB = Rule({"under_6": 8_157, "6_to_17": 6_883, "threshold_1": 38_237, "threshold_2": 82_847,
            "reduction": {1: (0.07, 3_123, 0.032), 2: (0.135, 6_022, 0.057),
                          3: (0.19, 8_476, 0.08), 4: (0.23, 10_260, 0.095)}}, 2026, _CCB)

# RRSP room: 18% of last year's earned income up to the dollar limit, less the
# pension adjustment.
RRSP_LIMIT = Rule({"rate": 0.18, "dollar_limit": 33_810}, 2026, _RRSP_LIMITS)

# Child care expense deduction (line 21400): per child, claimed by the lower-income
# spouse, capped at two-thirds of their earned income. Unchanged since 2015 (the folio
# gives them for "2015 and subsequent tax years"), so they apply to 2026.
CHILDCARE = Rule({"under_7": 8_000, "7_to_15": 5_000, "earned_share": 2 / 3}, 2026, _CHILDCARE)

# Employee payroll premiums on a salary (outside Quebec). Not income tax: CPP
# premiums buy the CPP pension.
PAYROLL = {
    # Of the 5.95%, the base 4.95% earns a credit at the lowest rate and the first
    # additional 1.00% is deducted from income (T4127 K2 / F5); CPP2 is all deducted.
    "cpp": Rule({"rate": 0.0595, "base_rate": 0.0495, "ympe": 74_600, "exemption": 3_500},
                2026, _CPP_PREMIUMS),
    "cpp2": Rule({"rate": 0.04, "yampe": 85_000}, 2026, _CPP2_PREMIUMS),
    "ei": Rule({"rate": 0.0163, "max_insurable": 68_900}, 2026, _EI_PREMIUMS),
}

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
    # Survivor's pension: 60% of the contributor's pension at 65+, or a flat rate plus
    # 37.5% under 65; with the survivor's own retirement pension the two together are
    # capped. Shares from the survivor page, 2026 amounts from the ESDC maximums table.
    "survivor": Rule({"share_65": 0.60, "share_under_65": 0.375, "flat_monthly": 238.17,
                      "combined_max_monthly": 1_531.56}, 2026, _CPP_MAXIMUMS),
    "survivor_rules": Rule("60% at 65+; flat rate + 37.5% under 65; combined maximum with "
                           "your own retirement pension", 2026, _CPP_SURVIVOR),
    "death_benefit": Rule(2_500.0, 2026, _CPP_DEATH),
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


# Canada's all-items CPI, yearly change of the annual average (Statistics Canada
# table 18-10-0004-01, vector v41690973, monthly, averaged by calendar year).
# From 1928 so the inflation and return draws reach the 1930s and 1970s; the default
# rate is still the last 40 years (historical_inflation).
_CPI = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810000401"
CPI = Rule({
                  1928: 0.00735, 1929: 0.01004, 1930: -0.00994, 1931: -0.09854, 1932: -0.09413, 1933: -0.04358,
                  1934: 0.01168, 1935: 0.00462, 1936: 0.02069, 1937: 0.03604, 1938: 0.00543, 1939: -0.00757,
                  1940: 0.04248, 1941: 0.05956, 1942: 0.04635, 1943: 0.02168, 1944: 0.00646, 1945: 0.00642,
                  1946: 0.03097, 1947: 0.09011, 1948: 0.14263, 1949: 0.03475,
                  1950: 0.02673, 1951: 0.10547, 1952: 0.02597, 1953: -0.01001, 1954: 0.00654, 1955: 0.00177,
                  1956: 0.01356, 1957: 0.03316, 1958: 0.02365, 1959: 0.01210, 1960: 0.01359, 1961: 0.01019,
                  1962: 0.01062, 1963: 0.01628, 1964: 0.01912, 1965: 0.02333, 1966: 0.03816, 1967: 0.03580,
                  1968: 0.04055, 1969: 0.04562, 1970: 0.03346, 1971: 0.02705, 1972: 0.04988, 1973: 0.07488,
                  1974: 0.10997, 1975: 0.10672, 1976: 0.07542, 1977: 0.07976, 1978: 0.08974, 1979: 0.09145,
                  1980: 0.10129, 1981: 0.12472, 1982: 0.10769, 1983: 0.05864, 1984: 0.04305, 1985: 0.03962,
                  1986: 0.04195, 1987: 0.04356, 1988: 0.04028, 1989: 0.04984, 1990: 0.04780, 1991: 0.05626,
                  1992: 0.01490, 1993: 0.01865, 1994: 0.00166, 1995: 0.02149, 1996: 0.01571, 1997: 0.01621,
                  1998: 0.00996, 1999: 0.01735, 2000: 0.02719, 2001: 0.02525, 2002: 0.02258, 2003: 0.02759,
                  2004: 0.01857, 2005: 0.02214, 2006: 0.02002, 2007: 0.02138, 2008: 0.02370, 2009: 0.00299,
                  2010: 0.01777, 2011: 0.02912, 2012: 0.01516, 2013: 0.00938, 2014: 0.01907, 2015: 0.01125,
                  2016: 0.01429, 2017: 0.01597, 2018: 0.02268, 2019: 0.01949, 2020: 0.00717, 2021: 0.03395,
                  2022: 0.06803, 2023: 0.03879, 2024: 0.02382, 2025: 0.02072,
}, 2025, _CPI)


def historical_inflation(years: int = 40) -> float:
    """Average yearly inflation over the last ``years`` years of :data:`CPI`
    (geometric: what a dollar actually lost)."""
    changes = [CPI.value[y] for y in sorted(CPI.value)[-years:]]
    growth = 1.0
    for c in changes:
        growth *= 1 + c
    return growth ** (1 / len(changes)) - 1



_DAMODARAN = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histretSP.html"
_BLS_CPI = "https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems"   # CUUR0000SA0, M13

# US nominal yearly returns (stocks = S&P 500 with dividends, bonds = 10-year Treasury),
# Aswath Damodaran, "Historical Returns on Stocks, Bonds and Bills". The planner turns
# them real with US_CPI and pairs each year with Canada's CPI (engine.history_real).
US_RETURNS = Rule({
    1928: (0.4381, 0.0084), 1929: (-0.0830, 0.0420), 1930: (-0.2512, 0.0454), 1931: (-0.4384, -0.0256),
    1932: (-0.0864, 0.0879), 1933: (0.4998, 0.0186), 1934: (-0.0119, 0.0796), 1935: (0.4674, 0.0447),
    1936: (0.3194, 0.0502), 1937: (-0.3534, 0.0138), 1938: (0.2928, 0.0421), 1939: (-0.0110, 0.0441),
    1940: (-0.1067, 0.0540), 1941: (-0.1277, -0.0202), 1942: (0.1917, 0.0229), 1943: (0.2506, 0.0249),
    1944: (0.1903, 0.0258), 1945: (0.3582, 0.0380), 1946: (-0.0843, 0.0313), 1947: (0.0520, 0.0092),
    1948: (0.0570, 0.0195), 1949: (0.1830, 0.0466), 1950: (0.3081, 0.0043), 1951: (0.2368, -0.0030),
    1952: (0.1815, 0.0227), 1953: (-0.0121, 0.0414), 1954: (0.5256, 0.0329), 1955: (0.3260, -0.0134),
    1956: (0.0744, -0.0226), 1957: (-0.1046, 0.0680), 1958: (0.4372, -0.0210), 1959: (0.1206, -0.0265),
    1960: (0.0034, 0.1164), 1961: (0.2664, 0.0206), 1962: (-0.0881, 0.0569), 1963: (0.2261, 0.0168),
    1964: (0.1642, 0.0373), 1965: (0.1240, 0.0072), 1966: (-0.0997, 0.0291), 1967: (0.2380, -0.0158),
    1968: (0.1081, 0.0327), 1969: (-0.0824, -0.0501), 1970: (0.0356, 0.1675), 1971: (0.1422, 0.0979),
    1972: (0.1876, 0.0282), 1973: (-0.1431, 0.0366), 1974: (-0.2590, 0.0199), 1975: (0.3700, 0.0361),
    1976: (0.2383, 0.1598), 1977: (-0.0698, 0.0129), 1978: (0.0651, -0.0078), 1979: (0.1852, 0.0067),
    1980: (0.3174, -0.0299), 1981: (-0.0470, 0.0820), 1982: (0.2042, 0.3281), 1983: (0.2234, 0.0320),
    1984: (0.0615, 0.1373), 1985: (0.3124, 0.2571), 1986: (0.1849, 0.2428), 1987: (0.0581, -0.0496),
    1988: (0.1654, 0.0822), 1989: (0.3148, 0.1769), 1990: (-0.0306, 0.0624), 1991: (0.3023, 0.1500),
    1992: (0.0749, 0.0936), 1993: (0.0997, 0.1421), 1994: (0.0133, -0.0804), 1995: (0.3720, 0.2348),
    1996: (0.2268, 0.0143), 1997: (0.3310, 0.0994), 1998: (0.2834, 0.1492), 1999: (0.2089, -0.0825),
    2000: (-0.0903, 0.1666), 2001: (-0.1185, 0.0557), 2002: (-0.2197, 0.1512), 2003: (0.2836, 0.0038),
    2004: (0.1074, 0.0449), 2005: (0.0483, 0.0287), 2006: (0.1561, 0.0196), 2007: (0.0548, 0.1021),
    2008: (-0.3655, 0.2010), 2009: (0.2594, -0.1112), 2010: (0.1482, 0.0846), 2011: (0.0210, 0.1604),
    2012: (0.1589, 0.0297), 2013: (0.3215, -0.0910), 2014: (0.1352, 0.1075), 2015: (0.0138, 0.0128),
    2016: (0.1177, 0.0069), 2017: (0.2161, 0.0280), 2018: (-0.0423, -0.0002), 2019: (0.3121, 0.0964),
    2020: (0.1802, 0.1133), 2021: (0.2847, -0.0442), 2022: (-0.1804, -0.1783), 2023: (0.2606, 0.0388),
    2024: (0.2488, -0.0164), 2025: (0.1778, 0.0780),
}, 2025, _DAMODARAN)

# US CPI-U, all items, U.S. city average: yearly change of the annual average (BLS).
US_CPI = Rule({
    1928: -0.01724, 1929: 0.00000, 1930: -0.02339, 1931: -0.08982, 1932: -0.09868, 1933: -0.05109,
    1934: 0.03077, 1935: 0.02239, 1936: 0.01460, 1937: 0.03597, 1938: -0.02083, 1939: -0.01418,
    1940: 0.00719, 1941: 0.05000, 1942: 0.10884, 1943: 0.06135, 1944: 0.01734, 1945: 0.02273,
    1946: 0.08333, 1947: 0.14359, 1948: 0.08072, 1949: -0.01245, 1950: 0.01261, 1951: 0.07884,
    1952: 0.01923, 1953: 0.00755, 1954: 0.00749, 1955: -0.00372, 1956: 0.01493, 1957: 0.03309,
    1958: 0.02847, 1959: 0.00692, 1960: 0.01718, 1961: 0.01014, 1962: 0.01003, 1963: 0.01325,
    1964: 0.01307, 1965: 0.01613, 1966: 0.02857, 1967: 0.03086, 1968: 0.04192, 1969: 0.05460,
    1970: 0.05722, 1971: 0.04381, 1972: 0.03210, 1973: 0.06220, 1974: 0.11036, 1975: 0.09128,
    1976: 0.05762, 1977: 0.06503, 1978: 0.07591, 1979: 0.11350, 1980: 0.13499, 1981: 0.10316,
    1982: 0.06161, 1983: 0.03212, 1984: 0.04317, 1985: 0.03561, 1986: 0.01859, 1987: 0.03650,
    1988: 0.04137, 1989: 0.04818, 1990: 0.05403, 1991: 0.04208, 1992: 0.03010, 1993: 0.02994,
    1994: 0.02561, 1995: 0.02834, 1996: 0.02953, 1997: 0.02294, 1998: 0.01558, 1999: 0.02209,
    2000: 0.03361, 2001: 0.02846, 2002: 0.01581, 2003: 0.02279, 2004: 0.02663, 2005: 0.03388,
    2006: 0.03226, 2007: 0.02848, 2008: 0.03840, 2009: -0.00356, 2010: 0.01640, 2011: 0.03157,
    2012: 0.02069, 2013: 0.01465, 2014: 0.01622, 2015: 0.00119, 2016: 0.01262, 2017: 0.02130,
    2018: 0.02442, 2019: 0.01812, 2020: 0.01234, 2021: 0.04698, 2022: 0.08003, 2023: 0.04116,
    2024: 0.02949, 2025: 0.02631,
}, 2025, _BLS_CPI)

_FP_CANADA = ("https://www.fpcanada.ca/docs/professionalsitelibraries/standards/"
              "projection-assumption-guidelines.pdf")

# FP Canada / Institute of Financial Planning Projection Assumption Guidelines: nominal,
# geometric (compound) returns before fees, and their inflation. The planner's default
# expected real returns (inputs.Returns.stocks / bonds).
RETURN_ASSUMPTIONS = Rule({"inflation": 0.021, "fixed_income": 0.032, "canadian_equities": 0.063},
                          2026, _FP_CANADA)


def real_return(kind: str) -> float:
    """A RETURN_ASSUMPTIONS return made real with the guidelines' own inflation."""
    a = RETURN_ASSUMPTIONS.value
    return (1 + a[kind]) / (1 + a["inflation"]) - 1

_CALGARY_TAX = ("https://www.calgary.ca/cfod/finance/property-tax/tax-bill-and-tax-rate-calculation/"
                "historical-tax-rates.html")

# Yearly growth above Canada's CPI for costs that outpace it (today's dollars).
# StatCan CPI 18-10-0004-01 components vs all-items; the property tax is the City
# of Calgary's typical single-detached bill, 2023-2026 (+8.8%/yr nominal).
COST_GROWTH = {
    "care": Rule(0.011, 2025, _CPI),           # health care services, Canada, 1985-2025
    "education": Rule(0.004, 2025, _CPI),      # tuition fees, Canada, 2005-2025
    "property_tax": Rule(0.065, 2026, _CALGARY_TAX),
    "insurance": Rule(0.062, 2025, _CPI),      # homeowners' home and mortgage insurance, Alberta, 2005-2025
}


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

    for name in ("FEDERAL", "PROVINCIAL", "CAPITAL_GAINS_INCLUSION", "DIVIDENDS", "RESP", "STUDENT_GRANT", "PAYROLL", "CCB", "RRSP_LIMIT", "CHILDCARE", "CPI", "US_RETURNS", "US_CPI", "RETURN_ASSUMPTIONS",
                 "COST_GROWTH", "PENSION_SPLIT",
                 "OAS", "CPP", "RRIF", "TFSA", "LIF"):
        walk(name.lower(), globals()[name])
    return found


def is_stale(year: int) -> bool:
    """True once the calendar has moved past the rules' tax year."""
    return TAX_YEAR < year
