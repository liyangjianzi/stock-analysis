# Canadian Retirement Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `stockanalysis.retirement` subpackage and a `stock-analysis retire` command. The command projects a Canadian household's retirement year by year under Canadian tax and benefit rules, and writes an HTML dashboard with:
- the chance of success and the legacy left behind,
- a stacked yearly income projection by source,
- ranked what-if suggestions.

**Architecture:** `rules.py` is the only home for statutory values, each cited to its official source. `tax.py` computes one person-year of federal and Alberta tax and searches for the best pension split. `inputs.py` turns `plan.json` plus the holdings workbook into a validated `PlanInputs`. `engine.py` runs a year-by-year account model over N return paths, plus an "average" path and a "bad-luck" path. `scenarios.py` reruns the plan with one change at a time and ranks the results. `report.py` builds a self-contained HTML page with Plotly. `cli.py` adds the `retire` subcommand. The engine stays pure: balances come in on `PlanInputs.accounts`, filled either from plan.json's `balances` or through `inputs.with_holdings`.

**Tech Stack:** Python ≥ 3.10, numpy, pandas, plotly (all already dependencies), pytest.

**Deliberate refinements of the spec, made while planning:**
1. **Legacy is a hero stat tile, not a gauge.** The dataviz skill reserves a meter for a ratio against a limit, and a legacy has no limit. The chance of success keeps its meter.
2. **Balances come from `--holdings` first, then plan.json `balances`, then the default holdings workbook.** An explicit flag wins, and pinned balances beat an implicit file.
3. **`run(plan, *, paths, seed)` takes a plan whose balances are already filled.** `inputs.with_holdings(plan, frame)` is the library way to fill them from holdings. This keeps the engine pure.
4. **RRSPs convert to a RRIF at `rrif_start_age` (default 65).** Official CRA pages confirm that plain RRSP withdrawals are *not* eligible pension income; only RRIF and LIF payments at 65+ qualify for the pension credit and for splitting.

**Spec:** `docs/superpowers/specs/2026-09-29-retirement-planner-design.md`

## Global Constraints

- **Python and dependencies:**
  - Must run on Python ≥ 3.10 (`requires-python = ">=3.10"`). No `tomllib`, no `match`, and no backslashes inside f-string expressions.
  - No new dependencies. Only stdlib, numpy, pandas and plotly. Validation is plain Python, like `thesis.model`.
- **Privacy (the repo is public):**
  - No personal numbers go in package code, tests, this plan or the spec. Tests use invented values only.
  - The owner's `retirement/plan.json` and `retirement/output/` stay in the gitignored `retirement/` folder, and `tests/test_privacy.py` enforces this.
- **Canadian rules:**
  - **Every Canadian rule value lives only in `src/stockanalysis/retirement/rules.py`,** as `Rule(value, year, source)`. The source is an `https://` link to an official CRA, Service Canada, Government of Canada or Alberta page.
  - Values are held flat in today's dollars for every projected year.
- **Money:** all amounts are in today's CAD dollars, and every return is a *real* (after-inflation) return.
- **Pure modules:** `rules`, `tax`, `inputs` (apart from `load_inputs` / `write_template`), `engine`, `scenarios` and `report.build_report` do no printing and no I/O. The I/O helpers are `report.save_report`, `report.write_summary` and `report.latest_summary`.
- **Tests:** fully offline. No network and no yfinance.
- **Chart palette** (validated with the dataviz `validate_palette.js`, light mode: all checks PASS; the contrast WARN is relieved by the year-by-year table):

  | Role | Colour |
  |---|---|
  | Categorical series, in this fixed order | `#2a78d6, #eb6834, #1baf7a, #eda100, #e87ba4, #008300, #4a3aa7` |
  | Shortfall (status critical) | `#d03b3b` |
  | Surface | `#fcfcfb` |
  | Page | `#f9f9f7` |
  | Ink | `#0b0b0b` |
  | Secondary ink | `#52514e` |
  | Muted | `#898781` |
  | Grid | `#e1e0d9` |
  | Axis | `#c3c2b7` |
  | Good text | `#006300` |
  | Meter track | `#cde2fb` |

  The page is light mode only (v1 scope).
- **Commits:** work on branch `feat/retirement-planner`, not `main`.

## Review Focus

1. **A person who has already passed an age threshold at the start** (already retired, or already past `rrif_start_age`) → minimums apply from step 0 and nothing is contributed. Pinned by `test_already_converted_rrif_pays_minimum_immediately` (Task 5).
2. **A one-person household** → no pension splitting, and chart labels show a single age (`"62"`, not `"62/"`). Pinned by `test_single_person_household_*` (Tasks 5, 8).
3. **Holdings account names that nearly match a keyword** ("RESP" vs "RRSP", "Lifeco" vs "LIF") → RESP is excluded, and "Lifeco" is *not* treated as a LIF; an unmapped name stops the run. Pinned by `test_keyword_collisions_are_not_misread` (Task 3).
4. **Empty accounts or zero balances** (division by zero in pro-rata shares and in the non-registered cost ratio) → no NaN anywhere; the whole need becomes a shortfall. Pinned by `test_all_empty_accounts_produce_no_nan` (Task 5).
5. **A spending change dated before `start_year`** → applies from step 0. Pinned by `test_spending_change_before_start_applies_from_step_0` (Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| Create `src/stockanalysis/retirement/__init__.py` | Public API: `run`, `PlanResult`, `Projection`, `PlanInputs`, `load_inputs` |
| Create `src/stockanalysis/retirement/rules.py` | Every Canadian statutory value with its year and source, plus lookup helpers |
| Create `src/stockanalysis/retirement/tax.py` | Vectorized person tax, household tax, pension-split search |
| Create `src/stockanalysis/retirement/inputs.py` | Dataclasses, JSON load/validate, template, sorting holdings accounts |
| Create `src/stockanalysis/retirement/engine.py` | Returns, benefits, the year-by-year simulation, `run`, the bad-luck path |
| Create `src/stockanalysis/retirement/scenarios.py` | One-change variants, and ranking them by change in success |
| Create `src/stockanalysis/retirement/report.py` | HTML page and charts; summary JSON I/O |
| Create `src/stockanalysis/retirement/cli.py` | `add_parser` / `dispatch` for `stock-analysis retire` |
| Modify `src/stockanalysis/config.py` (after line 29) | `DEFAULT_RETIREMENT_INPUTS`, `DEFAULT_RETIREMENT_OUT` |
| Modify `src/stockanalysis/cli.py:279-281, 452-458` | Wire in the `retire` subcommand |
| Modify `CLAUDE.md` | Run section, module-map row, conventions bullet, test-suite list |
| Create `tests/test_retirement_rules.py`, `tests/test_retirement_tax.py`, `tests/test_retirement_inputs.py`, `tests/test_retirement_engine.py`, `tests/test_retirement_scenarios.py`, `tests/test_retirement_report.py`, `tests/test_cli_retire.py` | Offline tests with invented numbers |

---

### Task 1: Branch, package skeleton, config paths and `rules.py`

**Files:**
- Create: `src/stockanalysis/retirement/__init__.py`, `src/stockanalysis/retirement/rules.py`
- Modify: `src/stockanalysis/config.py` (after line 29, `DEFAULT_THESES_DIR`)
- Test: `tests/test_retirement_rules.py`
- Also commit the already-modified `.gitignore` (the `retirement/` entry) and `tests/test_privacy.py` (the retirement guards).

**Interfaces:**
- Produces:
  - `rules.Rule(value, year: int, source: str)`, a frozen dataclass.
  - `rules.TAX_YEAR: int`.
  - Rule tables: `rules.FEDERAL`, `rules.PROVINCIAL["AB"]`, `rules.CAPITAL_GAINS_INCLUSION`, `rules.PENSION_SPLIT`, `rules.OAS`, `rules.CPP`, `rules.RRIF`, `rules.TFSA`, `rules.LIF["AB"]`.
  - `rules.rrif_min_factor(age_jan1: int) -> float`
  - `rules.lif_max_pct(province: str, age_jan1: int) -> float`
  - `rules.top_marginal_rate(province: str) -> float`
  - `rules.all_rules() -> list[tuple[str, Rule]]`
  - `rules.is_stale(year: int) -> bool`
  - `config.DEFAULT_RETIREMENT_INPUTS`, `config.DEFAULT_RETIREMENT_OUT` (both `Path`).

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/retirement-planner
```

- [ ] **Step 2: Write the failing test** — `tests/test_retirement_rules.py`

```python
"""The rules table: every statutory value cited, and the lookup helpers."""
from __future__ import annotations

from stockanalysis.retirement import rules


def test_every_rule_value_is_cited_with_year_and_https_source():
    found = rules.all_rules()
    assert len(found) >= 20
    for name, rule in found:
        assert isinstance(rule.year, int) and 2016 <= rule.year <= rules.TAX_YEAR, name
        assert rule.source.startswith("https://"), name


def _check_brackets(brackets):
    uppers = [u for u, _ in brackets]
    rates = [r for _, r in brackets]
    assert uppers == sorted(uppers) and len(set(uppers)) == len(uppers)
    assert uppers[-1] == rules.INF
    assert all(0 < r < 1 for r in rates) and rates == sorted(rates)


def test_brackets_ascend_and_rates_are_fractions():
    _check_brackets(rules.FEDERAL["brackets"].value)
    for prov in rules.PROVINCIAL.values():
        _check_brackets(prov["brackets"].value)


def test_rrif_minimum_factors():
    table = rules.RRIF["factors"].value
    assert set(table) == set(range(71, 96))
    assert rules.rrif_min_factor(70) == 1 / 20
    assert rules.rrif_min_factor(71) == 0.0528
    assert rules.rrif_min_factor(99) == 0.20
    values = [table[a] for a in range(71, 96)]
    assert values == sorted(values)


def test_lif_maximum_covers_50_to_89_and_never_undercuts_the_minimum():
    table = rules.LIF["AB"]["max_pct"].value
    assert set(table) == set(range(50, 90))
    assert rules.lif_max_pct("AB", 45) == table[50]
    assert rules.lif_max_pct("AB", 95) == 1.0
    for age in range(50, 90):
        assert rules.lif_max_pct("AB", age) >= rules.rrif_min_factor(age)


def test_top_marginal_rate_alberta():
    assert abs(rules.top_marginal_rate("AB") - 0.48) < 1e-12


def test_is_stale():
    assert not rules.is_stale(rules.TAX_YEAR)
    assert rules.is_stale(rules.TAX_YEAR + 1)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/test_retirement_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stockanalysis.retirement'`

- [ ] **Step 4: Create the package and the rules table**

`src/stockanalysis/retirement/__init__.py`:

```python
"""Canadian retirement planner.

See docs/superpowers/specs/2026-09-29-retirement-planner-design.md. The public
API (``run``, ``PlanInputs``, ...) is exported once the engine exists.
"""
```

`src/stockanalysis/retirement/rules.py`:

```python
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
```

In `src/stockanalysis/config.py`, directly after the `DEFAULT_THESES_DIR = ...` line (line 29), add:

```python
# Personal retirement plan and its reports: gitignored (the repo is public).
DEFAULT_RETIREMENT_INPUTS = Path(__file__).resolve().parents[2] / "retirement" / "plan.json"
DEFAULT_RETIREMENT_OUT = Path(__file__).resolve().parents[2] / "retirement" / "output"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_rules.py tests/test_privacy.py -v`
Expected: PASS (6 + 5 tests)

- [ ] **Step 6: Commit**

```bash
git add .gitignore tests/test_privacy.py src/stockanalysis/config.py \
        src/stockanalysis/retirement/__init__.py src/stockanalysis/retirement/rules.py \
        tests/test_retirement_rules.py
git commit -m "feat(retirement): Canadian rules table, each value cited to its official source"
```

---

### Task 2: `tax.py` — person tax, household tax and the pension-split search

**Files:**
- Create: `src/stockanalysis/retirement/tax.py`
- Test: `tests/test_retirement_tax.py`

**Interfaces:**
- Consumes: the `rules` tables from Task 1.
- Produces:
  - `tax.bracket_tax(income, brackets) -> ndarray`
  - `tax.oas_recovery(net, oas) -> ndarray`
  - `tax.age_amount(net, age, spec: dict) -> ndarray`
  - `tax.income_tax(*, ordinary=0.0, pension=0.0, gains=0.0, oas=0.0, age=0, province="AB") -> ndarray`
  - `tax.household_tax(parts: list[dict], share, province="AB") -> ndarray`
  - `tax.best_split(parts: list[dict], province="AB") -> ndarray`
  - `tax.couple_tax(a: dict, b: dict, province="AB") -> tuple[ndarray, ndarray, ndarray]`

  A "part" is `{"ordinary", "pension", "gains", "oas", "age"}`, where `pension` is eligible pension income before any split. A positive `share` moves that fraction of A's eligible pension to B, and a negative one moves B's to A.

- [ ] **Step 1: Write the failing test** — `tests/test_retirement_tax.py`

```python
"""Canadian personal tax, checked against hand-worked 2026 federal + Alberta cases."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import rules, tax


def t(**kw) -> float:
    return float(tax.income_tax(**kw))


def test_no_tax_at_the_basic_personal_amount():
    assert t() == 0.0
    assert t(ordinary=16_452, age=50) == pytest.approx(0.0, abs=1e-6)


def test_mid_income_federal_plus_alberta():
    # Fed: .14*58,523 + .205*1,477 - .14*16,452 = 6,192.725
    # AB:  .08*60,000 - .08*22,769 = 2,978.48
    assert t(ordinary=60_000, age=50) == pytest.approx(9_171.205, abs=0.01)


def test_senior_with_pension_income_gets_age_and_pension_credits():
    # net 49,150. Fed: 6,881 - .14*(16,452 + 8,800.3 + 2,000) = 3,065.678
    # AB: 3,932 - .08*(22,769 + 6,057.6 + 1,753) = 1,485.632
    assert t(pension=40_000, oas=9_150, age=70) == pytest.approx(4_551.31, abs=0.01)


def test_age_amount_phases_out():
    spec = rules.FEDERAL["age_amount"].value
    assert tax.age_amount(np.array([46_432.0]), 70, spec)[0] == 9_208
    assert tax.age_amount(np.array([107_819.0]), 70, spec)[0] == 0.0
    assert tax.age_amount(np.array([20_000.0]), 64, spec)[0] == 0.0


def test_pension_credit_only_from_65():
    assert t(pension=30_000, age=64) == pytest.approx(t(ordinary=30_000, age=64))
    assert t(pension=30_000, age=65) < t(ordinary=30_000, age=65)


def test_only_half_a_capital_gain_is_taxed():
    assert t(gains=20_000, age=50) == pytest.approx(t(ordinary=10_000, age=50))


def test_oas_recovery_starts_at_threshold_and_is_capped():
    assert float(tax.oas_recovery(95_323.0, 9_150.0)) == 0.0
    assert float(tax.oas_recovery(109_150.0, 9_150.0)) == pytest.approx(2_074.05)
    assert float(tax.oas_recovery(500_000.0, 9_150.0)) == 9_150.0


def _part(pension, age, ordinary=0.0):
    z = np.zeros(3)
    return {"ordinary": z + ordinary, "pension": z + pension, "gains": z, "oas": z, "age": age}


def test_best_split_equalises_a_one_sided_pension():
    a, b = _part(80_000, 70), _part(0, 70)
    ta, tb, share = tax.couple_tax(a, b)
    assert np.all(share == 0.5)
    unsplit = tax.household_tax([a, b], 0.0)
    assert np.all(ta + tb < unsplit)
    ta2, tb2, share2 = tax.couple_tax(b, a)
    assert np.all(share2 == -0.5)
    np.testing.assert_allclose(ta + tb, ta2 + tb2)


def test_no_split_before_65():
    a, b = _part(80_000, 60), _part(0, 60)
    _, _, share = tax.couple_tax(a, b)
    assert np.all(share == 0.0)


def test_split_never_costs_more_than_no_split():
    rng = np.random.default_rng(0)
    a = {"ordinary": rng.uniform(0, 50_000, 50), "pension": rng.uniform(0, 90_000, 50),
         "gains": rng.uniform(0, 20_000, 50), "oas": np.full(50, 9_150.0), "age": 72}
    b = {"ordinary": rng.uniform(0, 50_000, 50), "pension": rng.uniform(0, 90_000, 50),
         "gains": np.zeros(50), "oas": np.full(50, 9_150.0), "age": 70}
    ta, tb, _ = tax.couple_tax(a, b)
    assert np.all(ta + tb <= tax.household_tax([a, b], 0.0) + 1e-9)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_retirement_tax.py -v`
Expected: FAIL with `ImportError: cannot import name 'tax'`

- [ ] **Step 3: Write `src/stockanalysis/retirement/tax.py`**

```python
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
    net = ordinary + pension + oas + rules.CAPITAL_GAINS_INCLUSION.value * gains
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

    return federal + provincial + oas_recovery(net, oas)


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_tax.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/tax.py tests/test_retirement_tax.py
git commit -m "feat(retirement): federal + Alberta tax with age/pension credits, OAS clawback and pension splitting"
```

---

### Task 3: `inputs.py` — plan.json, validation, the template and sorting holdings accounts

**Files:**
- Create: `src/stockanalysis/retirement/inputs.py`
- Test: `tests/test_retirement_inputs.py`

**Interfaces:**
- Consumes: `rules.PROVINCIAL`, `rules.LIF`, `rules.RRIF` (Task 1).
- Produces:
  - Frozen dataclasses:
    - `Person(id, name, age, retire_age, cpp_start_age=70, oas_start_age=70, cpp_at_65=None, cpp_years=0.0, cpp_earnings_ratio=1.0, years_in_canada_at_65=40.0, rrif_start_age=65, lif_start_age=None, unlock_share=0.5, tfsa_room=0.0, contributions={}, contributions_when_partner_retired=None)`
    - `SpendingChange(year, amount, label="")`
    - `Spending(base, changes=(), slow_go_age=75, slow_go_share=0.85, no_go_age=85, no_go_share=0.70, care=0.0, bad_market_cut=0.10, bad_market_trigger=0.80)`
    - `Home(value, downsize_age=None, new_value=0.0, selling_cost=0.04, moving_cost=0.0, property_tax=0.0, insurance=0.0)`
    - `Returns(mean=0.05, sd=0.15, paths=10_000, seed=7)`
    - `Withdrawal(strategy="rrsp_first", steady_income_target=58_000.0)`
    - `Account(owner, type, balance, cost=None)`
    - `PlanInputs(province, start_year, end_age, people: tuple, spending, home, returns, withdrawal, accounts: tuple = (), holdings: dict = {}, scenarios: dict = {})`
  - Constants: `ACCOUNT_TYPES`, `STRATEGIES`, `STRATEGY_LABELS`, `TEMPLATE`.
  - Functions:
    - `load_inputs(path) -> PlanInputs` (raises `FileNotFoundError` with the `--init` hint, and `ValueError` naming the field)
    - `validate(plan) -> PlanInputs`
    - `write_template(path) -> Path` (raises `FileExistsError`)
    - `balances_from_holdings(frame, mapping, people) -> tuple[Account, ...]`
    - `with_holdings(plan, frame) -> PlanInputs`

- [ ] **Step 1: Write the failing test** — `tests/test_retirement_inputs.py`

```python
"""plan.json loading/validation and sorting holdings accounts (invented numbers)."""
from __future__ import annotations

import copy
import json
import re

import numpy as np
import pandas as pd
import pytest

from stockanalysis.retirement import inputs


@pytest.fixture
def plan(tmp_path):
    return inputs.load_inputs(inputs.write_template(tmp_path / "plan.json"))


def test_template_round_trip(plan):
    assert [p.id for p in plan.people] == ["A", "B"]
    assert plan.withdrawal.strategy == "rrsp_first"
    assert {a.type for a in plan.accounts} == {"rrsp", "tfsa", "pension", "nonreg"}
    assert plan.spending.changes[0].amount == -10_000
    assert plan.home.downsize_age == 65


def test_write_template_refuses_to_overwrite(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("{}")
    with pytest.raises(FileExistsError, match="already exists"):
        inputs.write_template(path)


def test_missing_plan_names_the_init_hint(tmp_path):
    with pytest.raises(FileNotFoundError, match="--init"):
        inputs.load_inputs(tmp_path / "nope.json")


@pytest.mark.parametrize("edit, field", [
    (lambda d: d["people"][0].update(retire_age=40), "people[0].retire_age"),
    (lambda d: d["people"][0].update(cpp_start_age=58), "people[0].cpp_start_age"),
    (lambda d: d["people"][1].update(lif_start_age=45), "people[1].lif_start_age"),
    (lambda d: d["withdrawal"].update(strategy="yolo"), "withdrawal.strategy"),
    (lambda d: d.update(province="ZZ"), "province"),
    (lambda d: d["spending"].update(slow_go_share=1.5), "spending.slow_go_share"),
    (lambda d: d["home"].update(new_value=2_000_000), "home.new_value"),
    (lambda d: d["balances"][0].update(owner="Z"), "balances[0].owner"),
])
def test_validation_names_the_field(tmp_path, edit, field):
    d = copy.deepcopy(inputs.TEMPLATE)
    edit(d)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError, match=re.escape(field)):
        inputs.load_inputs(path)


def _frame(rows):
    return pd.DataFrame(rows, columns=["account", "kind", "shares", "cost", "value", "value_cad"])


MAPPING = {"owners": {"A": ["Partner A"], "B": ["Partner B"]},
           "accounts": {"Partner B Company": {"owner": "B", "type": "pension"},
                        "Partner A Lifeco shares": {"type": "nonreg"}},
           "ignore": []}


def test_holdings_sorted_by_keywords_and_mapping(plan):
    frame = _frame([
        ["Partner A RRSP", "stock", 10, 50.0, 1_000.0, 1_000.0],
        ["Partner B TFSA CAD", "cash", np.nan, np.nan, 500.0, 500.0],
        ["Family RESP", "stock", 5, 10.0, 100.0, 100.0],
        ["Partner B Company", "other", np.nan, np.nan, 2_000.0, 2_000.0],
        ["Partner A Lifeco shares", "stock", 10, 5.0, 100.0, 100.0],
    ])
    accts = {(a.owner, a.type): a for a in inputs.balances_from_holdings(frame, MAPPING, plan.people)}
    assert accts[("A", "rrsp")].balance == 1_000
    assert accts[("B", "tfsa")].balance == 500
    assert accts[("B", "pension")].balance == 2_000
    assert accts[("A", "nonreg")].balance == 100 and accts[("A", "nonreg")].cost == 50
    assert sum(a.balance for a in accts.values()) == 3_600      # the RESP is excluded


def test_keyword_collisions_are_not_misread(plan):
    # "Lifeco" must not be read as a LIF, and nothing unmapped is silently dropped.
    frame = _frame([["Partner A Lifeco", "stock", 1, 1.0, 10.0, 10.0],
                    ["Partner A RESP", "cash", np.nan, np.nan, 5.0, 5.0]])
    with pytest.raises(ValueError, match="Partner A Lifeco"):
        inputs.balances_from_holdings(frame, {"owners": MAPPING["owners"]}, plan.people)


def test_account_with_no_owner_raises(plan):
    frame = _frame([["Joint RRSP", "cash", np.nan, np.nan, 5.0, 5.0]])
    with pytest.raises(ValueError, match="whose"):
        inputs.balances_from_holdings(frame, {"owners": MAPPING["owners"]}, plan.people)


def test_with_holdings_replaces_the_balances(plan):
    frame = _frame([["Partner A RRSP", "cash", np.nan, np.nan, 7.0, 7.0]])
    out = inputs.with_holdings(plan, frame)
    assert out.accounts == (inputs.Account(owner="A", type="rrsp", balance=7.0, cost=None),)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_retirement_inputs.py -v`
Expected: FAIL with `ImportError: cannot import name 'inputs'`

- [ ] **Step 3: Write `src/stockanalysis/retirement/inputs.py`**

```python
"""Plan inputs for the retirement planner: plan.json, validated in plain Python.

``plan.json`` describes the household: people, savings, spending, home and
assumptions. Account balances come from its ``balances`` list when present;
otherwise they come live from the holdings workbook, sorted into
(owner, account type) by :func:`balances_from_holdings`.

Nothing here is personal: the owner's plan lives in the gitignored
``retirement/`` folder, and :data:`TEMPLATE` uses invented values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import rules

ACCOUNT_TYPES = ("rrsp", "pension", "tfsa", "nonreg")
STRATEGIES = ("rrsp_first", "proportional", "steady_income")
STRATEGY_LABELS = {
    "rrsp_first": "Withdraw from RRSP/RRIF first",
    "proportional": "Withdraw proportionally from all account types",
    "steady_income": "Keep taxable income steady (fill the lowest bracket)",
}
# Account-name keywords, checked in order. Word boundaries keep "RESP" apart
# from "RRSP", and "Lifeco" apart from "LIF".
KEYWORDS = (
    ("exclude", re.compile(r"\bRESP\b", re.I)),
    ("rrsp", re.compile(r"\bRRSP\b|\bRRIF\b", re.I)),
    ("tfsa", re.compile(r"\bTFSA\b", re.I)),
    ("pension", re.compile(r"\bLIRA\b|\bLIF\b|\bDCPP\b|\bPENSION\b", re.I)),
)


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    age: int
    retire_age: int
    cpp_start_age: int = 70
    oas_start_age: int = 70
    cpp_at_65: float | None = None
    cpp_years: float = 0.0
    cpp_earnings_ratio: float = 1.0
    years_in_canada_at_65: float = 40.0
    rrif_start_age: int = 65
    lif_start_age: int | None = None
    unlock_share: float = 0.5
    tfsa_room: float = 0.0
    contributions: dict = field(default_factory=dict)
    contributions_when_partner_retired: dict | None = None


@dataclass(frozen=True)
class SpendingChange:
    year: int
    amount: float
    label: str = ""


@dataclass(frozen=True)
class Spending:
    base: float
    changes: tuple = ()
    slow_go_age: int = 75
    slow_go_share: float = 0.85
    no_go_age: int = 85
    no_go_share: float = 0.70
    care: float = 0.0
    bad_market_cut: float = 0.10
    bad_market_trigger: float = 0.80


@dataclass(frozen=True)
class Home:
    value: float
    downsize_age: int | None = None
    new_value: float = 0.0
    selling_cost: float = 0.04
    moving_cost: float = 0.0
    property_tax: float = 0.0
    insurance: float = 0.0


@dataclass(frozen=True)
class Returns:
    mean: float = 0.05
    sd: float = 0.15
    paths: int = 10_000
    seed: int = 7


@dataclass(frozen=True)
class Withdrawal:
    strategy: str = "rrsp_first"
    steady_income_target: float = 58_000.0


@dataclass(frozen=True)
class Account:
    owner: str
    type: str
    balance: float
    cost: float | None = None


@dataclass(frozen=True)
class PlanInputs:
    province: str
    start_year: int
    end_age: int
    people: tuple
    spending: Spending
    home: Home | None
    returns: Returns
    withdrawal: Withdrawal
    accounts: tuple = ()
    holdings: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)


# Invented example household for `stock-analysis retire --init`. Delete
# "balances" to read balances from the holdings workbook instead.
TEMPLATE = {
    "province": "AB",
    "start_year": 2026,
    "end_age": 95,
    "people": [
        {"id": "A", "name": "Partner A", "age": 50, "retire_age": 60,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 25, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 40,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "contributions": {"pension": 8000, "tfsa": 7000, "rrsp": 10000, "nonreg": 0},
         "contributions_when_partner_retired": None},
        {"id": "B", "name": "Partner B", "age": 48, "retire_age": 58,
         "cpp_start_age": 70, "oas_start_age": 70, "cpp_at_65": None,
         "cpp_years": 20, "cpp_earnings_ratio": 0.9, "years_in_canada_at_65": 38,
         "rrif_start_age": 65, "lif_start_age": None, "unlock_share": 0.5, "tfsa_room": 0,
         "contributions": {"pension": 6000, "tfsa": 7000, "rrsp": 8000, "nonreg": 0},
         "contributions_when_partner_retired": None},
    ],
    "spending": {"base": 80000,
                 "changes": [{"year": 2035, "amount": -10000, "label": "Kids leave home"}],
                 "slow_go_age": 75, "slow_go_share": 0.85, "no_go_age": 85, "no_go_share": 0.70,
                 "care": 25000, "bad_market_cut": 0.10, "bad_market_trigger": 0.80},
    "home": {"value": 900000, "downsize_age": 65, "new_value": 600000, "selling_cost": 0.04,
             "moving_cost": 20000, "property_tax": 6000, "insurance": 2000},
    "returns": {"mean": 0.05, "sd": 0.15, "paths": 10000, "seed": 7},
    "withdrawal": {"strategy": "rrsp_first", "steady_income_target": 58000},
    "balances": [
        {"owner": "A", "type": "rrsp", "balance": 400000},
        {"owner": "A", "type": "tfsa", "balance": 100000},
        {"owner": "A", "type": "nonreg", "balance": 50000, "cost": 40000},
        {"owner": "B", "type": "rrsp", "balance": 300000},
        {"owner": "B", "type": "tfsa", "balance": 90000},
        {"owner": "B", "type": "pension", "balance": 80000},
    ],
    "holdings": {"owners": {"A": ["Partner A"], "B": ["Partner B"]}, "accounts": {}, "ignore": []},
    "scenarios": {"downsize_ages": [None, 65, 70], "cheaper_home_share": 0.8},
}


def _build(d: dict) -> PlanInputs:
    try:
        spending = dict(d["spending"])
        spending["changes"] = tuple(SpendingChange(**c) for c in spending.get("changes", []))
        return PlanInputs(
            province=d["province"], start_year=int(d["start_year"]), end_age=int(d["end_age"]),
            people=tuple(Person(**p) for p in d["people"]),
            spending=Spending(**spending),
            home=None if d.get("home") is None else Home(**d["home"]),
            returns=Returns(**d.get("returns", {})),
            withdrawal=Withdrawal(**d.get("withdrawal", {})),
            accounts=tuple(Account(**a) for a in d.get("balances") or []),
            holdings=dict(d.get("holdings") or {}),
            scenarios=dict(d.get("scenarios") or {}),
        )
    except KeyError as e:
        raise ValueError(f"plan.json is missing the field {e}") from e
    except TypeError as e:
        raise ValueError(f"plan.json has an unexpected or missing field: {e}") from e


def _fail(field_name: str, message: str):
    raise ValueError(f"{field_name}: {message}")


def validate(plan: PlanInputs) -> PlanInputs:
    """Return ``plan`` unchanged, or raise ValueError naming the offending field."""
    if plan.province not in rules.PROVINCIAL or plan.province not in rules.LIF:
        _fail("province", f"{plan.province!r} is not in rules.py (have {sorted(rules.PROVINCIAL)})")
    if not 1 <= len(plan.people) <= 2:
        _fail("people", "need one or two people")
    ids = [p.id for p in plan.people]
    if len(set(ids)) != len(ids):
        _fail("people", "ids must be unique")
    lif_min = rules.LIF[plan.province]["min_age"].value
    max_unlock = rules.LIF[plan.province]["unlock_share"].value
    for i, p in enumerate(plan.people):
        f = f"people[{i}]"
        if not 18 <= p.age < plan.end_age:
            _fail(f"{f}.age", f"{p.age} must be 18 or more and below end_age {plan.end_age}")
        if p.retire_age < p.age:
            _fail(f"{f}.retire_age", f"{p.retire_age} is below age {p.age}")
        if p.retire_age >= plan.end_age:
            _fail(f"{f}.retire_age", f"{p.retire_age} must be below end_age {plan.end_age}")
        if not 60 <= p.cpp_start_age <= 70:
            _fail(f"{f}.cpp_start_age", "CPP starts between 60 and 70")
        if not 65 <= p.oas_start_age <= 70:
            _fail(f"{f}.oas_start_age", "OAS starts between 65 and 70")
        if p.rrif_start_age > rules.RRIF["convert_by_age"].value:
            _fail(f"{f}.rrif_start_age", "an RRSP must become a RRIF by 71")
        if p.lif_start_age is not None and p.lif_start_age < lif_min:
            _fail(f"{f}.lif_start_age", f"a LIF can start at {lif_min} at the earliest")
        if not 0 <= p.unlock_share <= max_unlock:
            _fail(f"{f}.unlock_share", f"between 0 and {max_unlock}")
        if not 0 <= p.cpp_earnings_ratio <= 1:
            _fail(f"{f}.cpp_earnings_ratio", "between 0 and 1")
        for name in ("cpp_years", "years_in_canada_at_65", "tfsa_room"):
            if getattr(p, name) < 0:
                _fail(f"{f}.{name}", "must not be negative")
        if p.cpp_at_65 is not None and p.cpp_at_65 < 0:
            _fail(f"{f}.cpp_at_65", "must not be negative")
        for label, amounts in (("contributions", p.contributions),
                               ("contributions_when_partner_retired",
                                p.contributions_when_partner_retired or {})):
            for kind, amount in amounts.items():
                if kind not in ACCOUNT_TYPES:
                    _fail(f"{f}.{label}.{kind}", f"unknown account type (use {ACCOUNT_TYPES})")
                if amount < 0:
                    _fail(f"{f}.{label}.{kind}", "must not be negative")
    s = plan.spending
    if s.base < 0:
        _fail("spending.base", "must not be negative")
    for name in ("slow_go_share", "no_go_share", "bad_market_cut", "bad_market_trigger"):
        if not 0 <= getattr(s, name) <= 1:
            _fail(f"spending.{name}", "must be between 0 and 1")
    if s.slow_go_age > s.no_go_age:
        _fail("spending.slow_go_age", "must not be after no_go_age")
    if s.care < 0:
        _fail("spending.care", "must not be negative")
    h = plan.home
    if h is not None:
        if h.value <= 0:
            _fail("home.value", "must be positive")
        if not 0 <= h.selling_cost < 1:
            _fail("home.selling_cost", "between 0 and 1")
        for name in ("moving_cost", "property_tax", "insurance", "new_value"):
            if getattr(h, name) < 0:
                _fail(f"home.{name}", "must not be negative")
        if h.downsize_age is not None and h.value * (1 - h.selling_cost) - h.new_value - h.moving_cost < 0:
            _fail("home.new_value", "the sale must cover the new home and the move")
    r = plan.returns
    if r.mean <= -1 or r.sd < 0 or r.paths < 1:
        _fail("returns", "need mean > -1, sd >= 0 and paths >= 1")
    if plan.withdrawal.strategy not in STRATEGIES:
        _fail("withdrawal.strategy", f"{plan.withdrawal.strategy!r} is not one of {STRATEGIES}")
    if plan.withdrawal.steady_income_target < 0:
        _fail("withdrawal.steady_income_target", "must not be negative")
    for j, a in enumerate(plan.accounts):
        if a.owner not in ids:
            _fail(f"balances[{j}].owner", f"{a.owner!r} is not one of the people {ids}")
        if a.type not in ACCOUNT_TYPES:
            _fail(f"balances[{j}].type", f"{a.type!r} is not one of {ACCOUNT_TYPES}")
        if a.balance < 0:
            _fail(f"balances[{j}].balance", "must not be negative")
    return plan


def load_inputs(path) -> PlanInputs:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No plan at {path}. Create one with: stock-analysis retire --init --inputs {path}")
    return validate(_build(json.loads(path.read_text(encoding="utf-8"))))


def write_template(path) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; not overwriting it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
    return path


def _cost_cad(row) -> float:
    """Cost base in CAD: value_cad scaled by (shares x unit cost / value)."""
    try:
        ratio = float(row["shares"]) * float(row["cost"]) / float(row["value"])
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        return float(row["value_cad"])
    return float(row["value_cad"]) * ratio if np.isfinite(ratio) and ratio > 0 else float(row["value_cad"])


def _classify(name: str, mapping: dict) -> str | None:
    explicit = (mapping.get("accounts") or {}).get(name, {})
    if "type" in explicit:
        return explicit["type"]
    if name in (mapping.get("ignore") or []):
        return "exclude"
    for kind, pattern in KEYWORDS:
        if pattern.search(name):
            return kind
    return None


def _owner(name: str, mapping: dict, people) -> str | None:
    explicit = (mapping.get("accounts") or {}).get(name, {})
    if "owner" in explicit:
        return explicit["owner"]
    hits = [pid for pid, words in (mapping.get("owners") or {}).items()
            if any(w.lower() in name.lower() for w in words)]
    if len(hits) == 1:
        return hits[0]
    if len(people) == 1:
        return people[0].id
    return None


def balances_from_holdings(frame: pd.DataFrame, mapping: dict, people) -> tuple:
    """Sort holdings rows (``holdings.load()["holdings"]``) into Accounts.

    Type comes from an explicit ``mapping["accounts"][name]["type"]`` first, then
    keywords (RESP excluded; RRSP/RRIF; TFSA; LIRA/LIF/DCPP/pension). Anything
    else must be listed, so money is never silently dropped.
    """
    unknown, unowned, totals = [], [], {}
    for name, rows in frame.groupby("account", sort=True):
        kind = _classify(str(name), mapping)
        if kind is None:
            unknown.append(str(name))
            continue
        if kind == "exclude":
            continue
        who = _owner(str(name), mapping, people)
        if who is None:
            unowned.append(str(name))
            continue
        balance = float(rows["value_cad"].sum())
        cost = float(sum(_cost_cad(r) for _, r in rows.iterrows()))
        b, c = totals.get((who, kind), (0.0, 0.0))
        totals[(who, kind)] = (b + balance, c + cost)
    if unknown:
        raise ValueError("Can't tell the account type of: " + ", ".join(unknown)
                         + ". Add each to plan.json holdings.accounts (type rrsp / pension / "
                           "tfsa / nonreg) or to holdings.ignore.")
    if unowned:
        raise ValueError("Can't tell whose account this is: " + ", ".join(unowned)
                         + ". Add an owner keyword to plan.json holdings.owners or an "
                           "explicit owner in holdings.accounts.")
    return tuple(Account(owner=o, type=k, balance=b, cost=c if k == "nonreg" else None)
                 for (o, k), (b, c) in sorted(totals.items()))


def with_holdings(plan: PlanInputs, frame: pd.DataFrame) -> PlanInputs:
    """``plan`` with its accounts replaced by the sorted holdings."""
    return validate(replace(plan, accounts=balances_from_holdings(frame, plan.holdings, plan.people)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_inputs.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/inputs.py tests/test_retirement_inputs.py
git commit -m "feat(retirement): plan.json inputs, validation, template and holdings account sorting"
```

---

### Task 4: `engine.py` part 1 — returns and government benefits

**Files:**
- Create: `src/stockanalysis/retirement/engine.py` (the helpers only; Task 5 appends the simulation)
- Test: `tests/test_retirement_engine.py` (the benefits and returns tests)

**Interfaces:**
- Consumes: `rules.CPP`, `rules.OAS` (Task 1); `inputs.Person` (Task 3).
- Produces:
  - `engine.lognormal_params(mean, sd) -> (mu, s)`
  - `engine.draw_returns(mean, sd, paths, years, seed) -> ndarray (years, paths)`
  - `engine.median_return(mean, sd) -> float`
  - `engine.cpp_at_65(person) -> float` (yearly)
  - `engine.cpp_factor(start_age) -> float`
  - `engine.oas_yearly(person, age) -> float`

- [ ] **Step 1: Write the failing test** — create `tests/test_retirement_engine.py`

```python
"""The retirement engine: benefits, returns and the year-by-year model (zero-volatility
cases, so every number is predictable). Invented households only."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import engine
from stockanalysis.retirement.inputs import (Account, Home, Person, PlanInputs, Returns,
                                             Spending, SpendingChange, Withdrawal)


def person(**kw) -> Person:
    base = dict(id="A", name="A", age=60, retire_age=60, cpp_start_age=70, oas_start_age=70,
                cpp_at_65=0.0, years_in_canada_at_65=0.0, rrif_start_age=71,
                lif_start_age=None, unlock_share=0.0, tfsa_room=0.0)
    base.update(kw)
    return Person(**base)


def plan(people=None, accounts=(), base=30_000.0, end_age=63, strategy="rrsp_first",
         home=None, changes=(), **spend_kw) -> PlanInputs:
    spend = dict(base=base, changes=tuple(changes), slow_go_age=200, no_go_age=200,
                 care=0.0, bad_market_cut=0.0)
    spend.update(spend_kw)
    return PlanInputs(province="AB", start_year=2026, end_age=end_age,
                      people=tuple(people or (person(),)), spending=Spending(**spend),
                      home=home, returns=Returns(0.0, 0.0, 1, 1),
                      withdrawal=Withdrawal(strategy, 58_000.0), accounts=tuple(accounts))


# -- benefits & returns ------------------------------------------------------------

def test_cpp_statement_figure_wins_over_the_estimate():
    assert engine.cpp_at_65(person(cpp_at_65=12_000.0)) == 12_000.0


def test_cpp_estimate_from_years_with_general_dropout():
    p = person(cpp_at_65=None, age=50, retire_age=60, cpp_years=20, cpp_earnings_ratio=1.0)
    counted = 47 - min(8, 0.17 * 47)                       # 39.01 years after the dropout
    assert engine.cpp_at_65(p) == pytest.approx(12 * 1_507.65 * 30 / counted)


def test_cpp_start_age_adjustments():
    assert engine.cpp_factor(65) == 1.0
    assert engine.cpp_factor(70) == pytest.approx(1.42)
    assert engine.cpp_factor(60) == pytest.approx(0.64)


def test_oas_amounts_residence_deferral_and_age_75():
    p = person(oas_start_age=65, years_in_canada_at_65=40)
    assert engine.oas_yearly(p, 64) == 0.0
    assert engine.oas_yearly(p, 65) == pytest.approx(9_150.0)
    assert engine.oas_yearly(p, 75) == pytest.approx(10_065.0)
    assert engine.oas_yearly(person(oas_start_age=70, years_in_canada_at_65=40), 70) == pytest.approx(9_150 * 1.36)
    assert engine.oas_yearly(person(oas_start_age=65, years_in_canada_at_65=20), 66) == pytest.approx(4_575.0)
    assert engine.oas_yearly(person(oas_start_age=65, years_in_canada_at_65=9), 66) == 0.0


def test_median_return_is_the_geometric_typical_return():
    assert engine.median_return(0.05, 0.15) == pytest.approx(0.03945, abs=5e-4)
    assert engine.median_return(0.05, 0.0) == pytest.approx(0.05)


def test_draw_returns_is_seeded_and_shaped_years_by_paths():
    a = engine.draw_returns(0.05, 0.15, 4_000, 3, seed=1)
    assert a.shape == (3, 4_000)
    np.testing.assert_array_equal(a, engine.draw_returns(0.05, 0.15, 4_000, 3, seed=1))
    assert a.mean() == pytest.approx(0.05, abs=0.01)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_retirement_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'engine'`

- [ ] **Step 3: Write the helpers in `src/stockanalysis/retirement/engine.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_engine.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/test_retirement_engine.py
git commit -m "feat(retirement): CPP/OAS benefit rules and lognormal real returns"
```

---

### Task 5: `engine.py` part 2 — the year-by-year simulation

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (append)
- Test: `tests/test_retirement_engine.py` (append)

**Interfaces:**
- Consumes:
  - Task 4 helpers;
  - `tax.household_tax`, `tax.best_split` (Task 2);
  - `rules.rrif_min_factor`, `rules.lif_max_pct`, `rules.top_marginal_rate`, `rules.TFSA`, `rules.LIF`, `rules.PENSION_SPLIT`, `rules.CAPITAL_GAINS_INCLUSION` (Task 1);
  - `PlanInputs` (Task 3).
- Produces:
  - `engine.SOURCES = ("earned", "cpp", "oas", "minimums", "registered", "tfsa", "nonreg", "shortfall")`
  - `engine.ACCOUNTS = ("rrsp", "pension", "tfsa", "nonreg")`
  - `engine.steps(plan) -> int`
  - `engine.stage_share(spending, age) -> float`
  - `engine.allocate(withdrawal, avail, required, base_taxable) -> (draw: dict, unfunded, surplus)`
  - `engine.simulate(plan, returns: ndarray (T, N)) -> Projection`
  - `engine.Projection` dataclass:
    - fields: `years`, `ages`, `income: dict[str, (T,N)]`, `tax (T,N)`, `need (T,N)`, `saved (T,N)`, `tfsa_room (T,N)`, `investments (T+1,N)`, `balances: dict[str,(T+1,N)]`, `home_value (T+1,)`, `legacy (N,)`, `death_tax (N,)`, `retire_step: int`, `max_residual: float`
    - properties: `paths`, `shortfall_years`, `success`, `first_shortfall_step`, `lifetime_tax`, `investments_at_retirement`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_retirement_engine.py`

```python
# -- the simulation (zero returns unless noted) -------------------------------------

def run_flat(p, n=1):
    return engine.simulate(p, np.zeros((engine.steps(p), n)))


def test_sources_add_up_to_need_plus_tax_plus_saved():
    p = plan(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0),
                       Account("A", "nonreg", 50_000.0, cost=25_000.0)], base=40_000.0, end_age=66)
    proj = run_flat(p)
    total = sum(proj.income[s] for s in engine.SOURCES)
    np.testing.assert_allclose(total, proj.need + proj.tax + proj.saved, atol=2.0)
    assert proj.max_residual < 1.0 and proj.tax[0, 0] > 0


def test_rrsp_first_draws_registered_before_tfsa():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0)],
                         base=40_000.0))
    assert proj.income["registered"][0, 0] > 40_000 and proj.income["tfsa"][0, 0] == 0.0


def test_proportional_draws_in_proportion():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 100_000.0), Account("A", "tfsa", 100_000.0)],
                         base=20_000.0, strategy="proportional"))
    assert proj.income["registered"][0, 0] == pytest.approx(proj.income["tfsa"][0, 0], rel=1e-9)


def test_steady_income_fills_to_target_and_saves_the_rest():
    proj = run_flat(plan(accounts=[Account("A", "rrsp", 1_000_000.0)], base=20_000.0,
                         strategy="steady_income"))
    assert proj.income["registered"][0, 0] == pytest.approx(58_000.0)
    assert proj.saved[0, 0] == pytest.approx(58_000.0 - 20_000.0 - proj.tax[0, 0], abs=2.0)


def test_rrif_minimum_starts_the_year_after_conversion():
    proj = run_flat(plan(people=[person(age=64, rrif_start_age=65)],
                         accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=67))
    assert proj.income["minimums"][0, 0] == 0.0          # 64
    assert proj.income["minimums"][1, 0] == 0.0          # 65: the conversion year
    assert proj.income["minimums"][2, 0] == pytest.approx(0.04 * proj.balances["rrsp"][2, 0])


def test_already_converted_rrif_pays_minimum_immediately():
    proj = run_flat(plan(people=[person(age=72, rrif_start_age=65)],
                         accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=74))
    assert proj.income["minimums"][0, 0] == pytest.approx(5_280.0)


def test_locked_pension_waits_for_age_50_then_unlocks_half():
    proj = run_flat(plan(people=[person(age=48, retire_age=48, unlock_share=0.5)],
                         accounts=[Account("A", "pension", 100_000.0)], base=10_000.0, end_age=52))
    assert proj.income["shortfall"][0, 0] == pytest.approx(10_000.0)
    assert proj.income["shortfall"][1, 0] == pytest.approx(10_000.0)
    assert proj.balances["rrsp"][2, 0] == pytest.approx(50_000.0)
    assert proj.balances["pension"][2, 0] == pytest.approx(50_000.0)
    assert proj.income["shortfall"][2, 0] == pytest.approx(0.0, abs=1.0)


def test_lif_maximum_caps_withdrawals():
    proj = run_flat(plan(people=[person(age=60, retire_age=60, lif_start_age=60)],
                         accounts=[Account("A", "pension", 100_000.0)], base=50_000.0, end_age=62))
    assert proj.income["registered"][0, 0] == pytest.approx(0.0677 * 100_000)
    assert proj.income["shortfall"][0, 0] == pytest.approx(50_000 - 6_770, abs=1.0)


def test_tfsa_withdrawal_is_tax_free_and_room_returns_next_year():
    proj = run_flat(plan(accounts=[Account("A", "tfsa", 50_000.0)], base=10_000.0, end_age=62))
    assert proj.tax[0, 0] == 0.0 and proj.income["tfsa"][0, 0] == pytest.approx(10_000.0)
    assert proj.tfsa_room[1, 0] - proj.tfsa_room[0, 0] == pytest.approx(7_000.0 + 10_000.0)


def test_downsizing_adds_tax_free_money():
    home = Home(value=1_000_000.0, downsize_age=65, new_value=600_000.0, selling_cost=0.04,
                moving_cost=20_000.0)
    proj = run_flat(plan(people=[person(age=64)], base=30_000.0, end_age=67, home=home))
    assert proj.income["shortfall"][0, 0] == pytest.approx(30_000.0)
    assert proj.balances["nonreg"][1, 0] == pytest.approx(340_000.0)
    assert proj.income["nonreg"][1, 0] == pytest.approx(30_000.0) and proj.tax[1, 0] == 0.0
    assert proj.home_value[1] == 600_000.0


def test_single_path_matches_the_same_path_inside_many():
    p = plan(accounts=[Account("A", "rrsp", 500_000.0), Account("A", "tfsa", 100_000.0)],
             base=30_000.0, end_age=70)
    returns = np.random.default_rng(3).normal(0.04, 0.10, (engine.steps(p), 5))
    many, one = engine.simulate(p, returns), engine.simulate(p, returns[:, [3]])
    for s in engine.SOURCES:
        np.testing.assert_allclose(many.income[s][:, 3], one.income[s][:, 0], atol=2.0)
    np.testing.assert_allclose(many.investments[:, 3], one.investments[:, 0], atol=20.0)


def test_rich_plan_always_succeeds_and_impossible_plan_never_does():
    rich = run_flat(plan(accounts=[Account("A", "rrsp", 10_000_000.0)], base=30_000.0), n=3)
    poor = run_flat(plan(base=50_000.0), n=3)
    assert rich.success == 1.0
    assert poor.success == 0.0 and np.all(poor.shortfall_years == engine.steps(plan()))


def test_all_empty_accounts_produce_no_nan():
    for strategy in ("rrsp_first", "proportional", "steady_income"):
        proj = run_flat(plan(base=20_000.0, strategy=strategy), n=2)
        for arr in [proj.tax, proj.need, proj.saved, proj.investments, proj.legacy,
                    *proj.income.values(), *proj.balances.values()]:
            assert np.all(np.isfinite(arr)), strategy


def test_spending_change_before_start_applies_from_step_0():
    proj = run_flat(plan(base=20_000.0, changes=[SpendingChange(2000, -5_000.0)],
                         accounts=[Account("A", "tfsa", 100_000.0)]))
    assert proj.need[0, 0] == pytest.approx(15_000.0)


def test_pension_splitting_equalises_a_one_sided_rrif():
    both = dict(age=70, retire_age=60, rrif_start_age=65)
    one_sided = plan(people=[person(id="A", **both), person(id="B", name="B", **both)],
                     accounts=[Account("A", "rrsp", 1_000_000.0)], base=60_000.0, end_age=72)
    even = plan(people=[person(id="A", **both), person(id="B", name="B", **both)],
                accounts=[Account("A", "rrsp", 500_000.0), Account("B", "rrsp", 500_000.0)],
                base=60_000.0, end_age=72)
    t1, t2 = run_flat(one_sided).tax[0, 0], run_flat(even).tax[0, 0]
    assert t1 == pytest.approx(t2, rel=0.02)


def test_single_person_household_has_single_ages():
    proj = run_flat(plan(accounts=[Account("A", "tfsa", 100_000.0)]))
    assert proj.ages[0] == (60,)


def test_legacy_taxes_registered_money_at_the_top_rate():
    proj = run_flat(plan(people=[person(age=60)], accounts=[Account("A", "rrsp", 100_000.0)],
                         base=0.0, end_age=61))
    assert proj.death_tax[0] == pytest.approx(0.48 * 100_000.0)
    assert proj.legacy[0] == pytest.approx(100_000.0 * 0.52)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_retirement_engine.py -v`
Expected: FAIL with `AttributeError: module 'stockanalysis.retirement.engine' has no attribute 'simulate'` (the six Task 4 tests still pass)

- [ ] **Step 3: Append the simulation to `src/stockanalysis/retirement/engine.py`**

Add `from dataclasses import dataclass` to the imports and change `from . import rules` to `from . import rules, tax`. Add `from .inputs import PlanInputs` after it. Then append:

```python
SOURCES = ("earned", "cpp", "oas", "minimums", "registered", "tfsa", "nonreg", "shortfall")
ACCOUNTS = ("rrsp", "pension", "tfsa", "nonreg")
MAX_ITER = 40         # tax <-> withdrawal fixed-point rounds per split share
SPLIT_ROUNDS = 3      # re-optimise the pension split at most this often per year
TOL = 1.0             # dollars
SHORTFALL_EPS = 1.0   # a gap under a dollar is rounding, not a short year


def steps(plan: PlanInputs) -> int:
    """Years projected: from now until the first person reaches ``end_age``."""
    return plan.end_age - plan.people[0].age


def stage_share(spending, age: int) -> float:
    """Go-go (1.0), slow-go or no-go share of the budget at the first person's ``age``."""
    if age >= spending.no_go_age:
        return spending.no_go_share
    if age >= spending.slow_go_age:
        return spending.slow_go_share
    return 1.0


@dataclass
class Projection:
    years: list
    ages: list
    income: dict
    tax: np.ndarray
    need: np.ndarray
    saved: np.ndarray
    tfsa_room: np.ndarray
    investments: np.ndarray
    balances: dict
    home_value: np.ndarray
    legacy: np.ndarray
    death_tax: np.ndarray
    retire_step: int
    max_residual: float

    @property
    def paths(self) -> int:
        return self.tax.shape[1]

    @property
    def shortfall_years(self) -> np.ndarray:
        return (self.income["shortfall"] > SHORTFALL_EPS).sum(axis=0)

    @property
    def success(self) -> float:
        return float((self.shortfall_years == 0).mean())

    @property
    def first_shortfall_step(self) -> np.ndarray:
        short = self.income["shortfall"] > SHORTFALL_EPS
        return np.where(short.any(axis=0), short.argmax(axis=0), len(self.years))

    @property
    def lifetime_tax(self) -> np.ndarray:
        return self.tax.sum(axis=0) + self.death_tax

    @property
    def investments_at_retirement(self) -> np.ndarray:
        return self.investments[min(self.retire_step, len(self.years))]


def allocate(withdrawal, avail: dict, required: np.ndarray, base_taxable: np.ndarray):
    """Split the cash still needed across accounts.

    ``avail`` maps rrsp / lif / nonreg / tfsa to (P, N) accessible balances.
    ``required`` is (N,); a negative value is a surplus. Returns
    ``(draw, unfunded, surplus)``, where draw values are (P, N). Within each
    account type the draw is pro-rata to the people's balances.
    """
    draw = {k: np.zeros_like(v) for k, v in avail.items()}
    left = {k: v.copy() for k, v in avail.items()}
    need = np.maximum(required, 0.0)
    surplus = np.maximum(-required, 0.0)

    def take(kinds):
        nonlocal need
        for k in kinds:
            total = left[k].sum(axis=0)
            amount = np.minimum(need, total)
            share = np.divide(left[k], total, out=np.zeros_like(left[k]), where=total > 0)
            draw[k] += share * amount
            left[k] -= share * amount
            need = need - amount

    if withdrawal.strategy == "steady_income":
        room = np.maximum(withdrawal.steady_income_target - base_taxable, 0.0)
        for k in ("rrsp", "lif"):
            forced = np.minimum(left[k], room)
            draw[k] += forced
            left[k] -= forced
            room -= forced
        forced_total = draw["rrsp"].sum(axis=0) + draw["lif"].sum(axis=0)
        covered = np.minimum(need, forced_total)
        surplus = surplus + forced_total - covered
        need = need - covered
        take(("nonreg", "tfsa", "rrsp", "lif"))
    elif withdrawal.strategy == "proportional":
        total = sum(left[k].sum(axis=0) for k in left)
        amount = np.minimum(need, total)
        for k in left:
            draw[k] += np.divide(left[k], total, out=np.zeros_like(left[k]), where=total > 0) * amount
        need = need - amount
    else:
        take(("rrsp", "lif", "nonreg", "tfsa"))
    return draw, need, surplus


def _parts(people, ages, is_rrif, cpp, oas, min_rrif, min_lif, draw, gain_ratio) -> list:
    """Each person's taxable pieces. RRIF/LIF payments are eligible pension income at
    65+; plain RRSP withdrawals and anything before 65 are ordinary income."""
    pension_age = rules.PENSION_SPLIT.value["min_age"]
    parts = []
    for i in range(len(people)):
        reg = min_rrif[i] + draw["rrsp"][i]
        lif = min_lif[i] + draw["lif"][i]
        cpp_i = np.full_like(reg, cpp[i])
        if ages[i] >= pension_age:
            pension = (reg if is_rrif[i] else np.zeros_like(reg)) + lif
            ordinary = cpp_i + (np.zeros_like(reg) if is_rrif[i] else reg)
        else:
            pension = np.zeros_like(reg)
            ordinary = cpp_i + reg + lif
        parts.append({"ordinary": ordinary, "pension": pension,
                      "gains": draw["nonreg"][i] * gain_ratio[i],
                      "oas": np.full_like(reg, oas[i]), "age": ages[i]})
    return parts


def simulate(plan: PlanInputs, returns: np.ndarray) -> Projection:
    """Project ``plan`` over ``returns``: (T, N) real yearly returns, with T = steps(plan)."""
    people, prov, spend, home = plan.people, plan.province, plan.spending, plan.home
    T = steps(plan)
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2 or returns.shape[0] != T:
        raise ValueError(f"returns must have shape ({T}, paths); got {returns.shape}")
    N, P = returns.shape[1], len(people)
    owner = {p.id: i for i, p in enumerate(people)}

    bal = {k: np.zeros((P, N)) for k in ACCOUNTS}
    cost = np.zeros((P, N))
    for acct in plan.accounts:
        i = owner[acct.owner]
        bal[acct.type][i] += acct.balance
        if acct.type == "nonreg":
            cost[i] += acct.balance if acct.cost is None else acct.cost
    room = np.repeat(np.array([[p.tfsa_room] for p in people], dtype=float), N, axis=1)
    restore = np.zeros((P, N))
    lif_age = [p.lif_start_age if p.lif_start_age is not None
               else max(rules.LIF[prov]["min_age"].value, p.retire_age) for p in people]
    lif_step: list = [None] * P
    lif_gain = np.zeros((P, N))               # last year's LIF return (Alberta's "A")
    cpp_year = [cpp_at_65(p) * cpp_factor(p.cpp_start_age) for p in people]
    limit = rules.TFSA["annual_limit"].value
    split_age = rules.PENSION_SPLIT.value["min_age"]

    income = {s: np.zeros((T, N)) for s in SOURCES}
    tax_paid, need_rec, saved_rec, room_rec = (np.zeros((T, N)) for _ in range(4))
    invest = np.zeros((T + 1, N))
    balances = {k: np.zeros((T + 1, N)) for k in ACCOUNTS}
    home_value = np.zeros(T + 1)
    retire_ref, retire_step, max_resid, downsized = None, T, 0.0, False
    prev_tax, prev_share = np.zeros(N), np.zeros(N)

    for t in range(T):
        year = plan.start_year + t
        ages = [p.age + t for p in people]
        working = [ages[i] < p.retire_age for i, p in enumerate(people)]
        anyone_working = any(working)

        # 1. January 1: TFSA room, LIF start (+ unlocking), downsizing.
        room += limit + restore
        restore[:] = 0.0
        for i, p in enumerate(people):
            if lif_step[i] is None and not working[i] and ages[i] >= lif_age[i]:
                unlock = p.unlock_share * bal["pension"][i]
                bal["pension"][i] -= unlock
                bal["rrsp"][i] += unlock
                lif_step[i] = t
        if home is not None and home.downsize_age is not None and ages[0] == home.downsize_age:
            released = home.value * (1 - home.selling_cost) - home.new_value - home.moving_cost
            bal["nonreg"] += released / P
            cost += released / P
            downsized = True
        if home is not None:
            home_value[t] = home.new_value if downsized else home.value
        room_rec[t] = room.sum(axis=0)
        for k in ACCOUNTS:
            balances[k][t] = bal[k].sum(axis=0)
        invest[t] = sum(balances[k][t] for k in ACCOUNTS)
        if not anyone_working and retire_ref is None:
            retire_ref, retire_step = invest[t].copy(), t
        rrsp_jan1, pension_jan1 = bal["rrsp"].copy(), bal["pension"].copy()

        # 2. The household's after-tax spending need.
        level = spend.base + sum(c.amount for c in spend.changes if c.year <= year)
        if downsized:
            level -= (home.property_tax + home.insurance) * (1 - home.new_value / home.value)
        need = np.full(N, max(level, 0.0) * stage_share(spend, ages[0]))
        if not anyone_working:
            need = np.where(invest[t] < spend.bad_market_trigger * retire_ref,
                            need * (1 - spend.bad_market_cut), need)
        if ages[0] >= spend.no_go_age:
            need = need + spend.care
        need_rec[t] = need
        if anyone_working:
            income["earned"][t] = need
        cash_need = np.zeros(N) if anyone_working else need

        # 3. Guaranteed income: CPP, OAS, RRIF and LIF minimums.
        cpp = np.array([cpp_year[i] if ages[i] >= p.cpp_start_age else 0.0
                        for i, p in enumerate(people)])
        oas = np.array([oas_yearly(p, ages[i]) for i, p in enumerate(people)])
        is_rrif = [ages[i] >= p.rrif_start_age for i, p in enumerate(people)]
        min_rrif, min_lif, lif_room = (np.zeros((P, N)) for _ in range(3))
        for i, p in enumerate(people):
            if ages[i] > p.rrif_start_age:        # the minimum starts the year after conversion
                min_rrif[i] = rules.rrif_min_factor(ages[i] - 1) * rrsp_jan1[i]
            if lif_step[i] is not None:
                if t > lif_step[i]:
                    min_lif[i] = rules.rrif_min_factor(ages[i] - 1) * pension_jan1[i]
                cap = np.maximum(lif_gain[i], rules.lif_max_pct(prov, ages[i] - 1) * pension_jan1[i])
                lif_room[i] = np.maximum(cap - min_lif[i], 0.0)
        min_rrif = np.minimum(min_rrif, bal["rrsp"])
        min_lif = np.minimum(min_lif, bal["pension"])
        bal["rrsp"] -= min_rrif
        bal["pension"] -= min_lif
        lif_room = np.minimum(lif_room, bal["pension"])
        guaranteed = cpp.sum() + oas.sum() + (min_rrif + min_lif).sum(axis=0)
        base_taxable = cpp[:, None] + oas[:, None] + min_rrif + min_lif

        # 4. Top-up withdrawals, iterated with the household's tax.
        avail = {"rrsp": bal["rrsp"], "lif": lif_room, "nonreg": bal["nonreg"], "tfsa": bal["tfsa"]}
        gain_ratio = np.clip(np.divide(bal["nonreg"] - cost, bal["nonreg"], out=np.zeros((P, N)),
                                       where=bal["nonreg"] > 0), 0.0, 1.0)
        share, tax_est = prev_share, prev_tax.copy()
        for _round in range(SPLIT_ROUNDS):
            for _ in range(MAX_ITER):
                draw, unfunded, surplus = allocate(plan.withdrawal, avail,
                                                   cash_need + tax_est - guaranteed, base_taxable)
                parts = _parts(people, ages, is_rrif, cpp, oas, min_rrif, min_lif, draw, gain_ratio)
                new_tax = tax.household_tax(parts, share, prov)
                resid = float(np.abs(new_tax - tax_est).max())
                tax_est = new_tax
                if resid < TOL:
                    break
            if P < 2 or max(ages) < split_age:
                break
            best = tax.best_split(parts, prov)
            if np.array_equal(best, share):
                break
            share = best
        max_resid = max(max_resid, resid)
        prev_share, prev_tax = share, tax_est

        # 5. Apply the draws; save any surplus (TFSA room first).
        bal["rrsp"] -= draw["rrsp"]
        bal["pension"] -= draw["lif"]
        bal["tfsa"] -= draw["tfsa"]
        restore += draw["tfsa"]
        cost -= np.divide(cost * draw["nonreg"], bal["nonreg"], out=np.zeros((P, N)),
                          where=bal["nonreg"] > 0)
        bal["nonreg"] -= draw["nonreg"]
        per_person = surplus / P
        into_tfsa = np.minimum(per_person, room)
        room -= into_tfsa
        bal["tfsa"] += into_tfsa
        bal["nonreg"] += per_person - into_tfsa
        cost += per_person - into_tfsa
        for k in ACCOUNTS:
            np.maximum(bal[k], 0.0, out=bal[k])

        income["cpp"][t] = cpp.sum()
        income["oas"][t] = oas.sum()
        income["minimums"][t] = (min_rrif + min_lif).sum(axis=0)
        income["registered"][t] = (draw["rrsp"] + draw["lif"]).sum(axis=0)
        income["tfsa"][t] = draw["tfsa"].sum(axis=0)
        income["nonreg"][t] = draw["nonreg"].sum(axis=0)
        income["shortfall"][t] = unfunded
        tax_paid[t], saved_rec[t] = tax_est, surplus

        # 6. Growth, then contributions from whoever still works.
        r = returns[t]
        started = np.array([s is not None for s in lif_step])[:, None]
        lif_gain = np.where(started, bal["pension"] * r, 0.0)
        for k in ACCOUNTS:
            bal[k] *= 1 + r
        for i, p in enumerate(people):
            if not working[i]:
                continue
            partner_working = all(working[j] for j in range(P) if j != i)
            c = (p.contributions if partner_working or p.contributions_when_partner_retired is None
                 else p.contributions_when_partner_retired)
            bal["pension"][i] += c.get("pension", 0.0)
            bal["rrsp"][i] += c.get("rrsp", 0.0)
            to_tfsa = np.minimum(c.get("tfsa", 0.0), room[i])
            room[i] -= to_tfsa
            bal["tfsa"][i] += to_tfsa
            extra = c.get("tfsa", 0.0) - to_tfsa + c.get("nonreg", 0.0)
            bal["nonreg"][i] += extra
            cost[i] += extra

    for k in ACCOUNTS:
        balances[k][T] = bal[k].sum(axis=0)
    invest[T] = sum(balances[k][T] for k in ACCOUNTS)
    if home is not None:
        home_value[T] = home.new_value if downsized else home.value
    top = rules.top_marginal_rate(prov)
    registered = balances["rrsp"][T] + balances["pension"][T]
    gains = np.maximum(bal["nonreg"] - cost, 0.0).sum(axis=0)
    death_tax = top * registered + top * rules.CAPITAL_GAINS_INCLUSION.value * gains
    return Projection(
        years=[plan.start_year + t for t in range(T)],
        ages=[tuple(p.age + t for p in people) for t in range(T)],
        income=income, tax=tax_paid, need=need_rec, saved=saved_rec, tfsa_room=room_rec,
        investments=invest, balances=balances, home_value=home_value,
        legacy=invest[T] + home_value[T] - death_tax, death_tax=death_tax,
        retire_step=retire_step, max_residual=max_resid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_engine.py -v`
Expected: PASS (6 + 17 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/test_retirement_engine.py
git commit -m "feat(retirement): year-by-year account model with RRIF/LIF rules, withdrawal strategies and tax iteration"
```

---

### Task 6: `engine.run`, the bad-luck path and the package API

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (append), `src/stockanalysis/retirement/__init__.py`
- Test: `tests/test_retirement_engine.py` (append)

**Interfaces:**
- Consumes: `simulate`, `draw_returns`, `median_return`, `steps` (Tasks 4–5).
- Produces:
  - `engine.PlanResult(inputs, simulated: Projection, average: Projection, bad_luck: Projection, average_return: float, bad_luck_path: int)`
  - `engine.bad_luck_index(proj, pct=0.10) -> int`
  - `engine.run(plan, *, paths=None, seed=None) -> PlanResult`
  - Package exports: `from stockanalysis.retirement import run, PlanResult, Projection, PlanInputs, load_inputs`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_retirement_engine.py`

```python
# -- run & the two single futures ---------------------------------------------------

def test_bad_luck_is_the_tenth_percentile_path():
    p = plan(accounts=[Account("A", "rrsp", 2_000_000.0)], base=30_000.0, end_age=70)
    returns = np.tile(np.linspace(-0.05, 0.05, 11), (engine.steps(p), 1))   # column j = its own rate
    proj = engine.simulate(p, returns)
    assert engine.bad_luck_index(proj) == 1                                  # 2nd-worst of 11


def test_run_uses_the_median_return_and_replays_the_bad_luck_path():
    p = plan(accounts=[Account("A", "rrsp", 400_000.0)], base=30_000.0, end_age=80)
    from dataclasses import replace
    p = replace(p, returns=Returns(0.05, 0.15, 200, 3))
    result = engine.run(p)
    assert result.average_return == engine.median_return(0.05, 0.15)
    assert result.average.paths == 1 and result.simulated.paths == 200
    k = result.bad_luck_path
    for s in engine.SOURCES:
        np.testing.assert_allclose(result.bad_luck.income[s][:, 0],
                                   result.simulated.income[s][:, k], atol=2.0)


def test_package_exports_the_api():
    import stockanalysis.retirement as retirement
    assert retirement.run is engine.run and retirement.PlanInputs is PlanInputs
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_retirement_engine.py -k "bad_luck or run_uses or exports" -v`
Expected: FAIL with `AttributeError: ... has no attribute 'bad_luck_index'`

- [ ] **Step 3: Append to `engine.py` and export from the package**

Append to `src/stockanalysis/retirement/engine.py`:

```python
@dataclass
class PlanResult:
    inputs: PlanInputs
    simulated: Projection
    average: Projection
    bad_luck: Projection
    average_return: float
    bad_luck_path: int


def bad_luck_index(proj: Projection, pct: float = 0.10) -> int:
    """The path at the ``pct`` point, worst first: ranked by the year the money runs
    out (earliest = worst), then by money left at the end."""
    order = np.lexsort((proj.investments[-1], proj.first_shortfall_step))
    return int(order[int(np.floor(pct * (len(order) - 1)))])


def run(plan: PlanInputs, *, paths: int | None = None, seed: int | None = None) -> PlanResult:
    """The plan over ``paths`` simulated futures, plus the average future (a steady
    median return) and the bad-luck future (the 10th-percentile path, replayed alone)."""
    r = plan.returns
    T = steps(plan)
    R = draw_returns(r.mean, r.sd, r.paths if paths is None else paths, T,
                     r.seed if seed is None else seed)
    simulated = simulate(plan, R)
    g = median_return(r.mean, r.sd)
    average = simulate(plan, np.full((T, 1), g))
    k = bad_luck_index(simulated)
    return PlanResult(plan, simulated, average, simulate(plan, R[:, [k]]), g, k)
```

Replace `src/stockanalysis/retirement/__init__.py` with:

```python
"""Canadian retirement planner.

See docs/superpowers/specs/2026-09-29-retirement-planner-design.md. Library use::

    from stockanalysis.retirement import load_inputs, run
    result = run(load_inputs("retirement/plan.json"))
    result.simulated.success
"""
from .engine import PlanResult, Projection, run
from .inputs import PlanInputs, load_inputs

__all__ = ["PlanInputs", "PlanResult", "Projection", "load_inputs", "run"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_engine.py -v`
Expected: PASS (26 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/engine.py src/stockanalysis/retirement/__init__.py tests/test_retirement_engine.py
git commit -m "feat(retirement): run() with the average and bad-luck futures; package API"
```

---

### Task 7: `scenarios.py` — one-change what-ifs, ranked

**Files:**
- Create: `src/stockanalysis/retirement/scenarios.py`
- Test: `tests/test_retirement_scenarios.py`

**Interfaces:**
- Consumes: `engine.simulate`, `engine.draw_returns`, `engine.median_return`, `engine.steps` (Tasks 4–5); `inputs.STRATEGIES`, `inputs.STRATEGY_LABELS`, `PlanInputs` (Task 3).
- Produces:
  - `scenarios.Suggestion(key, label, success, delta, lifetime_tax, legacy)`
  - `scenarios.variants(plan) -> list[tuple[str, str, PlanInputs]]`
  - `scenarios.evaluate(plan, *, paths, seed) -> (success, lifetime_tax, legacy)`
  - `scenarios.rank(plan, *, paths, seed) -> (baseline: Suggestion, ranked: list[Suggestion])`

- [ ] **Step 1: Write the failing test** — `tests/test_retirement_scenarios.py`

```python
"""What-if suggestions: each changes one thing; ranked by change in success."""
from __future__ import annotations

from dataclasses import fields

import pytest

from stockanalysis.retirement import inputs, scenarios
from stockanalysis.retirement.inputs import PlanInputs


@pytest.fixture
def plan(tmp_path):
    return inputs.load_inputs(inputs.write_template(tmp_path / "plan.json"))


def test_variant_keys_for_the_template(plan):
    keys = {k for k, _, _ in scenarios.variants(plan)}
    assert keys == {"withdraw_proportional", "withdraw_steady_income", "spend_less",
                    "retire_later_A", "retire_later_B", "downsize_never", "downsize_70",
                    "cheaper_home"}


def test_each_variant_changes_exactly_one_field(plan):
    for key, label, variant in scenarios.variants(plan):
        changed = [f.name for f in fields(PlanInputs)
                   if getattr(variant, f.name) != getattr(plan, f.name)]
        assert len(changed) == 1, (key, changed)
        assert label


def test_spend_less_is_five_percent(plan):
    variant = dict((k, v) for k, _, v in scenarios.variants(plan))["spend_less"]
    assert variant.spending.base == pytest.approx(plan.spending.base * 0.95)


def test_rank_is_sorted_by_change_in_success(plan):
    baseline, ranked = scenarios.rank(plan, paths=30, seed=1)
    assert baseline.key == "baseline" and baseline.delta == 0.0
    deltas = [s.delta for s in ranked]
    assert deltas == sorted(deltas, reverse=True)
    for s in ranked:
        assert s.delta == pytest.approx(s.success - baseline.success)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_retirement_scenarios.py -v`
Expected: FAIL with `ImportError: cannot import name 'scenarios'`

- [ ] **Step 3: Write `src/stockanalysis/retirement/scenarios.py`**

```python
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
        if p.retire_age + 1 < plan.end_age:
            people = list(plan.people)
            people[i] = replace(p, retire_age=p.retire_age + 1)
            out.append((f"retire_later_{p.id}", f"{p.name} retires 1 year later",
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
    """(success over ``paths`` futures, average-future lifetime tax, average-future legacy)."""
    r, T = plan.returns, engine.steps(plan)
    simulated = engine.simulate(plan, engine.draw_returns(r.mean, r.sd, paths, T, seed))
    average = engine.simulate(plan, np.full((T, 1), engine.median_return(r.mean, r.sd)))
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_scenarios.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/scenarios.py tests/test_retirement_scenarios.py
git commit -m "feat(retirement): one-change what-if suggestions ranked by change in success"
```

---

### Task 8: `report.py` — the HTML dashboard

**Files:**
- Create: `src/stockanalysis/retirement/report.py`
- Test: `tests/test_retirement_report.py`

**Interfaces:**
- Consumes:
  - `engine.PlanResult`, `engine.Projection`, `engine.SOURCES`, `engine.cpp_at_65` (Tasks 4–6);
  - `scenarios.Suggestion` (Task 7);
  - `inputs.STRATEGY_LABELS` (Task 3);
  - `rules.all_rules`, `rules.is_stale`, `rules.TAX_YEAR` (Task 1);
  - `stockanalysis.report.save_report` (existing).
- Produces:
  - `report.build_report(result, baseline, suggestions, *, generated_at: str, holdings_source: str | None = None, previous: dict | None = None, today: date | None = None) -> str`
  - `report.save_report(html, path) -> str`
  - `report.write_summary(result, path) -> str`
  - `report.latest_summary(root) -> dict | None`
  - `report.age_label(ages) -> str`
  - Constants: `report.SECTION_IDS`, `report.CRITICAL`

- [ ] **Step 1: Write the failing test** — `tests/test_retirement_report.py`

```python
"""The retirement HTML report: sections, the Average/Bad-luck switch, red shortfalls."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from stockanalysis.retirement import engine, inputs, report, rules, scenarios


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    plan = inputs.load_inputs(inputs.write_template(tmp_path_factory.mktemp("r") / "plan.json"))
    result = engine.run(plan, paths=40, seed=1)
    baseline, ranked = scenarios.rank(plan, paths=20, seed=1)
    return plan, result, baseline, ranked


def _html(built, **kw):
    _, result, baseline, ranked = built
    kw.setdefault("today", dt.date(rules.TAX_YEAR, 6, 1))
    return report.build_report(result, baseline, ranked, generated_at="2026-09-29 12:00", **kw)


def test_report_has_all_sections(built):
    html = _html(built)
    for sid in report.SECTION_IDS:
        assert f'id="{sid}"' in html
    assert "Expert planning" in html and "Detailed income projection" in html


def test_income_chart_has_average_and_bad_luck_buttons(built):
    html = _html(built)
    assert "Average future" in html and "Bad-luck future" in html


def test_shortfall_layer_is_status_red(built):
    assert report.CRITICAL in _html(built)


def test_stale_rules_banner(built):
    assert "update rules.py" not in _html(built).lower()
    assert "update rules.py" in _html(built, today=dt.date(rules.TAX_YEAR + 1, 1, 5)).lower()


def test_previous_run_shows_the_change(built):
    assert "pts" in _html(built, previous={"success": 0.5})


def test_holdings_source_is_named(built):
    assert "book.xlsx, saved 2026-09-01" in _html(built, holdings_source="book.xlsx, saved 2026-09-01")


def test_summary_round_trip(built, tmp_path):
    _, result, _, _ = built
    report.write_summary(result, tmp_path / "2026-09-29_120000" / "summary.json")
    summary = report.latest_summary(tmp_path)
    assert summary["success"] == pytest.approx(result.simulated.success)
    assert report.latest_summary(tmp_path / "missing") is None


def test_single_person_household_labels_one_age(built):
    plan, _, _, _ = built
    single = replace(plan, people=plan.people[:1],
                     accounts=tuple(a for a in plan.accounts if a.owner == "A"))
    result = engine.run(single, paths=20, seed=1)
    baseline, ranked = scenarios.rank(single, paths=10, seed=1)
    html = report.build_report(result, baseline, ranked, generated_at="x",
                               today=dt.date(rules.TAX_YEAR, 1, 1))
    assert report.age_label((62,)) == "62" and report.age_label((62, 60)) == "62/60"
    assert 'id="income"' in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_retirement_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'report'`

- [ ] **Step 3: Write `src/stockanalysis/retirement/report.py`**

```python
"""Retirement report: one self-contained HTML page from a PlanResult.

``build_report`` is pure (it returns the HTML string), like
:func:`stockanalysis.report.build_full_report`. ``save_report``,
``write_summary`` and ``latest_summary`` are the only I/O.

Colours are the dataviz reference palette in its fixed categorical order. It
was validated on the light surface: every adjacent pair clears the CVD and
normal-vision floors, and the three sub-3:1 slots are relieved by the
year-by-year table view. The shortfall layer uses the reserved status-critical
red and always carries a ⚠ label.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

from ..report import save_report  # noqa: F401  (re-exported: the same writer as the other reports)
from . import engine, rules
from .engine import SOURCES, PlanResult, Projection
from .inputs import STRATEGY_LABELS

SURFACE, PAGE = "#fcfcfb", "#f9f9f7"
INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7")
CRITICAL, GOOD_TEXT, TRACK, BAND = "#d03b3b", "#006300", "#cde2fb", "rgba(42,120,214,0.15)"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
LABELS = {"earned": "Earned income", "cpp": "CPP", "oas": "OAS", "minimums": "RRIF/LIF minimums",
          "registered": "Registered", "tfsa": "TFSA", "nonreg": "Non-registered",
          "shortfall": "⚠ Shortfall"}
COLORS = {**dict(zip(SOURCES[:-1], SERIES)), "shortfall": CRITICAL}
SECTION_IDS = ("summary", "suggestions", "income", "money-left", "years", "assumptions")

_STYLE = f"""
body{{margin:0;background:{PAGE};color:{INK};font-family:{FONT}}}
header,section{{max-width:1180px;margin:0 auto;padding:12px 24px}}
h1{{font-size:1.5rem;margin:.6rem 0 .2rem}} h2{{font-size:1.1rem;margin:1.2rem 0 .5rem}}
.note,.generated{{color:{INK_2};font-size:.85rem}}
.banner{{background:#fff4e5;border:1px solid {AXIS};padding:8px 12px;border-radius:6px}}
.top{{display:grid;grid-template-columns:300px 240px 1fr;gap:16px;align-items:stretch}}
.card,.tile{{background:{SURFACE};border:1px solid rgba(11,11,11,.10);border-radius:10px;padding:12px}}
.tile .label{{color:{INK_2};font-size:.85rem}} .tile .value{{font-size:1.6rem;font-weight:600}}
.tile.hero .value{{font-size:3rem}} .tile .sub{{color:{MUTED};font-size:.8rem}}
.kpis{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
table{{border-collapse:collapse;width:100%;background:{SURFACE};font-size:.85rem}}
th,td{{padding:6px 8px;border-bottom:1px solid {GRID};text-align:right}}
th:first-child,td:first-child{{text-align:left}} td{{font-variant-numeric:tabular-nums}}
.up{{color:{GOOD_TEXT}}} .down{{color:{CRITICAL}}} .flat{{color:{INK_2}}} tr.base td{{font-weight:600}}
details summary{{cursor:pointer;font-weight:600;margin:.8rem 0}} .years{{overflow-x:auto}}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _money(x) -> str:
    x = float(x)
    return f"-C${abs(x):,.0f}" if x < 0 else f"C${x:,.0f}"


def _short(x) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"C${x / 1e6:.2f}M"
    if abs(x) >= 1e3:
        return f"C${x / 1e3:.0f}k"
    return _money(x)


def age_label(ages) -> str:
    return "/".join(str(a) for a in ages)


def _fig(fig: go.Figure, include) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=include,
                       config={"displaylogo": False, "responsive": True})


def _style_axes(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      font=dict(family=FONT, color=INK_2, size=13),
                      margin=dict(l=70, r=20, t=50, b=40),
                      hoverlabel=dict(bgcolor="white", font=dict(family=FONT, color=INK)))
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, tickfont=dict(color=MUTED),
                     tickprefix="C$", tickformat="~s")
    return fig


def success_meter(success: float, previous: float | None = None) -> go.Figure:
    delta = None if previous is None else {
        "reference": round(previous * 100), "suffix": " pts",
        "increasing": {"color": GOOD_TEXT}, "decreasing": {"color": CRITICAL}}
    fig = go.Figure(go.Indicator(
        mode="gauge+number" + ("" if delta is None else "+delta"),
        value=round(success * 100), delta=delta,
        number={"suffix": "%", "font": {"size": 48, "color": INK}},
        title={"text": "Chance the money lasts", "font": {"size": 14, "color": INK_2}},
        gauge={"axis": {"range": [0, 100], "tickcolor": MUTED, "tickfont": {"color": MUTED}},
               "bar": {"color": SERIES[0], "thickness": 0.35}, "bgcolor": TRACK, "borderwidth": 0}))
    fig.update_layout(height=240, paper_bgcolor=SURFACE, font=dict(family=FONT, color=INK_2),
                      margin=dict(l=30, r=30, t=50, b=10))
    return fig


def income_chart(avg: Projection, bad: Projection) -> go.Figure:
    """Stacked income by source per year; buttons switch the average / bad-luck future."""
    fig = go.Figure()
    for proj, visible in ((avg, True), (bad, False)):
        x, zeros = proj.years, [0] * len(proj.years)
        fig.add_trace(go.Scatter(x=x, y=zeros, mode="markers", marker=dict(opacity=0),
                                 visible=visible, showlegend=False, name="Ages",
                                 customdata=[age_label(a) for a in proj.ages],
                                 hovertemplate="Ages %{customdata}<extra></extra>"))
        for s in SOURCES:
            fig.add_trace(go.Bar(x=x, y=proj.income[s][:, 0], name=LABELS[s], visible=visible,
                                 marker=dict(color=COLORS[s], line=dict(color=SURFACE, width=1)),
                                 hovertemplate=f"{LABELS[s]}: C$%{{y:,.0f}}<extra></extra>"))
        fig.add_trace(go.Scatter(x=x, y=zeros, mode="markers", marker=dict(opacity=0),
                                 visible=visible, showlegend=False, name="Income tax",
                                 customdata=proj.tax[:, 0],
                                 hovertemplate="Income tax: C$%{customdata:,.0f}<extra></extra>"))
    per_set = len(SOURCES) + 2

    def show(average_first: bool) -> list:
        return [average_first] * per_set + [not average_first] * per_set

    fig.update_layout(
        barmode="stack", bargap=0.15, barcornerradius=4, hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.3, x=0, font=dict(color=INK_2)),
        updatemenus=[dict(type="buttons", direction="right", x=0, xanchor="left", y=1.08,
                          yanchor="bottom", showactive=True, bgcolor=SURFACE, bordercolor=AXIS,
                          font=dict(color=INK),
                          buttons=[dict(label="Average future", method="update",
                                        args=[{"visible": show(True)}]),
                                   dict(label="Bad-luck future", method="update",
                                        args=[{"visible": show(False)}])])])
    _style_axes(fig, 540)
    idx = list(range(0, len(avg.years), 5))
    fig.update_xaxes(tickvals=[avg.years[i] for i in idx], ticktext=[age_label(avg.ages[i]) for i in idx],
                     title_text="Ages", rangeslider=dict(visible=True, thickness=0.06))
    return fig


def money_left_chart(sim: Projection, plan) -> go.Figure:
    """Investments by age as a bad / typical / good band, plus the home's value."""
    ref = plan.people[0]
    x = [ref.age + t for t in range(sim.investments.shape[0])]
    p10, p50, p90 = np.percentile(sim.investments, [10, 50, 90], axis=1)
    fig = go.Figure([
        go.Scatter(x=x, y=p90, name="Good luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   hovertemplate="Good luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p10, name="Bad luck (1 in 10)", line=dict(color=SERIES[0], width=1),
                   fill="tonexty", fillcolor=BAND, hovertemplate="Bad luck: C$%{y:,.0f}<extra></extra>"),
        go.Scatter(x=x, y=p50, name="Typical", line=dict(color=SERIES[0], width=2),
                   hovertemplate="Typical: C$%{y:,.0f}<extra></extra>"),
    ])
    if plan.home is not None:
        fig.add_trace(go.Scatter(x=x, y=sim.home_value, name="Home value",
                                 line=dict(color=SERIES[1], width=2, dash="dot"),
                                 hovertemplate="Home: C$%{y:,.0f}<extra></extra>"))
    marks = [(ref.age + (p.retire_age - p.age), f"{p.name} retires") for p in plan.people]
    if plan.home is not None and plan.home.downsize_age is not None:
        marks.append((plan.home.downsize_age, "Downsize"))
    marks.append((ref.cpp_start_age, "CPP starts"))
    for i, (age, label) in enumerate(marks):
        if x[0] <= age <= x[-1]:
            fig.add_vline(x=age, line=dict(color=AXIS, width=1, dash="dash"))
            fig.add_annotation(x=age, y=1.0, yref="paper", text=label, showarrow=False,
                               yshift=10 + 14 * (i % 2), font=dict(color=MUTED, size=11))
    _style_axes(fig, 440)
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=-0.2))
    fig.update_xaxes(title_text=f"Age of {ref.name}")
    return fig


def _legacy_tile(plan, avg: Projection, bad: Projection) -> str:
    return (f"<div class='tile hero'><div class='label'>Legacy at {plan.end_age} "
            f"(after tax, home included)</div><div class='value'>{_short(avg.legacy[0])}</div>"
            f"<div class='sub'>Bad luck: {_short(bad.legacy[0])}</div></div>")


def _kpis(result: PlanResult) -> str:
    avg, bad, r = result.average, result.bad_luck, result.inputs.returns
    tiles = [
        ("Short years", f"{int(avg.shortfall_years[0])}", f"Bad luck: {int(bad.shortfall_years[0])}"),
        ("Investments at retirement", _short(avg.investments_at_retirement[0]),
         f"Bad luck: {_short(bad.investments_at_retirement[0])}"),
        ("Rate of return", f"{result.average_return:.2%}",
         f"After inflation; {r.mean:.0%} average with ups and downs"),
        ("Lifetime taxes", _short(avg.lifetime_tax[0]), "Income tax plus tax at death"),
    ]
    return "<div class='kpis'>" + "".join(
        f"<div class='tile'><div class='label'>{_esc(a)}</div><div class='value'>{_esc(b)}</div>"
        f"<div class='sub'>{_esc(c)}</div></div>" for a, b, c in tiles) + "</div>"


def _suggestions(baseline, suggestions) -> str:
    def row(s, base: bool) -> str:
        if base:
            cls, change = "flat", "—"
        else:
            cls = "up" if s.delta > 0.0005 else "down" if s.delta < -0.0005 else "flat"
            arrow = {"up": "▲", "down": "▼", "flat": "•"}[cls]
            change = f"{arrow} {s.delta * 100:+.1f} pts"
        attr = ' class="base"' if base else ""
        return (f"<tr{attr}><td>{_esc(s.label)}</td><td>{s.success:.0%}</td>"
                f"<td class='{cls}'>{_esc(change)}</td><td>{_money(s.lifetime_tax)}</td>"
                f"<td>{_money(s.legacy)}</td></tr>")

    head = ("<tr><th>Suggestion</th><th>Chance of success</th><th>Change</th>"
            "<th>Lifetime tax</th><th>Legacy</th></tr>")
    body = row(baseline, True) + "".join(row(s, False) for s in suggestions)
    return ("<p class='note'>Each line reruns the plan with one change, on the same random "
            "futures, so the differences are the change's effect.</p>"
            f"<table><thead>{head}</thead><tbody>{body}</tbody></table>")


def _year_table(proj: Projection) -> str:
    head = ["Year", "Ages", "Spending", "Tax", *[LABELS[s] for s in SOURCES], "Saved",
            "RRSP/RRIF", "Pension/LIF", "TFSA", "Non-registered", "Total"]
    rows = []
    for t, year in enumerate(proj.years):
        cells = [year, age_label(proj.ages[t]), _money(proj.need[t, 0]), _money(proj.tax[t, 0]),
                 *[_money(proj.income[s][t, 0]) for s in SOURCES], _money(proj.saved[t, 0]),
                 *[_money(proj.balances[k][t, 0]) for k in ("rrsp", "pension", "tfsa", "nonreg")],
                 _money(proj.investments[t, 0])]
        rows.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    return ("<div class='years'><table><thead><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in head)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _rule_value(value) -> str:
    text = str(value).replace("inf", "∞")
    return text if len(text) <= 140 else text[:137] + "…"


def _assumptions(plan, result: PlanResult, holdings_source: str | None) -> str:
    people = "".join(
        f"<tr><td>{_esc(p.name)}</td><td>{p.age}</td><td>{p.retire_age}</td>"
        f"<td>{p.cpp_start_age}</td><td>{p.oas_start_age}</td><td>{_money(engine.cpp_at_65(p))}</td>"
        f"<td>{p.years_in_canada_at_65:g}</td><td>{p.rrif_start_age}</td>"
        f"<td>{_esc(p.lif_start_age if p.lif_start_age is not None else 'at retirement (50+)')}</td></tr>"
        for p in plan.people)
    s, h, r = plan.spending, plan.home, plan.returns
    facts = [
        ("Withdrawal order", STRATEGY_LABELS[plan.withdrawal.strategy]),
        ("Returns", f"{r.mean:.1%} average, {r.sd:.0%} yearly swings; typical "
                    f"{result.average_return:.2%} a year after inflation"),
        ("Spending", f"{_money(s.base)} a year after tax; slow-go from {s.slow_go_age} "
                     f"({s.slow_go_share:.0%}), no-go from {s.no_go_age} ({s.no_go_share:.0%}) "
                     f"plus {_money(s.care)} care"),
        ("Bad-market rule", f"cut {s.bad_market_cut:.0%} when investments are below "
                            f"{s.bad_market_trigger:.0%} of their value on retirement day"),
        ("Home", "none" if h is None else (
            f"{_money(h.value)}; downsize at {h.downsize_age} to {_money(h.new_value)}"
            if h.downsize_age is not None else f"{_money(h.value)}; never sold")),
        ("Balances from", holdings_source or "plan.json"),
        ("Futures simulated", f"{result.simulated.paths:,}"),
    ]
    rules_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_esc(_rule_value(rule.value))}</td><td>{rule.year}</td>"
        f"<td><a href='{_esc(rule.source)}'>source</a></td></tr>" for name, rule in rules.all_rules())
    return (
        "<table><thead><tr><th>Person</th><th>Age</th><th>Retires</th><th>CPP from</th>"
        "<th>OAS from</th><th>CPP at 65 (yearly)</th><th>Years in Canada at 65</th>"
        f"<th>RRIF from</th><th>LIF from</th></tr></thead><tbody>{people}</tbody></table>"
        "<table><tbody>" + "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in facts)
        + "</tbody></table><h2>Canadian rules used</h2>"
        "<table><thead><tr><th>Rule</th><th>Value</th><th>Year</th><th>Official source</th></tr>"
        f"</thead><tbody>{rules_rows}</tbody></table>")


def build_report(result: PlanResult, baseline, suggestions, *, generated_at: str,
                 holdings_source: str | None = None, previous: dict | None = None,
                 today: dt.date | None = None) -> str:
    """The whole page as a string. ``previous`` is the last run's summary, for the change."""
    plan, sim, avg, bad = result.inputs, result.simulated, result.average, result.bad_luck
    today = today or dt.date.today()
    banner = ""
    if rules.is_stale(today.year):
        banner = (f"<p class='banner'>⚠ The tax and benefit rules are for {rules.TAX_YEAR}. "
                  f"Update rules.py for {today.year} from the official pages it cites.</p>")
    prev = previous.get("success") if previous else None
    top = (f"<div class='top'><div class='card'>{_fig(success_meter(sim.success, prev), 'cdn')}</div>"
           f"{_legacy_tile(plan, avg, bad)}{_kpis(result)}</div>")
    sections = [
        ("summary", "", top),
        ("suggestions", "Expert planning", _suggestions(baseline, suggestions)),
        ("income", "Detailed income projection",
         "<p class='note'>Each bar is one year's spending plus income tax, by where the money "
         "comes from. Switch to the bad-luck future (the 1-in-10 bad run of returns) to see "
         "short years in red.</p>" + _fig(income_chart(avg, bad), False)),
        ("money-left", "Money left",
         "<p class='note'>Investments only, in today's dollars; the home is the dotted line.</p>"
         + _fig(money_left_chart(sim, plan), False)),
        ("years", "", "<details><summary>Year-by-year table (average future)</summary>"
                      f"{_year_table(avg)}</details>"),
        ("assumptions", "Assumptions &amp; rules", _assumptions(plan, result, holdings_source)),
    ]
    body = "".join(f'<section id="{sid}">' + (f"<h2>{title}</h2>" if title else "") + content
                   + "</section>" for sid, title, content in sections)
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Retirement plan</title><style>{_STYLE}</style></head><body>"
            f"<header><h1>Retirement plan</h1><p class='generated'>Generated {_esc(generated_at)} · "
            f"{sim.paths:,} simulated futures · all amounts in today's dollars (CAD)</p>{banner}"
            f"</header>{body}</body></html>")


def write_summary(result: PlanResult, path) -> str:
    """The run's headline numbers, so the next run can show the change."""
    avg = result.average
    data = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
            "success": result.simulated.success, "paths": result.simulated.paths,
            "legacy": float(avg.legacy[0]), "lifetime_tax": float(avg.lifetime_tax[0]),
            "investments_at_retirement": float(avg.investments_at_retirement[0])}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(path)


def latest_summary(root) -> dict | None:
    """The newest ``<root>/<timestamp>/summary.json``, or None."""
    root = Path(root)
    found = sorted(root.glob("*/summary.json")) if root.is_dir() else []
    return json.loads(found[-1].read_text(encoding="utf-8")) if found else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_retirement_report.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Render the report and look at it**

This is dataviz step 7: check the rendered page for label collisions, geometry and overflow.

```bash
python - <<'EOF'
import datetime as dt, tempfile, webbrowser
from pathlib import Path
from stockanalysis.retirement import engine, inputs, report, scenarios
d = Path(tempfile.mkdtemp())
plan = inputs.load_inputs(inputs.write_template(d / "plan.json"))
result = engine.run(plan, paths=2000)
base, ranked = scenarios.rank(plan, paths=500, seed=7)
out = report.save_report(report.build_report(result, base, ranked, generated_at="preview"), d / "r.html")
webbrowser.open(f"file://{out}"); print(out)
EOF
```

Expected: the page opens. Check that:
- the meter shows a percentage;
- the bars stack from Earned income through Non-registered, with a red ⚠ Shortfall layer on the bad-luck switch if any year falls short;
- the x-axis shows the "50/48"-style ages;
- the legend doesn't overlap the range slider;
- the money-left band sits under the median line;
- the vline labels don't collide.

Fix spacing (legend `y`, the `yshift` values) if they do.

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/report.py tests/test_retirement_report.py
git commit -m "feat(retirement): HTML dashboard with success meter, suggestions, stacked income and money-left charts"
```

---

### Task 9: The `stock-analysis retire` command, and docs

**Files:**
- Create: `src/stockanalysis/retirement/cli.py`
- Modify:
  - `src/stockanalysis/cli.py:279-281` (`build_parser`)
  - `src/stockanalysis/cli.py:452-458` (`main`, after the `thesis` branch)
  - `CLAUDE.md`
- Test: `tests/test_cli_retire.py`

**Interfaces:**
- Consumes:
  - `inputs.load_inputs`, `inputs.write_template`, `inputs.with_holdings` (Task 3);
  - `engine.run` (Task 6);
  - `scenarios.rank` (Task 7);
  - `report.build_report`, `report.save_report`, `report.write_summary`, `report.latest_summary` (Task 8);
  - `holdings.load` (existing);
  - `config.DEFAULT_RETIREMENT_INPUTS`, `config.DEFAULT_RETIREMENT_OUT` (Task 1).
- Produces: `retirement.cli.add_parser(sub)` and `retirement.cli.dispatch(args) -> int`. The CLI takes `--inputs`, `--holdings`, `--out`, `--paths`, `--scenario-paths` (default 2000), `--seed` and `--init`.

- [ ] **Step 1: Write the failing test** — `tests/test_cli_retire.py`

```python
"""`stock-analysis retire` through the real argparse dispatch; offline, invented numbers."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stockanalysis import cli, holdings
from stockanalysis.retirement import inputs

FAST = ["--paths", "40", "--scenario-paths", "20"]


def _plan(tmp_path, *, balances=True) -> Path:
    d = copy.deepcopy(inputs.TEMPLATE)
    if not balances:
        d.pop("balances")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    return path


def test_init_writes_and_refuses_to_overwrite(tmp_path, capsys):
    path = tmp_path / "plan.json"
    assert cli.main(["retire", "--init", "--inputs", str(path)]) == 0 and path.exists()
    assert cli.main(["retire", "--init", "--inputs", str(path)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_missing_plan_suggests_init(tmp_path, capsys):
    assert cli.main(["retire", "--inputs", str(tmp_path / "none.json")]) == 1
    assert "--init" in capsys.readouterr().err


def test_pinned_balances_write_report_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(holdings, "load", lambda *a, **k: pytest.fail("holdings must not be read"))
    out = tmp_path / "out"
    rc = cli.main(["retire", "--inputs", str(_plan(tmp_path)), "--out", str(out), *FAST])
    assert rc == 0
    [run_dir] = list(out.iterdir())
    assert (run_dir / "retirement_report.html").exists() and (run_dir / "summary.json").exists()
    printed = capsys.readouterr().out
    assert "Chance the money lasts to 95" in printed and "Report:" in printed


def test_reads_holdings_when_plan_has_no_balances(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        [["Partner A RRSP", "cash", np.nan, np.nan, 400_000.0, 400_000.0],
         ["Partner B TFSA", "cash", np.nan, np.nan, 90_000.0, 90_000.0]],
        columns=["account", "kind", "shares", "cost", "value", "value_cad"])
    monkeypatch.setattr(holdings, "load", lambda path=None: {
        "holdings": frame, "path": Path("book.xlsx"), "saved_at": dt.datetime(2026, 9, 1, 8, 0)})
    out = tmp_path / "out"
    assert cli.main(["retire", "--inputs", str(_plan(tmp_path, balances=False)),
                     "--out", str(out), *FAST]) == 0
    [run_dir] = list(out.iterdir())
    assert "book.xlsx, saved 2026-09-01 08:00" in (run_dir / "retirement_report.html").read_text()


def test_no_balances_and_no_holdings_names_both(tmp_path, monkeypatch, capsys):
    def missing(path=None):
        raise FileNotFoundError("No holdings file at x")
    monkeypatch.setattr(holdings, "load", missing)
    rc = cli.main(["retire", "--inputs", str(_plan(tmp_path, balances=False)),
                   "--out", str(tmp_path / "out"), *FAST])
    assert rc == 1
    err = capsys.readouterr().err
    assert "balances" in err and "--holdings" in err
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_cli_retire.py -v`
Expected: FAIL with `SystemExit: 2` (argparse: invalid choice `'retire'`)

- [ ] **Step 3: Write `src/stockanalysis/retirement/cli.py` and wire it in**

`src/stockanalysis/retirement/cli.py`:

```python
"""The ``stock-analysis retire`` subcommand: a thin CLI over the retirement API.

Kept next to the logic it drives (like ``thesis/cli.py``). The main CLI wires it
in with :func:`add_parser` and :func:`dispatch`.

Balances come from ``--holdings`` if given, then plan.json's ``balances``, then
the default holdings file (``holdings.load()``).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .. import config, holdings
from . import engine, inputs, report, scenarios


def add_parser(sub) -> None:
    p = sub.add_parser("retire", help="Canadian retirement plan: chance of success, legacy, "
                                      "yearly income by source.")
    p.add_argument("--inputs", default=None, help="plan.json (default: retirement/plan.json).")
    p.add_argument("--holdings", default=None,
                   help="Holdings file for live balances (overrides plan.json 'balances').")
    p.add_argument("--out", default=None, help="Output root (default: retirement/output/).")
    p.add_argument("--paths", type=int, default=None,
                   help="Simulated futures (default: plan.json returns.paths).")
    p.add_argument("--scenario-paths", type=int, default=2000,
                   help="Futures per what-if suggestion (default 2000).")
    p.add_argument("--seed", type=int, default=None, help="Random seed (default: plan.json).")
    p.add_argument("--init", action="store_true", help="Write a starter plan.json and exit.")


def _with_balances(plan, holdings_path):
    """Pick the balances and say where they came from."""
    if holdings_path is None and plan.accounts:
        return plan, "plan.json balances"
    try:
        book = holdings.load(holdings_path)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"{e}; add 'balances' to plan.json or pass --holdings") from e
    source = f"{Path(book['path']).name}, saved {book['saved_at']:%Y-%m-%d %H:%M}"
    return inputs.with_holdings(plan, book["holdings"]), source


def dispatch(args) -> int:
    path = Path(args.inputs) if args.inputs else config.DEFAULT_RETIREMENT_INPUTS
    if args.init:
        inputs.write_template(path)
        print(f"Wrote a starter plan with example values: {path}")
        return 0
    plan, source = _with_balances(inputs.load_inputs(path), args.holdings)
    seed = plan.returns.seed if args.seed is None else args.seed
    result = engine.run(plan, paths=args.paths, seed=seed)
    baseline, ranked = scenarios.rank(plan, paths=args.scenario_paths, seed=seed)
    root = Path(args.out) if args.out else config.DEFAULT_RETIREMENT_OUT
    previous = report.latest_summary(root)
    now = dt.datetime.now()
    run_dir = root / now.strftime("%Y-%m-%d_%H%M%S")
    html_doc = report.build_report(result, baseline, ranked, generated_at=now.strftime("%Y-%m-%d %H:%M"),
                                   holdings_source=source, previous=previous)
    out = report.save_report(html_doc, run_dir / "retirement_report.html")
    report.write_summary(result, run_dir / "summary.json")
    avg = result.average
    print(f"Chance the money lasts to {plan.end_age}: {result.simulated.success:.0%}")
    print(f"Legacy (average future): C${avg.legacy[0]:,.0f}")
    print(f"Lifetime taxes (average future): C${avg.lifetime_tax[0]:,.0f}")
    print(f"Report: {out}")
    return 0
```

In `src/stockanalysis/cli.py` `build_parser`, after `thesis_cli.add_parser(sub)`:

```python
    from .retirement import cli as retire_cli
    retire_cli.add_parser(sub)
```

In `main`, directly after the `if args.command == "thesis": ...` block and before the final `return 1`:

```python
    if args.command == "retire":
        from .retirement import cli as retire_cli
        try:
            return retire_cli.dispatch(args)
        except (ValueError, FileNotFoundError, FileExistsError) as e:
            print(f"Retire command failed: {e}", file=sys.stderr)
            return 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli_retire.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Check the runtime at full size**

```bash
cd "$(mktemp -d)" && time stock-analysis retire --init --inputs plan.json && time stock-analysis retire --inputs plan.json --out out
```

Expected: the second command finishes in under 60 s (10,000 futures plus 8 suggestions at 2,000). If it's slower, profile with `python -m cProfile -s cumtime -m stockanalysis.cli retire --inputs plan.json --out out | head -30`. The usual cause is `tax.best_split` being called in years where no one is 65 or older; that path is guarded in `simulate`, so check the guard.

- [ ] **Step 6: Update `CLAUDE.md`**

(a) After the `### Household risk (\`stock-analysis risk\`)` section (it ends just before `### Broad-universe research`), insert:

````markdown
### Retirement planner (`stock-analysis retire`)
```bash
stock-analysis retire --init       # writes retirement/plan.json with example values
stock-analysis retire              # -> retirement/output/<ts>/retirement_report.html + summary.json
```
Projects a Canadian household year by year (`stockanalysis.retirement`) under federal + Alberta tax, CPP, OAS, RRIF, TFSA and Alberta LIF rules, over 10,000 return paths plus an "average" and a "bad-luck" (10th-percentile) future. The report shows the chance the money lasts, the after-tax legacy, a stacked income-by-source chart, one-change what-ifs ranked by effect, the money left by age, a year-by-year table and every rule with its official source. Balances: `--holdings` → plan.json `balances` → the holdings workbook, sorted by account name (RRSP / TFSA / RESP / LIRA-LIF keywords plus plan.json overrides; an unsorted account stops the run rather than being dropped). `retirement/` is gitignored — the owner's plan and reports never enter git.
````

(b) In the module-map table, after the `thesis/` row, add:

```markdown
| `retirement/` | **Canadian retirement planner** — `rules` (every statutory value as `Rule(value, year, source)`: federal/Alberta tax, OAS, CPP, RRIF factors, TFSA, Alberta LIF), `tax` (vectorized person tax, household tax, 5%-step pension-split search), `inputs` (`plan.json` + holdings → validated `PlanInputs`), `engine` (year-by-year accounts over N paths; `run` adds the average + bad-luck futures), `scenarios` (one-change what-ifs on common random numbers), `report` (self-contained HTML), `cli` (`stock-analysis retire`). Separate from `pipeline.run`. |
```

(c) At the end of "Conventions that are easy to get wrong", add:

```markdown
- **Canadian rule values live only in `retirement/rules.py`,** each a `Rule(value, year, source)` read from the official CRA / Service Canada / Alberta page — never from memory. Update them every January (CRA's T4127 and TD1 forms carry the indexed amounts; OAS changes quarterly); the report shows a banner once `TAX_YEAR` is behind the calendar. The planner works in today's dollars, so rules are held flat for later years. **Plain RRSP withdrawals are not eligible pension income** — only RRIF/LIF payments at 65+ get the pension credit and pension splitting — which is why `rrif_start_age` defaults to 65.
```

(d) In the "Test suite (fully offline)" bullet, change `and the whole \`thesis/\` subpackage` so the sentence names the retirement planner too:

```markdown
  monkeypatched `ingest.fetch_bulk_prices`), the whole `retirement/` planner (rules citations,
  hand-worked federal + Alberta tax, the RRIF/LIF/TFSA account model, what-ifs, the HTML
  report, the `retire` CLI), and the whole `thesis/` subpackage
```

- [ ] **Step 7: Run the whole suite**

Run: `pytest -q`
Expected: all tests pass (the existing suite plus the 7 new files).

- [ ] **Step 8: Commit**

```bash
git add src/stockanalysis/retirement/cli.py src/stockanalysis/cli.py tests/test_cli_retire.py CLAUDE.md
git commit -m "feat(retirement): stock-analysis retire command; document the planner"
```

---

### Task 10: The owner's plan (private, never committed)

**Files:**
- Create: `retirement/plan.json`. This file is gitignored: **never `git add` it.**

**Interfaces:**
- Consumes: the `stock-analysis retire` command (Task 9).

- [ ] **Step 1: Write the owner's plan.json**

Run `stock-analysis retire --init` to write `retirement/plan.json`. Then replace the example values with the owner's actual plan, taken from `retirement/retirement_plan.md` and the planning conversation. This document deliberately does **not** repeat them. Specifically:
- ages, retirement ages and years in Canada;
- the pension contribution and the yearly saving split;
- spending, with its "kids leave home" change;
- stages and care;
- the home and the downsizing age;
- `holdings.owners` keywords and explicit `holdings.accounts` entries for every account the keywords can't sort. Those are the pension account, the employer-stock account and the company accounts.

Delete `"balances"`, so the live holdings workbook is used.

- [ ] **Step 2: Run it and look at the report**

```bash
stock-analysis retire
git status --short retirement/    # must print nothing: the folder is ignored
```

Expected:
- the run prints the success %, legacy, lifetime taxes and the report path;
- `git status` shows nothing under `retirement/`.

Open the report. Compare it with `retirement/retirement_plan.md` (one pot, flat 15% tax), then explain the differences to the owner: real tax brackets, RRIF minimums, LIF limits on the pension, and pension splitting.
