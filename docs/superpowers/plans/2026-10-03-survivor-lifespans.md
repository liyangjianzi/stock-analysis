# Survivor Years and Lifespan as a Range — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each simulated future draws when each person dies from Canadian cohort
life tables, and a couple's plan continues for the survivor after the first death
under the spousal-rollover and CPP-survivor rules.

**Architecture:** A per-path "alive" mask inside `engine.simulate`. Death ages are
a `(P, N)` int array drawn once (new `mortality.py`) beside the return draws, and
every comparison shares them. With no `deaths` argument everyone lives through the
whole horizon, which reproduces today's engine exactly; that's the regression
anchor. Survivor rules (rollover, CPP survivor pension, no splitting, survivor
spending) switch on per path.

**Tech Stack:** Python 3, numpy, plotly, pytest; stdlib `http.server` GUI with a
vanilla-JS page.

**Spec:** `docs/superpowers/specs/2026-10-03-survivor-lifespans-design.md`

## Global Constraints

- Every statutory or official value lives in `rules.py` (or, for the life table, `mortality.py`) as `Rule(value, year, source)` with the official URL. Never hardcode one anywhere else, including the GUI page.
- The repo is public: tests use invented households only (`inputs.TEMPLATE` or the `person()`/`plan()` helpers). Never put plan values in tracked files.
- `report.py` stays pure (it returns a string); no `print`/`fig.show()` in the package core.
- The engine stays vectorized over paths; no per-path Python loops.
- Comparisons share futures: every caller that compares plans draws returns **and** deaths once with the same seed.
- Run tests with `source venv/bin/activate && pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`; use small path counts (40 runs / 20 scenarios).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Spec deviations (decided while planning, from measured facts)

1. **Deterministic views keep `T = steps(plan)`.** The spec says every `Projection` shares the 110 horizon. Instead, only the lifespan runs use `life_steps(plan)`. The average and bad-luck futures keep `steps(plan)`, the "plan to" horizon the charts and table already use. That keeps every existing test and chart shape unchanged; the money-left chart cuts the simulated band at `end_age`.
2. **The largest death age is `mortality.OMEGA = 111`.** The table's last row is "110 and over" with q = 1, so someone who dies during age 110 has death age 111 (the death-age convention in spec Part 2).
3. **No coarse-to-fine grid in the optimizer.** It was measured: `simulate` with 300 paths over 62 years takes 0.30 s against 0.25 s for one path, so 300 death draws cost about one candidate's worth. The full grid stays.
4. **The death benefit is untaxed cash to the survivor.** CRA has it filed on the estate's T3 at graduated rates, about no tax on C$2,500, so it is added to the survivor's non-registered account, untaxed. It isn't taxed as the deceased's income, as the spec had it.
5. **The CPP survivor pension uses the deceased's unadjusted CPP at 65** (`engine.cpp_at_65`), whether or not they had started it. That's what "the contributor's retirement pension" means in Service Canada's formula.
6. **Improvement uses the ultimate rates from the table year on.** The 32nd CPP report reaches them in 2039; using them from 2022 slightly understates improvement in 2022–2039. Documented in `mortality.py`.

## Official values (fetched 2026-10-03)

| Value | Number | Source |
|---|---|---|
| Alberta life table, qx by single age 0–110 and sex, 2021/2023 (centre year 2022) | below, in `mortality.QX` | Statistics Canada table 13-10-0114-01, `https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1310011401` |
| Ultimate mortality improvement, both sexes | 1.0%/yr under 90, 0.6% for 90–94, 0.2% for 95+ (reached 2039) | 32nd CPP Actuarial Report (revised), `https://www.osfi-bsif.gc.ca/en/oca/actuarial-reports/actuarial-report-32nd-canada-pension-plan-revised-version` |
| CPP survivor's pension shares | 60% of the contributor's pension at 65+; flat rate + 37.5% under 65 | `https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-survivor-pension.html` |
| CPP 2026 amounts | flat rate C$238.17/month; combined survivor + retirement maximum C$1,531.56/month | `https://www.canada.ca/en/employment-social-development/programs/pensions/pension/statistics/2026-quarterly-july-september.html` |
| CPP death benefit | C$2,500 one-time | `https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-death-benefit.html` |

## Review Focus

1. **A person already very old at the start** (e.g. age 105): the draws must stay ≤ `OMEGA` and `life_steps` must stay ≥ 1. Pinned in Task 1.
2. **A widow(er) under 65 at the first death**: must get the flat rate + 37.5%, not 60%. Pinned in Task 4.
3. **A one-person plan**: lifespans only; `survivor_share` must be unused, and the estate valued at that person's death. Pinned in Task 4.
4. **A worker who dies before retiring while the partner is already retired**: salary and contributions stop, and the need must then be funded from the accounts instead of earned income. Pinned in Task 3.
5. **`sex` omitted or misspelled**: omitted uses the average table; a misspelling is a `PlanError` on `people[i].sex`, not a crash deep in the engine. Pinned in Task 2.

---

### Task 1: Official rules and the `mortality` module

**Files:**
- Modify: `src/stockanalysis/retirement/rules.py` (CPP dict, URL constants)
- Create: `src/stockanalysis/retirement/mortality.py`
- Test: `tests/test_retirement_mortality.py` (new), `tests/test_retirement_rules.py`

**Interfaces:**
- Produces: `rules.CPP["survivor"]` (`Rule({"share_65": 0.60, "share_under_65": 0.375, "flat_monthly": 238.17, "combined_max_monthly": 1_531.56}, 2026, ...)`), `rules.CPP["death_benefit"]` (`Rule(2_500.0, 2026, ...)`).
- Produces: `mortality.OMEGA: int = 111`, `mortality.TABLE_YEAR: int = 2022`, `mortality.QX: Rule` (value `{"female": tuple[111], "male": tuple[111]}`), `mortality.IMPROVEMENT: Rule` (value `{"under_90": 0.010, "90_94": 0.006, "95_plus": 0.002}`), `mortality.qx(sex: str | None, age: int, year: int) -> float`, `mortality.death_cdf(person, start_year) -> np.ndarray`, `mortality.draw_death_ages(people, start_year: int, paths: int, seed: int) -> np.ndarray (P, N) int`, `mortality.median_death_age(person, start_year: int) -> int`, `mortality.SEXES = ("female", "male")`.
- Death-age convention: the age reached on the January 1 after the last year lived. Dying during the year at age `a` gives `a + 1`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retirement_mortality.py`:

```python
"""Lifespans from the Statistics Canada Alberta life table with CPP-report
mortality improvement. Offline; invented people."""
from __future__ import annotations

import numpy as np
import pytest

from stockanalysis.retirement import mortality, rules
from stockanalysis.retirement.inputs import Person


def person(age=50, sex="female", **kw) -> Person:
    return Person(id="A", name="A", age=age, retire_age=max(age, 60), sex=sex, **kw)


def test_table_covers_ages_0_to_110_and_ends_certain():
    for sex in mortality.SEXES:
        q = mortality.QX.value[sex]
        assert len(q) == mortality.OMEGA == 111
        assert q[-1] == 1.0 and all(0 < x <= 1 for x in q)
    assert mortality.QX.value["female"][65] == 0.00763      # spot value from table 13-10-0114-01
    assert mortality.QX.value["male"][65] == 0.01226


def test_improvement_is_applied_per_year_from_the_table_year():
    base = mortality.QX.value["male"][70]
    assert mortality.qx("male", 70, mortality.TABLE_YEAR) == base
    assert mortality.qx("male", 70, mortality.TABLE_YEAR + 10) == pytest.approx(base * 0.99 ** 10)
    assert mortality.qx("male", 92, mortality.TABLE_YEAR + 5) == pytest.approx(
        mortality.QX.value["male"][92] * 0.994 ** 5)
    assert mortality.qx("male", 110, 2080) == 1.0


def test_no_sex_uses_the_average_of_both_tables():
    f, m = mortality.qx("female", 80, 2030), mortality.qx("male", 80, 2030)
    assert mortality.qx(None, 80, 2030) == pytest.approx((f + m) / 2)


def test_draws_are_seeded_conditional_on_age_and_capped():
    people = (person(50, "female"), person(105, "male"))
    a = mortality.draw_death_ages(people, 2026, 2_000, seed=3)
    b = mortality.draw_death_ages(people, 2026, 2_000, seed=3)
    assert a.shape == (2, 2_000) and a.dtype.kind == "i"
    np.testing.assert_array_equal(a, b)
    assert a[0].min() >= 51 and a[1].min() >= 106                 # alive through this year
    assert a.max() <= mortality.OMEGA


def test_sample_median_matches_median_death_age():
    p = person(48, "male")
    draws = mortality.draw_death_ages((p,), 2026, 20_000, seed=1)[0]
    assert abs(np.median(draws) - mortality.median_death_age(p, 2026)) <= 1


def test_women_live_longer_and_improvement_lengthens_life():
    f, m = person(50, "female"), person(50, "male")
    assert mortality.median_death_age(f, 2026) > mortality.median_death_age(m, 2026)
    later = mortality.median_death_age(m, 2060)
    assert later >= mortality.median_death_age(m, 2026)
```

Append to `tests/test_retirement_rules.py`:

```python
def test_cpp_survivor_rules_are_consistent_with_the_2026_maximums():
    s = rules.CPP["survivor"].value
    max65 = rules.CPP["max_monthly_at_65"].value
    assert s["share_65"] * max65 == pytest.approx(904.59, abs=0.01)        # published 65+ maximum
    assert s["flat_monthly"] + s["share_under_65"] * max65 == pytest.approx(803.54, abs=0.01)
    assert rules.CPP["death_benefit"].value == 2_500.0
```

Add `import pytest` to the top of `tests/test_retirement_rules.py` (it imports only `rules` today).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_mortality.py tests/test_retirement_rules.py`
Expected: FAIL. `mortality` doesn't import, `Person` has no `sex`, and `KeyError: 'survivor'`.

- [ ] **Step 2b: Add the `sex` field to `Person`**

The mortality functions read `person.sex`, so add it now as the **last** field of `Person` in `inputs.py`, after `contributions_when_partner_retired`. That keeps every positional use working; Task 2 validates it.

```python
    sex: str | None = None      # "female" / "male" for the life table; None averages the two
```

- [ ] **Step 3: Add the CPP rules**

In `rules.py`, next to the other URL constants (after `_CPP_HOW_MUCH`):

```python
_CPP_SURVIVOR = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-survivor-pension.html"
_CPP_MAXIMUMS = ("https://www.canada.ca/en/employment-social-development/programs/pensions/pension/"
                 "statistics/2026-quarterly-july-september.html")
_CPP_DEATH = "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-death-benefit.html"
```

Add to the `CPP` dict, after `"dropout"`:

```python
    # Survivor's pension: 60% of the contributor's pension at 65+, or a flat rate plus
    # 37.5% under 65; with the survivor's own retirement pension the two together are
    # capped. Shares from the survivor page, 2026 amounts from the ESDC maximums table.
    "survivor": Rule({"share_65": 0.60, "share_under_65": 0.375, "flat_monthly": 238.17,
                      "combined_max_monthly": 1_531.56}, 2026, _CPP_MAXIMUMS),
    "survivor_rules": Rule("60% at 65+; flat rate + 37.5% under 65; combined maximum with "
                           "your own retirement pension", 2026, _CPP_SURVIVOR),
    "death_benefit": Rule(2_500.0, 2026, _CPP_DEATH),
```

- [ ] **Step 4: Create `mortality.py`**

```python
"""Lifespans: when each person dies, from Canadian cohort life tables.

The base table is Statistics Canada's complete life table for Alberta, 2021/2023
(three-year estimate, centre year 2022), death probability ``qx`` by single age
0-110 and sex. Death rates then fall every year at the 32nd CPP Actuarial
Report's ultimate improvement rates (1.0% a year under 90, 0.6% at 90-94, 0.2% at
95+, for both sexes). The report reaches those rates in 2039; applying them from
2022 on slightly understates improvement before then.

A **death age** is the age reached on the January 1 after the last year lived:
dying during the year at age ``a`` gives ``a + 1``. The engine counts a person
alive in a year while their age is below it.

Pure: no I/O.
"""
from __future__ import annotations

import numpy as np

from .rules import Rule

OMEGA = 111                 # largest death age: the table's last row is "110 and over", q = 1
TABLE_YEAR = 2022           # centre of the 2021/2023 estimate
SEXES = ("female", "male")
_QX_SOURCE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1310011401"
_IMPROVEMENT_SOURCE = ("https://www.osfi-bsif.gc.ca/en/oca/actuarial-reports/"
                       "actuarial-report-32nd-canada-pension-plan-revised-version")

QX = Rule({
    "female": (
        0.00458, 0.0003, 0.00021, 0.00016, 0.00012, 0.0001, 9e-05, 9e-05, 9e-05, 9e-05,
        0.00011, 0.00012, 0.00014, 0.00016, 0.00019, 0.00024, 0.00029, 0.00036, 0.00044,
        0.00052, 0.00061, 0.00069, 0.00075, 0.00079, 0.00082, 0.00083, 0.00084, 0.00086,
        0.00089, 0.00091, 0.00095, 0.00098, 0.00101, 0.00104, 0.00106, 0.00109, 0.00111,
        0.00114, 0.00118, 0.00122, 0.00127, 0.00133, 0.00139, 0.00146, 0.00154, 0.00163,
        0.00173, 0.00184, 0.00196, 0.00209, 0.00225, 0.00241, 0.0026, 0.0028, 0.00302,
        0.00327, 0.00354, 0.00383, 0.00416, 0.00452, 0.00491, 0.00535, 0.00584, 0.00637,
        0.00697, 0.00763, 0.00837, 0.00919, 0.0101, 0.01112, 0.01225, 0.01352, 0.01495,
        0.01654, 0.01833, 0.02033, 0.02259, 0.02513, 0.028, 0.03123, 0.03487, 0.039,
        0.04367, 0.04897, 0.05498, 0.06181, 0.06958, 0.07843, 0.08852, 0.10004, 0.11321,
        0.1279, 0.14381, 0.16094, 0.17926, 0.20047, 0.22118, 0.24291, 0.2655, 0.28876,
        0.31247, 0.3364, 0.36029, 0.38391, 0.40702, 0.42941, 0.45089, 0.47131, 0.49056,
        0.50854, 1),
    "male": (
        0.00524, 0.00037, 0.00025, 0.00018, 0.00014, 0.00012, 0.0001, 9e-05, 9e-05, 0.0001,
        0.00011, 0.00012, 0.00015, 0.00018, 0.00024, 0.00032, 0.00044, 0.00057, 0.0007,
        0.00084, 0.00099, 0.00113, 0.00127, 0.00139, 0.00149, 0.00158, 0.00166, 0.00175,
        0.00183, 0.00192, 0.002, 0.00208, 0.00215, 0.00221, 0.00226, 0.00231, 0.00235,
        0.00239, 0.00244, 0.00249, 0.00254, 0.00261, 0.00268, 0.00277, 0.00288, 0.003,
        0.00315, 0.00331, 0.0035, 0.00371, 0.00396, 0.00423, 0.00453, 0.00485, 0.0052,
        0.00559, 0.00601, 0.00648, 0.00698, 0.00754, 0.00815, 0.00882, 0.00956, 0.01038,
        0.01127, 0.01226, 0.01336, 0.01457, 0.01591, 0.01739, 0.01903, 0.02086, 0.0229,
        0.02516, 0.02768, 0.03049, 0.03363, 0.03713, 0.04106, 0.04545, 0.05038, 0.05592,
        0.06214, 0.06914, 0.07703, 0.08592, 0.09596, 0.1073, 0.12014, 0.13468, 0.15118,
        0.16935, 0.18872, 0.20921, 0.23072, 0.25395, 0.27634, 0.29927, 0.32253, 0.3459,
        0.36915, 0.39205, 0.41439, 0.43598, 0.45666, 0.4763, 0.49479, 0.51207, 0.5281,
        0.54287, 1),
}, TABLE_YEAR, _QX_SOURCE)

IMPROVEMENT = Rule({"under_90": 0.010, "90_94": 0.006, "95_plus": 0.002}, 2024, _IMPROVEMENT_SOURCE)


def _improvement(age: int) -> float:
    imp = IMPROVEMENT.value
    return imp["under_90"] if age < 90 else imp["90_94"] if age < 95 else imp["95_plus"]


def qx(sex: str | None, age: int, year: int) -> float:
    """Chance of dying during ``year`` at ``age``: the table rate improved from
    TABLE_YEAR. ``sex=None`` averages the two tables."""
    age = min(age, OMEGA - 1)
    if sex is None:
        base = (QX.value["female"][age] + QX.value["male"][age]) / 2
    else:
        base = QX.value[sex][age]
    return min(1.0, base * (1 - _improvement(age)) ** (year - TABLE_YEAR))


def death_cdf(person, start_year: int) -> np.ndarray:
    """``cdf[k]``: chance the person, alive at ``person.age`` on January 1 of
    ``start_year``, has died by the end of the year at age ``person.age + k``."""
    ages = range(person.age, OMEGA)
    survive = np.cumprod([1 - qx(person.sex, a, start_year + (a - person.age)) for a in ages])
    return 1 - survive


def draw_death_ages(people, start_year: int, paths: int, seed: int) -> np.ndarray:
    """(P, N) death ages, each person independent and alive through this year at
    least. Uses its own stream of ``seed``, so it never shifts the return draws."""
    rng = np.random.default_rng([seed, 1])
    out = np.empty((len(people), paths), dtype=int)
    for i, p in enumerate(people):
        cdf = death_cdf(p, start_year)
        k = np.searchsorted(cdf, rng.random(paths), side="right")
        out[i] = np.minimum(p.age + k + 1, OMEGA)
    return out


def median_death_age(person, start_year: int) -> int:
    """The death age the person reaches with a 50% chance or less (the median draw)."""
    cdf = death_cdf(person, start_year)
    return int(min(person.age + int(np.argmax(cdf >= 0.5)) + 1, OMEGA))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_mortality.py tests/test_retirement_rules.py`
Expected: PASS.

- [ ] **Step 6: Run the whole retirement suite (nothing else may move)**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/stockanalysis/retirement/rules.py src/stockanalysis/retirement/mortality.py src/stockanalysis/retirement/inputs.py tests/test_retirement_mortality.py tests/test_retirement_rules.py
git commit -m "feat(retirement): Alberta cohort life table and CPP survivor rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Plan inputs — `sex` and `survivor_share`

**Files:**
- Modify: `src/stockanalysis/retirement/inputs.py` (`Person`, `Spending`, `TEMPLATE`, `limits`, `validate`)
- Test: `tests/test_retirement_inputs.py`

**Interfaces:**
- Consumes: `mortality.SEXES` (Task 1).
- Produces: `Person.sex: str | None = None` (last field; added in Task 1), `Spending.survivor_share: float = 0.70` (last field), `inputs.limits(province)["survivor_share"] == (0.4, 1.0)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retirement_inputs.py`:

```python
def test_sex_and_survivor_share_default_and_round_trip():
    d = copy.deepcopy(inputs.TEMPLATE)
    plan = inputs.parse(d)
    assert [p.sex for p in plan.people] == ["female", "male"]
    assert plan.spending.survivor_share == 0.70
    for p in d["people"]:
        p.pop("sex")
    d["spending"].pop("survivor_share")
    plan = inputs.parse(d)
    assert [p.sex for p in plan.people] == [None, None] and plan.spending.survivor_share == 0.70


def test_a_misspelled_sex_names_the_field():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][1]["sex"] = "M"
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "people[1].sex"


@pytest.mark.parametrize("share", [0.39, 1.01])
def test_survivor_share_is_bounded(share):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["spending"]["survivor_share"] = share
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "spending.survivor_share"
    assert inputs.limits("AB")["survivor_share"] == (0.4, 1.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_inputs.py -k "sex or survivor"`
Expected: FAIL (`KeyError: 'sex'` / `survivor_share` unexpected).

- [ ] **Step 3: Implement**

In `Spending`, add as the last field:

```python
    survivor_share: float = 0.70    # a lone survivor's share of the couple's budget
```

In `TEMPLATE`, add `"sex": "female",` to Partner A and `"sex": "male",` to Partner B (after `"name"`), and `"survivor_share": 0.70` at the end of `"spending"`.

In `limits`, add to the returned dict (a plan bound, not a rule):

```python
            "survivor_share": (0.4, 1.0),
```

In `validate`, inside the people loop after the `age` checks:

```python
        if p.sex is not None and p.sex not in mortality.SEXES:
            _fail(f"{f}.sex", f"{p.sex!r} is not one of {mortality.SEXES} (or leave it out)")
```

After the `spending` share loop:

```python
    lo, hi = lim["survivor_share"]
    if not lo <= s.survivor_share <= hi:
        _fail("spending.survivor_share", f"between {lo} and {hi}")
```

Add `mortality` to the module's imports: `from . import mortality, rules` (it imports `rules` today).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_inputs.py tests/test_retirement_gui.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/inputs.py tests/test_retirement_inputs.py
git commit -m "feat(retirement): plan inputs for sex and survivor spending share

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Engine — the alive mask, the household's end and the estate at the last death

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py`
- Test: `tests/test_retirement_engine.py`

**Interfaces:**
- Consumes: `mortality.OMEGA`.
- Produces:
  - `engine.life_steps(plan) -> int` (= `OMEGA - min(p.age)`);
  - `engine.fixed_deaths(plan, T: int, N: int) -> np.ndarray (P, N)`, where everyone lives through all T years;
  - `engine.simulate(plan, returns, deaths=None) -> Projection`, with T taken from `returns.shape[0]`;
  - new `Projection` fields `death_ages (P, N)`, `alive (T, P, N) bool`, `end_step (N,) int`, `final_investments (N,)`;
  - balances and investments are `NaN` after `end_step`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retirement_engine.py`:

```python
# -- lifespans: the alive mask ------------------------------------------------------

def couple(**kw):
    a = person(id="A", name="A", age=60, retire_age=60)
    b = person(id="B", name="B", age=60, retire_age=60)
    return plan(people=[a, b], **kw)


def test_no_deaths_given_means_everyone_lives_the_whole_horizon():
    p = couple(accounts=[Account("A", "rrsp", 300_000.0), Account("B", "tfsa", 200_000.0)],
               base=40_000.0, end_age=75)
    returns = np.random.default_rng(5).normal(0.03, 0.1, (engine.steps(p), 4))
    implicit = engine.simulate(p, returns)
    explicit = engine.simulate(p, returns, engine.fixed_deaths(p, engine.steps(p), 4))
    for s in engine.SOURCES:
        np.testing.assert_array_equal(implicit.income[s], explicit.income[s])
    np.testing.assert_array_equal(implicit.legacy, explicit.legacy)
    assert implicit.alive.all() and (implicit.end_step == engine.steps(p)).all()


def test_after_the_last_death_nothing_is_spent_or_short_and_balances_are_nan():
    p = couple(accounts=[Account("A", "tfsa", 50_000.0)], base=40_000.0, end_age=80)
    deaths = np.array([[63], [65]])                     # A lives 3 years, B 5
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)
    assert proj.end_step[0] == 5
    assert (proj.need[5:, 0] == 0).all() and (proj.income["shortfall"][5:, 0] == 0).all()
    assert np.isnan(proj.investments[6:, 0]).all() and not np.isnan(proj.investments[5, 0])
    assert proj.final_investments[0] == proj.investments[5, 0]


def test_legacy_is_valued_at_the_last_death():
    p = plan(people=[person(age=60)], accounts=[Account("A", "rrsp", 100_000.0)], base=0.0, end_age=90)
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), np.array([[61]]))
    assert proj.death_tax[0] == pytest.approx(0.48 * 100_000.0)
    assert proj.legacy[0] == pytest.approx(52_000.0)


def test_a_worker_who_dies_stops_earning_and_the_retired_partner_draws_instead():
    a = person(id="A", name="A", age=50, retire_age=60)                # works
    b = person(id="B", name="B", age=62, retire_age=60)                # retired
    p = plan(people=[a, b], accounts=[Account("B", "tfsa", 500_000.0)], base=30_000.0, end_age=70)
    deaths = np.array([[52], [80]])                                    # A dies after 2 years
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)
    assert proj.income["earned"][1, 0] == 30_000.0 and proj.income["earned"][2, 0] == 0.0
    assert proj.income["tfsa"][2, 0] > 0
```

Update `test_projection_runs_until_the_youngest_reaches_end_age` by appending:

```python
    from stockanalysis.retirement import mortality
    assert engine.life_steps(p) == mortality.OMEGA - min(q.age for q in p.people)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_engine.py`
Expected: the new tests FAIL (`simulate() takes 2 positional arguments`, `no attribute 'alive'`); the old ones PASS.

- [ ] **Step 3: Horizon helpers and `Projection` fields**

Change the import to `from . import education, mortality, rules, tax`. Replace `steps` and add helpers:

```python
def steps(plan: PlanInputs) -> int:
    """The "plan to" horizon: from now until the youngest person reaches ``end_age``.
    The average and bad-luck futures and the charts use it."""
    return plan.end_age - min(p.age for p in plan.people)


def life_steps(plan: PlanInputs) -> int:
    """The lifespan horizon: until the youngest could reach mortality.OMEGA."""
    return mortality.OMEGA - min(p.age for p in plan.people)


def fixed_deaths(plan: PlanInputs, T: int, N: int) -> np.ndarray:
    """(P, N) death ages under which everyone lives through all T years."""
    return np.repeat(np.array([[p.age + T] for p in plan.people], dtype=int), N, axis=1)
```

Add these fields at the end of `Projection`:

```python
    death_ages: np.ndarray | None = None        # (P, N) death ages (see mortality)
    alive: np.ndarray | None = None             # (T, P, N) alive in each year
    end_step: np.ndarray | None = None          # (N,) years the household lives; the estate's January 1
    final_investments: np.ndarray | None = None  # (N,) investments on that January 1, before tax at death
```

In `bad_luck_index`, replace `proj.investments[-1]` with `proj.final_investments`.

- [ ] **Step 4: Thread `deaths` through `simulate`**

Make these edits in `simulate` (existing lines quoted, then their replacement):

Signature and shape checks:

```python
def simulate(plan: PlanInputs, returns: np.ndarray, deaths: np.ndarray | None = None) -> Projection:
    """Project ``plan`` over ``returns``: (T, N) real yearly returns. ``deaths`` is a
    (P, N) array of death ages (see mortality); None lets everyone live all T years."""
    people, prov, spend, home = plan.people, plan.province, plan.spending, plan.home
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2 or returns.shape[0] < 1:
        raise ValueError(f"returns must have shape (years, paths); got {returns.shape}")
    T, N, P = returns.shape[0], returns.shape[1], len(people)
    deaths = fixed_deaths(plan, T, N) if deaths is None else np.asarray(deaths, dtype=int)
    if deaths.shape != (P, N):
        raise ValueError(f"deaths must have shape ({P}, {N}); got {deaths.shape}")
    age0 = np.array([[p.age] for p in people])
    end_step = np.clip((deaths - age0).max(axis=0), 0, T)     # years someone is alive
    owner = {p.id: i for i, p in enumerate(people)}
```

(Delete the old `T = steps(plan)`, the old shape check and the old `N, P = ...` line.)

Records: next to `invest = np.zeros((T + 1, N))` add

```python
    gains_rec = np.zeros((T + 1, N))            # unrealized non-reg gains each January 1
    alive_rec = np.zeros((T, P, N), dtype=bool)
```

At the top of the year loop, replace the `working`/`anyone_working` lines with:

```python
        ages = [p.age + t for p in people]
        alive = (age0 + t) < deaths                                      # (P, N)
        alive_rec[t] = alive
        planned = [ages[i] < p.retire_age for i, p in enumerate(people)]  # the plan's calendar
        working = np.array(planned)[:, None] & alive                     # (P, N) actually earning
        anyone_working = any(planned)
        earning = working.any(axis=0)                                    # (N,)
        household = alive.any(axis=0)                                    # (N,)
```

Replace `if lif_step[i] is None and not working[i] and ages[i] >= lif_age[i]:` with `if lif_step[i] is None and not planned[i] and ages[i] >= lif_age[i]:`, and `if working[0]:` (RESP leftover) with `if planned[0]:`.

Next to `balances[k][t] = ...`, after the `for k in ACCOUNTS` loop that records balances, add:

```python
        gains_rec[t] = np.maximum(bal["nonreg"] - cost, 0.0).sum(axis=0)
```

Step 2 of the year (need): replace from `need_rec[t] = need + uncovered` through `cash_need = ...` with

```python
        need = need * household
        uncovered = uncovered * household
        need_rec[t] = need + uncovered
        income["earned"][t] = np.where(earning, need, 0.0)
        cash_need = np.where(earning, uncovered, need + uncovered)
```

Step 3 (guaranteed income): replace the `cpp =` and `oas =` lines with

```python
        cpp = np.array([[cpp_year[i] if ages[i] >= p.cpp_start_age else 0.0]
                        for i, p in enumerate(people)]) * alive          # (P, N)
        oas = np.array([[oas_yearly(p, ages[i])] for i, p in enumerate(people)]) * alive
```

Before `min_rrif = np.minimum(min_rrif, bal["rrsp"])` add `min_rrif, min_lif, lif_room = min_rrif * alive, min_lif * alive, lif_room * alive`. Replace `guaranteed = cpp.sum() + oas.sum() + ...` with

```python
        guaranteed = (cpp + oas + min_rrif + min_lif).sum(axis=0)
        base_taxable = cpp + oas + min_rrif + min_lif
```

(deleting the old `base_taxable` line).

Step 4: replace `retired = np.array([not w for w in working], dtype=float)[:, None]` with `retired = (~working & alive).astype(float)`.

In `_parts`, replace `cpp_i = np.full_like(reg, cpp[i])` with `cpp_i = np.zeros_like(reg) + cpp[i]`, and `"oas": np.full_like(reg, oas[i])` with `"oas": np.zeros_like(reg) + oas[i]`.

Step 5 (worker payouts): replace the loop body so it works per path:

```python
        for i, p in enumerate(people):
            w = working[i]
            if not w.any():
                continue
            held = np.maximum(bal["nonreg"][i], 0.0)
            worker_income += np.where(w, salaries[i] + taxable_yield * held, 0.0)
            if not payouts[i].any():
                continue
            due = np.where(w, np.minimum(tax.extra_tax(p.salary or 0.0, ordinary=y_other * held,
                                                       dividends=y_eligible * held, age=ages[i],
                                                       province=prov), held), 0.0)
            cost[i] -= _pro_rata(cost[i], due, held)
            bal["nonreg"][i] -= due
            drag += due
```

Replace `per_person = surplus / P` with

```python
        per_person = surplus * alive / np.maximum(alive.sum(axis=0), 1)   # to whoever is alive
```

Replace `income["cpp"][t] = cpp.sum()` / `income["oas"][t] = oas.sum()` with `cpp.sum(axis=0)` / `oas.sum(axis=0)`.

Step 6 (contributions): replace the loop with

```python
        for i, p in enumerate(people):
            w = working[i]
            if not w.any():
                continue
            others = [working[j] for j in range(P) if j != i]
            partner = np.logical_and.reduce(others) if others else np.ones(N, dtype=bool)
            alt = p.contributions_when_partner_retired

            def amount(kind, w=w, partner=partner, alt=alt, p=p):
                full = p.contributions.get(kind, 0.0)
                when_alone = full if alt is None else alt.get(kind, 0.0)
                return np.where(w, np.where(partner, full, when_alone), 0.0)

            bal["pension"][i] += amount("pension")
            bal["rrsp"][i] += amount("rrsp")
            to_tfsa = np.minimum(amount("tfsa"), room[i])
            room[i] -= to_tfsa
            bal["tfsa"][i] += to_tfsa
            extra = amount("tfsa") - to_tfsa + amount("nonreg")
            bal["nonreg"][i] += extra
            cost[i] += extra
```

After the loop, replace the whole death-tax block (`top = ...` through the `return Projection(...)`) with

```python
    for k in ACCOUNTS:
        balances[k][T] = bal[k].sum(axis=0)
    invest[T] = sum(balances[k][T] for k in ACCOUNTS)
    gains_rec[T] = np.maximum(bal["nonreg"] - cost, 0.0).sum(axis=0)
    if school is not None:
        resp_rec[T] = resp_bal
    if home is not None:
        home_value[T] = home.new_value if downsized else home.value
    # The estate: the January 1 after the last death, in each future.
    cols = np.arange(N)
    top = rules.top_marginal_rate(prov)
    registered = balances["rrsp"][end_step, cols] + balances["pension"][end_step, cols]
    death_tax = top * registered + top * rules.CAPITAL_GAINS_INCLUSION.value * gains_rec[end_step, cols]
    final = invest[end_step, cols].copy()
    later = np.arange(T + 1)[:, None] > end_step[None, :]       # nobody left: no estate to track
    invest[later] = np.nan
    for k in ACCOUNTS:
        balances[k][later] = np.nan
    return Projection(
        years=[plan.start_year + t for t in range(T)],
        ages=[tuple(p.age + t for p in people) for t in range(T)],
        income=income, tax=tax_paid, need=need_rec, saved=saved_rec, tfsa_room=room_rec,
        investments=invest, balances=balances, home_value=home_value,
        legacy=final + home_value[end_step] - death_tax, death_tax=death_tax,
        retire_step=retire_step, max_residual=max_resid,
        education=edu_out, student_grant=csg_rec, resp=resp_rec, school=school,
        death_ages=deaths, alive=alive_rec, end_step=end_step, final_investments=final)
```

(The existing `for k in ACCOUNTS: balances[k][T] = ...`, `invest[T]`, `resp_rec[T]` and `home_value[T]` lines after the loop are folded into this block; don't keep duplicates.)

- [ ] **Step 5: Run the engine tests**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_engine.py tests/test_retirement_education.py`
Expected: PASS, including every pre-existing test unchanged. That's the regression anchor: with `deaths=None` the numbers are today's.

- [ ] **Step 6: Run the whole retirement suite**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/test_retirement_engine.py
git commit -m "feat(retirement): per-path alive mask and the estate at the last death

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Engine — survivor rules

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py`
- Test: `tests/test_retirement_engine.py`

**Interfaces:**
- Consumes: `rules.CPP["survivor"]`, `rules.CPP["death_benefit"]` (Task 1); `Spending.survivor_share` (Task 2); the Task 3 mask.
- Produces: `engine.roll_over(bal, cost, room, restore, lif_gain, alive) -> np.ndarray (P, N) bool` and `engine.survivor_cpp(people, ages, alive, own) -> np.ndarray (P, N)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retirement_engine.py`:

```python
# -- lifespans: survivor years ------------------------------------------------------

def widowed(a_kw=None, b_kw=None, accounts=(), base=40_000.0, end_age=80, death_a=63, **kw):
    """A couple where A dies at ``death_a`` and B lives past the horizon; zero returns."""
    a = person(**{"id": "A", "name": "A", "age": 60, "retire_age": 60, **(a_kw or {})})
    b = person(**{"id": "B", "name": "B", "age": 60, "retire_age": 60, **(b_kw or {})})
    p = plan(people=[a, b], accounts=accounts, base=base, end_age=end_age, **kw)
    deaths = np.array([[death_a], [p.end_age]])
    return p, engine.simulate(p, np.zeros((engine.steps(p), 1)), deaths)


def test_rollover_moves_everything_to_the_survivor_untaxed():
    # No spending, no returns, no RRIF yet (rrif_start_age 71): nothing should move but owners.
    p, proj = widowed(accounts=[Account("A", "rrsp", 200_000.0), Account("A", "tfsa", 50_000.0),
                                Account("B", "tfsa", 10_000.0)], base=0.0)
    assert proj.investments[3, 0] == pytest.approx(260_000.0)          # the January after A's death
    assert proj.investments[10, 0] == pytest.approx(260_000.0)         # still all there, now B's
    assert proj.balances["rrsp"][3, 0] == pytest.approx(200_000.0)     # still registered, untaxed
    assert (proj.tax[:, 0] == 0).all()


def test_survivor_spends_the_survivor_share_and_care_stays_whole():
    p, proj = widowed(base=40_000.0, accounts=[Account("B", "tfsa", 2_000_000.0)],
                      care=5_000.0, no_go_age=60, no_go_share=1.0)
    assert proj.need[2, 0] == pytest.approx(45_000.0)
    assert proj.need[3, 0] == pytest.approx(40_000.0 * 0.70 + 5_000.0)


def test_cpp_survivor_pension_at_65_is_60_percent_capped_by_the_combined_maximum():
    s = rules.CPP["survivor"].value
    p, proj = widowed(a_kw=dict(age=66, cpp_at_65=12_000.0, cpp_start_age=65),
                      b_kw=dict(age=66, cpp_at_65=6_000.0, cpp_start_age=65),
                      accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=68, end_age=75)
    assert proj.income["cpp"][1, 0] == pytest.approx(18_000.0)
    assert proj.income["cpp"][2, 0] == pytest.approx(6_000.0 + 0.60 * 12_000.0)
    big = widowed(a_kw=dict(age=66, cpp_at_65=18_000.0, cpp_start_age=65),
                  b_kw=dict(age=66, cpp_at_65=17_000.0, cpp_start_age=65),
                  accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=68, end_age=75)[1]
    assert big.income["cpp"][2, 0] == pytest.approx(12 * s["combined_max_monthly"])


def test_a_survivor_under_65_gets_the_flat_rate_plus_37_5_percent():
    s = rules.CPP["survivor"].value
    p, proj = widowed(a_kw=dict(age=55, cpp_at_65=12_000.0), b_kw=dict(age=55, cpp_at_65=0.0),
                      accounts=[Account("B", "tfsa", 2_000_000.0)], death_a=57, end_age=70)
    assert proj.income["cpp"][2, 0] == pytest.approx(12 * s["flat_monthly"] + 0.375 * 12_000.0)


def test_no_pension_splitting_after_the_first_death():
    # All the RRIF is A's; B dies at 72. With no spending, A's only income is the RRIF
    # minimum, so A must be taxed exactly as a single filer on it: no split to B.
    both = dict(age=70, retire_age=60, rrif_start_age=65)
    p = plan(people=[person(id="A", name="A", **both), person(id="B", name="B", **both)],
             accounts=[Account("A", "rrsp", 1_000_000.0)], base=0.0, end_age=80)
    zeros = np.zeros((engine.steps(p), 1))
    alone = engine.simulate(p, zeros, np.array([[80], [72]]))
    together = engine.simulate(p, zeros)
    pension = alone.income["minimums"][3, 0]
    assert alone.tax[3, 0] == pytest.approx(float(tax.income_tax(pension=pension, age=73)), abs=1.0)
    assert together.tax[3, 0] < alone.tax[3, 0]                      # splitting helped while both lived


def test_the_death_benefit_is_paid_once_to_the_survivor():
    benefit = rules.CPP["death_benefit"].value
    p, with_cpp = widowed(a_kw=dict(cpp_at_65=10_000.0), base=0.0)
    _, without = widowed(a_kw=dict(cpp_at_65=0.0), base=0.0)
    gap = with_cpp.investments[3, 0] - without.investments[3, 0]
    assert gap == pytest.approx(benefit, abs=1.0)


def test_one_person_plan_ignores_the_survivor_share():
    p = plan(people=[person(age=60)], accounts=[Account("A", "tfsa", 500_000.0)], base=30_000.0,
             end_age=80, survivor_share=0.5)
    proj = engine.simulate(p, np.zeros((engine.steps(p), 1)), np.array([[70]]))
    assert proj.need[5, 0] == 30_000.0 and proj.end_step[0] == 10
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_engine.py -k "rollover or survivor or splitting or death_benefit or one_person"`
Expected: FAIL (spending unchanged after the death, no survivor CPP, money stranded in the dead person's accounts).

- [ ] **Step 3: Add the helpers** (after `_resp_year`)

```python
def roll_over(bal, cost, room, restore, lif_gain, alive) -> np.ndarray:
    """Spousal rollover, untaxed, in place: a dead person's accounts, cost base and
    last LIF return move to the living partner, and their TFSA room is lost. Runs
    every January, so money paid to a dead person later is swept too. Returns
    (P, N) bool: who was rolled into a partner this January."""
    moved = np.zeros(alive.shape, dtype=bool)
    for i, j in ((0, 1), (1, 0)):
        go = ~alive[i] & alive[j]
        if not go.any():
            continue
        for k in ACCOUNTS:
            bal[k][j] += np.where(go, bal[k][i], 0.0)
            bal[k][i] = np.where(go, 0.0, bal[k][i])
        for arr in (cost, lif_gain):
            arr[j] += np.where(go, arr[i], 0.0)
            arr[i] = np.where(go, 0.0, arr[i])
        room[i] = np.where(go, 0.0, room[i])
        restore[i] = np.where(go, 0.0, restore[i])
        moved[i] |= go
    return moved


def survivor_cpp(people, ages, alive, own) -> np.ndarray:
    """(P, N) yearly CPP survivor's pension for a living person whose partner has
    died: 60% of the partner's CPP at 65 from 65, or the flat rate plus 37.5% before,
    capped so it plus their own CPP stays within the combined maximum."""
    s = rules.CPP["survivor"].value
    out = np.zeros(alive.shape)
    for i, j in ((0, 1), (1, 0)):                       # j survives i
        base = cpp_at_65(people[i])
        amount = (s["share_65"] * base if ages[j] >= 65
                  else 12 * s["flat_monthly"] + s["share_under_65"] * base)
        out[j] = np.where(~alive[i] & alive[j],
                          np.clip(12 * s["combined_max_monthly"] - own[j], 0.0, amount), 0.0)
    return out
```

- [ ] **Step 4: Wire them into `simulate`**

Before the year loop (next to `prev_tax, prev_share = ...`):

```python
    benefit_paid = np.zeros((P, N), dtype=bool)
    death_benefit = rules.CPP["death_benefit"].value
```

In step 1, right after `restore[:] = 0.0` and **before** `rrsp_jan1 = bal["rrsp"].copy()`:

```python
        if P == 2:
            moved = roll_over(bal, cost, room, restore, lif_gain, alive)
            first = moved & ~benefit_paid
            benefit_paid |= moved
            for i, j in ((0, 1), (1, 0)):              # the CPP death benefit, once, untaxed
                if cpp_at_65(people[i]) > 0:
                    paid = np.where(first[i], death_benefit, 0.0)
                    bal["nonreg"][j] += paid
                    cost[j] += paid
```

Downsizing: replace `bal["nonreg"] += released / P` and `cost += released / P` with

```python
            split = np.where(household, alive / np.maximum(alive.sum(axis=0), 1), 1.0 / P)
            bal["nonreg"] += released * split
            cost += released * split
```

Step 2 (need): right after the bad-market `need = np.where(...)` block (and before care is added):

```python
        if P == 2:
            need = np.where(alive.sum(axis=0) == 1, need * spend.survivor_share, need)
```

Step 3: right after the `oas = ...` line:

```python
        if P == 2:
            cpp = cpp + survivor_cpp(people, ages, alive, cpp)
```

Step 4 (tax loop): replace `share, tax_est = prev_share, prev_tax.copy()` with

```python
        both = alive.all(axis=0)
        share, tax_est = np.where(both, prev_share, 0.0), prev_tax.copy()
```

and replace `best = tax.best_split(parts, prov)` with

```python
            best = np.where(both, tax.best_split(parts, prov), 0.0)   # never split to the dead
```

- [ ] **Step 5: Run the tests**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_engine.py tests/test_retirement_education.py`
Expected: PASS (old and new).

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/test_retirement_engine.py
git commit -m "feat(retirement): survivor years: rollover, CPP survivor pension, no splitting, survivor spending

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Lifespan runs — `engine.run`, what-ifs and the planning tools

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (`average_deaths`, `draw_futures`, `run`)
- Modify: `src/stockanalysis/retirement/scenarios.py` (`evaluate`)
- Modify: `src/stockanalysis/retirement/optimize.py`
- Test: `tests/test_retirement_engine.py`, `tests/test_retirement_optimize.py`, `tests/test_retirement_scenarios.py`

**Interfaces:**
- Consumes: `mortality.draw_death_ages`, `mortality.median_death_age`, the Task 3/4 engine.
- Produces:
  - `engine.average_deaths(plan) -> np.ndarray (P, 1)`;
  - `engine.draw_futures(plan, paths: int, seed: int) -> tuple[np.ndarray (life_steps, N), np.ndarray (P, N)]`;
  - `engine.run(...)`: `simulated` over the lifespan draws; `average` / `bad_luck` over `steps(plan)` with `average_deaths`;
  - `optimize._futures(plan, paths, seed) -> (R, D)` replaces `optimize._returns`; `max_spending(plan, target, F)` / `earliest_retirement(plan, target, F)` take `F = (R, D)`;
  - `optimize.LIFESPAN_DRAWS = 300` and `optimize._expected_legacy(plan, deaths) -> float`;
  - `BenefitAges.legacy` / `best_legacy` now mean the expected legacy over lifespans.

- [ ] **Step 1: Write the failing tests**

In `tests/test_retirement_engine.py`, replace the body of `test_run_uses_the_median_return_and_replays_the_bad_luck_path` with

```python
    p = plan(accounts=[Account("A", "rrsp", 400_000.0)], base=30_000.0, end_age=80)
    p = replace(p, returns=Returns(0.05, 0.15, 200, 3))
    result = engine.run(p)
    assert result.average_return == engine.median_return(0.05, 0.15)
    assert result.average.paths == 1 and result.simulated.paths == 200
    assert len(result.simulated.years) == engine.life_steps(p)
    assert len(result.average.years) == engine.steps(p)
    R, D = engine.draw_futures(p, 200, 3)
    replay = engine.simulate(p, R[:engine.steps(p), [result.bad_luck_path]], engine.average_deaths(p))
    for s in engine.SOURCES:
        np.testing.assert_allclose(result.bad_luck.income[s], replay.income[s])
    np.testing.assert_array_equal(result.simulated.death_ages, D)
```

and append:

```python
def test_average_future_loses_the_earlier_median_death_first_and_the_survivor_reaches_end_age():
    from stockanalysis.retirement import mortality
    a = person(id="A", name="A", age=60, sex="male")
    b = person(id="B", name="B", age=60, sex="female")
    p = plan(people=[a, b], end_age=95)
    d = engine.average_deaths(p)
    assert d[0, 0] == min(mortality.median_death_age(a, 2026), a.age + engine.steps(p))
    assert d[1, 0] == b.age + engine.steps(p)
    single = plan(people=[person(age=60)], end_age=95)
    assert engine.average_deaths(single)[0, 0] == 95


def test_success_counts_only_years_someone_is_alive():
    p = plan(accounts=[Account("A", "tfsa", 100_000.0)], base=30_000.0, end_age=95)
    R = np.zeros((engine.life_steps(p), 2))
    proj = engine.simulate(p, R, np.array([[62, 100]]))
    assert proj.shortfall_years[0] == 0 and proj.shortfall_years[1] > 0
```

In `tests/test_retirement_optimize.py`, replace the `R` fixture and the uses of `_returns`:

```python
@pytest.fixture(scope="module")
def R():
    return optimize._futures(PLAN, 60, 7)
```

and in `test_affordability_reports_the_plan_as_it_stands`:

```python
    assert a.success == engine.simulate(PLAN, *optimize._futures(PLAN, 60, PLAN.returns.seed)).success
```

Append:

```python
def test_expected_legacy_averages_the_lifespan_draws():
    from stockanalysis.retirement import mortality
    D = mortality.draw_death_ages(PLAN.people, PLAN.start_year, 20, 7)
    g = engine.median_return(PLAN.returns.mean, PLAN.returns.sd)
    proj = engine.simulate(PLAN, np.full((engine.life_steps(PLAN), 20), g), D)
    assert optimize._expected_legacy(PLAN, D) == pytest.approx(float(proj.legacy.mean()))
```

(add `import numpy as np` at the top of the module).

- [ ] **Step 2: Run them to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_engine.py tests/test_retirement_optimize.py`
Expected: FAIL (`no attribute 'draw_futures'`, `'_futures'`, `'average_deaths'`).

- [ ] **Step 3: Engine `average_deaths`, `draw_futures`, `run`**

Replace `run` and add the helpers above it:

```python
def average_deaths(plan: PlanInputs) -> np.ndarray:
    """(P, 1) deaths for the average and bad-luck futures. In a couple, the person
    with fewer median years left (ties: the older, then people[1]) dies at their
    median death age and the survivor lives to ``end_age``; one person lives to it."""
    T = steps(plan)
    d = fixed_deaths(plan, T, 1)
    if len(plan.people) == 2:
        left = [(mortality.median_death_age(p, plan.start_year) - p.age, -p.age, -i)
                for i, p in enumerate(plan.people)]
        i = min(range(2), key=lambda k: left[k])
        d[i, 0] = min(d[i, 0], plan.people[i].age + left[i][0])
    return d


def draw_futures(plan: PlanInputs, paths: int, seed: int) -> tuple:
    """(returns over life_steps, death ages): one set of futures every comparison shares."""
    r = plan.returns
    return (draw_returns(r.mean, r.sd, paths, life_steps(plan), seed),
            mortality.draw_death_ages(plan.people, plan.start_year, paths, seed))


def run(plan: PlanInputs, *, paths: int | None = None, seed: int | None = None) -> PlanResult:
    """The plan over ``paths`` simulated futures with drawn lifespans, plus the average
    future (a steady median return) and the bad-luck future (the 10th-percentile path's
    returns, replayed alone), both with ``average_deaths`` over ``steps(plan)``."""
    r = plan.returns
    R, D = draw_futures(plan, r.paths if paths is None else paths, r.seed if seed is None else seed)
    simulated = simulate(plan, R, D)
    T, g, fixed = steps(plan), median_return(r.mean, r.sd), average_deaths(plan)
    average = simulate(plan, np.full((T, 1), g), fixed)
    k = bad_luck_index(simulated)
    return PlanResult(plan, simulated, average, simulate(plan, R[:T, [k]], fixed), g, k)
```

Update the module docstring's step list with one new line after step 6:

```
7. Lifespans (``deaths``): a dead person earns, draws and pays nothing; each January
   their accounts roll to the living partner untaxed (plus the CPP death benefit
   once); the survivor gets the CPP survivor's pension, spends ``survivor_share`` of
   the budget, and files alone (no splitting). The estate is valued the January
   after the last death.
```

- [ ] **Step 4: `scenarios.evaluate`**

```python
def evaluate(plan: PlanInputs, *, paths: int, seed: int) -> tuple:
    """(success over ``paths`` futures with drawn lifespans, average-future lifetime tax,
    average-future legacy)."""
    r, T = plan.returns, engine.steps(plan)
    simulated = engine.simulate(plan, *engine.draw_futures(plan, paths, seed))
    average = engine.simulate(plan, np.full((T, 1), engine.median_return(r.mean, r.sd)),
                              engine.average_deaths(plan))
    return simulated.success, float(average.lifetime_tax[0]), float(average.legacy[0])
```

- [ ] **Step 5: `optimize.py`**

Replace `_returns`, `_success` and `_average_legacy`:

```python
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
```

In `max_spending` and `earliest_retirement`, rename the parameter `R` to `F` (and every `_success(..., R)` to `_success(..., F)`); update their docstrings' wording only where they mention returns. In `affordability`: `F = _futures(plan, paths, plan.returns.seed if seed is None else seed)` and pass `F`.

In `best_benefit_ages`: add `from functools import partial` to the imports, then

```python
    s = plan.returns.seed if seed is None else seed
    D = mortality.draw_death_ages(plan.people, plan.start_year, LIFESPAN_DRAWS, s)
    score = partial(_expected_legacy, deaths=D)
    with nullcontext() if workers == 1 else ProcessPoolExecutor(workers) as pool:
        best, grids = _search(plan, cpp_ages, oas_ages, rounds, pool.map if pool else map, score)
    ...
    F = _futures(plan, paths, s)
    return BenefitAges(tuple(choices), score(plan), score(best), _success(plan, F), _success(best, F), best)
```

and give `_search` a `score` parameter used in place of `_average_legacy`:

```python
def _search(plan: PlanInputs, cpp_ages, oas_ages, rounds: int, run, score) -> tuple:
    ...
            grid = dict(zip(keys, run(score, [_with_ages(best, i, *k) for k in keys])))
```

Imports: `from . import engine, mortality`. Update the module docstring: `best_benefit_ages` maximizes "the expected after-tax legacy over drawn lifespans (a steady median return)", and `BenefitAges.legacy` / `best_legacy`'s comments say "expected legacy over lifespans, today's / best ages".

- [ ] **Step 6: Run the tests**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`
Expected: PASS. If a report/GUI test pinned an old CLI string, leave it for Task 6 and note which.

- [ ] **Step 7: Time the optimizer (record the number in the commit message)**

Run: `source venv/bin/activate && python -c "import time; from stockanalysis.retirement import inputs, optimize; p=inputs.parse(inputs.TEMPLATE); t=time.time(); optimize.best_benefit_ages(p, paths=1000); print(round(time.time()-t,1))"`
Expected: about 10 s or less on 10 cores. If it is over 20 s, stop and report it before continuing.

- [ ] **Step 8: Commit**

```bash
git add src/stockanalysis/retirement/engine.py src/stockanalysis/retirement/scenarios.py src/stockanalysis/retirement/optimize.py tests/test_retirement_engine.py tests/test_retirement_optimize.py
git commit -m "feat(retirement): lifespan draws in runs, what-ifs and the planning tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Report, summary and CLI

**Files:**
- Modify: `src/stockanalysis/retirement/report.py`, `src/stockanalysis/retirement/cli.py`, `src/stockanalysis/retirement/gui.py` (`summarize` only)
- Test: `tests/test_retirement_report.py`, `tests/test_cli_retire.py`

**Interfaces:**
- Consumes: `Projection.death_ages`, `.alive`, `engine.average_deaths`, `mortality.QX`, `mortality.IMPROVEMENT`.
- Produces:
  - `report.lasts_label(plan) -> str`;
  - `report.lifespans(result) -> dict` (`{"people": [{"name", "median_age_at_death"}], "reach_95": float, "alone_years": float | None}`);
  - `report.success_meter(success, previous=None, title="Chance the money lasts")`;
  - `headline(result)["lifespans"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retirement_report.py`:

```python
def test_headline_says_as_long_as_either_of_you_lives(built):
    plan, result, *_ = built
    html = _html(built)
    assert report.lasts_label(plan) in html
    assert report.lasts_label(plan) == "Chance the money lasts as long as either of you lives"
    single = replace(plan, people=plan.people[:1])
    assert report.lasts_label(single) == "Chance the money lasts as long as you live"


def test_lifespans_tile_and_summary(built):
    plan, result, *_ = built
    life = report.lifespans(result)
    assert [x["name"] for x in life["people"]] == [p.name for p in plan.people]
    for x, p in zip(life["people"], plan.people):
        assert p.age <= x["median_age_at_death"] < 111
    assert 0 <= life["reach_95"] <= 1 and life["alone_years"] >= 0
    assert "Median age at death" in _html(built)
    assert report.headline(result)["lifespans"] == life


def test_year_table_marks_the_dead_with_a_dagger(built):
    _, result, *_ = built
    avg = result.average
    table = report._year_table(avg)
    if (~avg.alive[:, :, 0]).any():
        assert "†" in table
    else:
        assert "†" not in table


def test_money_left_band_stops_at_end_age_and_ignores_ended_futures(built):
    plan, result, *_ = built
    fig = report.money_left_chart(result.simulated, plan)
    xs = fig.data[0].x
    assert xs[-1] <= plan.end_age and not any(np.isnan(fig.data[2].y[:3]))


def test_assumptions_list_lifespan_sources(built):
    html = _html(built)
    assert "pid=1310011401" in html and "actuarial-report-32nd" in html
    assert "Survivor spending" in html
```

(add `import numpy as np` at the top).

In `tests/test_cli_retire.py`, `test_pinned_balances_write_report_and_summary`, replace

```python
    assert "Chance the money lasts to 95" in printed and "Report:" in printed
```

with

```python
    assert "Chance the money lasts as long as either of you lives" in printed and "Report:" in printed
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_report.py tests/test_cli_retire.py`
Expected: FAIL (`no attribute 'lasts_label'`).

- [ ] **Step 3: Implement in `report.py`**

Imports: `from . import engine, mortality, rules` and add `import warnings`.

```python
def lasts_label(plan) -> str:
    who = "either of you lives" if len(plan.people) == 2 else "you live"
    return f"Chance the money lasts as long as {who}"


def lifespans(result: PlanResult) -> dict:
    """Median age at death per person, the chance someone reaches 95, and the median
    years a survivor lives alone, over the simulated futures."""
    plan, d = result.inputs, result.simulated.death_ages
    age0 = np.array([[p.age] for p in plan.people])
    out = {"people": [{"name": p.name, "median_age_at_death": int(np.median(d[i])) - 1}
                      for i, p in enumerate(plan.people)],
           "reach_95": float((d > 95).any(axis=0).mean()),
           "alone_years": None}
    if len(plan.people) == 2:
        left = d - age0
        out["alone_years"] = float(np.median(np.abs(left[0] - left[1])))
    return out


def _lifespan_tiles(result: PlanResult) -> str:
    life = lifespans(result)
    ages = " · ".join(f"{x['name']} {x['median_age_at_death']}" for x in life["people"])
    tiles = [_tile("Median age at death", ages, "Alberta life table, improving over time"),
             _tile("Chance one of you reaches 95" if len(life["people"]) == 2 else "Chance of reaching 95",
                   f"{life['reach_95']:.0%}", "Plan-to age stays your choice")]
    if life["alone_years"] is not None:
        tiles.append(_tile("Survivor alone (median)", f"{life['alone_years']:.0f} years",
                           f"Spending {result.inputs.spending.survivor_share:.0%} of the couple's"))
    return (f"<div class='kpis' style='grid-template-columns:repeat({len(tiles)},1fr);margin-top:16px'>"
            + "".join(tiles) + "</div>")
```

`success_meter` gets a `title` parameter (default `"Chance the money lasts"`) used in `title={"text": title, ...}`. In `build_report`, call it with `title=lasts_label(plan)` and append `_lifespan_tiles(result)` to `top` after the closing `</div>` of `.top`.

`money_left_chart`: replace the first lines with

```python
    ref = plan.people[0]
    rows = min(sim.investments.shape[0], plan.end_age - ref.age + 1)   # the band stops at end_age
    inv = sim.investments[:rows]
    x = [ref.age + t for t in range(rows)]
    with warnings.catch_warnings():                       # a year where every future has ended
        warnings.simplefilter("ignore", RuntimeWarning)
        p10, p50, p90 = np.nanpercentile(inv, [10, 50, 90], axis=1)
```

and use `sim.home_value[:rows]` for the home trace. After the CPP/OAS marks, add the average future's first death:

```python
    fixed = engine.average_deaths(plan)
    for i, p in enumerate(plan.people):
        if len(plan.people) == 2 and fixed[i, 0] < p.age + engine.steps(plan):
            marks.append((at(p, int(fixed[i, 0])), f"{p.name}: dies (average future)"))
```

`_year_table`: replace the Ages cell with

```python
        ages = "/".join(f"{a}{'' if proj.alive is None or proj.alive[t, i, 0] else '†'}"
                        for i, a in enumerate(proj.ages[t]))
```

using `{_esc(ages)}` in the cell, and add to the note: `"† = has died (the survivor's years follow)."`.

`_assumptions`: add a `Sex` column to the people table (`<th>Sex</th>` after `Age`, cell `{_esc(p.sex or 'not set (average table)')}`); add facts after "Spending":

```python
        ("Survivor spending", f"{s.survivor_share:.0%} of the couple's budget once one of you has "
                              "died (care costs stay whole)" if len(plan.people) == 2 else "n/a"),
        ("Lifespans", "drawn per future from the Statistics Canada Alberta life table "
                      "(2021–2023) with the CPP actuarial report's mortality improvement; "
                      f"the average future assumes the median first death and the survivor to {plan.end_age}"),
```

and append the mortality sources to the rules rows:

```python
    life = [("mortality.life_table", "Alberta 2021–2023, by sex, ages 0–110", mortality.QX),
            ("mortality.improvement", _rule_value(mortality.IMPROVEMENT.value), mortality.IMPROVEMENT)]
    rules_rows += "".join(
        f"<tr><td>{_esc(n)}</td><td>{_esc(v)}</td><td>{r.year}</td>"
        f"<td><a href='{_esc(r.source)}'>source</a></td></tr>" for n, v, r in life)
```

`headline`: add `"lifespans": lifespans(result)` to the dict.

- [ ] **Step 4: CLI and GUI summary**

`cli.py`: replace `print(f"Chance the money lasts to {plan.end_age}: ...")` with `print(f"{report.lasts_label(plan)}: {result.simulated.success:.0%}")` (import `report` from `.` if not already), and in `_print_optimize` replace the last print's `"Legacy (average future)"` with `"Expected legacy (over lifespans)"`.

`gui.py` `summarize`: `report.success_meter(sim.success, previous, title=report.lasts_label(plan))`.

- [ ] **Step 5: Run the tests**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/report.py src/stockanalysis/retirement/cli.py src/stockanalysis/retirement/gui.py tests/test_retirement_report.py tests/test_cli_retire.py
git commit -m "feat(retirement): report lifespans, survivor years and the new success wording

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: GUI page — sex, survivor share, optimizer wording

**Files:**
- Modify: `src/stockanalysis/retirement/static/planner.html`
- Test: `tests/test_retirement_gui.py`

**Interfaces:**
- Consumes: `limits.survivor_share` (Task 2, delivered through `/api/plan`), the `summarize()` dict (Task 6).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retirement_gui.py` (it uses the module's `server` fixture and `_req` helper):

```python
def test_page_edits_sex_and_survivor_share(server):
    status, page = _req(server, "GET", "/")
    page = page.decode()
    assert status == 200 and "${P}.sex" in page and "spending.survivor_share" in page
    assert "It assumes everyone lives to" not in page
    status, st = _req(server, "GET", "/api/plan")
    assert st["limits"]["survivor_share"] == [0.4, 1.0]
    assert "lifespans" in st["saved"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_gui.py -k sex_and_survivor`
Expected: FAIL.

- [ ] **Step 3: Implement in `planner.html`**

Add a select helper after `text()`:

```javascript
function choice(path, label, options) {
  const v = get(draft, path) ?? "";
  return `<div class="field" data-field="${path}"><label>${esc(label)}</label>
    <select data-path="${path}">${options.map(([k, l]) =>
      `<option value="${k}" ${k === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
}
```

In `readInput`, first line:

```javascript
  if (el.tagName === "SELECT") return el.value === "" ? null : el.value;
```

In `peoplePanel`, inside the `grid2` after the age field:

```javascript
        ${choice(`${P}.sex`, "Sex (for life expectancy)", [["", "Not set (average)"], ["female", "Female"], ["male", "Male"]])}
```

In `spendingPanel`'s "Later life" card, after the care field, only for couples:

```javascript
      ${draft.people.length === 2 ? slider("spending.survivor_share", "A survivor alone spends", {min: limits.survivor_share[0], max: limits.survivor_share[1], step: 0.01, unit: "%"}) : ""}
```

In `optimizePanel`, replace the CPP/OAS note's two sentences with:

```javascript
        leave the most money after tax on average over 300 drawn lifespans (about 10 seconds), so
        starting later counts only in the futures where you live to collect it.</p>
```

and in the benefits answer replace `Legacy in the average future:` with `Expected legacy (over lifespans):`.

In `showResults`, add a fifth tile after "Lifetime taxes" when `r.lifespans` exists:

```javascript
    ...(r.lifespans ? [["Median age at death", r.lifespans.people.map(x => `${esc(x.name)} ${x.median_age_at_death}`).join(" · "),
        `Chance someone reaches 95: ${pct(r.lifespans.reach_95)}`]] : []),
```

(The existing `tiles.map` doesn't escape the value column, because values carry HTML deltas, so `esc(x.name)` stays.)

- [ ] **Step 4: Run the GUI tests**

Run: `source venv/bin/activate && pytest -q tests/test_retirement_gui.py`
Expected: PASS.

- [ ] **Step 5: Check it in the real page**

Run: `source venv/bin/activate && stock-analysis retire --gui --inputs /tmp/plan_check/plan.json --no-browser` after `stock-analysis retire --init --inputs /tmp/plan_check/plan.json`; open the printed URL. Change a person's sex and the survivor slider, and check the preview reruns without a console error. Stop the server.

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/static/planner.html tests/test_retirement_gui.py
git commit -m "feat(retirement): GUI edits sex and survivor share; optimizer weighs lifespans

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs

**Files:**
- Modify: `src/stockanalysis/retirement/CLAUDE.md`, `src/stockanalysis/retirement/README.md`, `.claude/skills/retirement-planning/SKILL.md`, root `CLAUDE.md` (the retirement test list mentions planner pieces; add "lifespans")

- [ ] **Step 1: `retirement/CLAUDE.md`**

Module map row, after `rules.py`:

```
| `mortality.py` | Lifespans: the Statistics Canada Alberta life table (`QX`, 2021/2023) and CPP-report improvement (`IMPROVEMENT`) as `Rule`s; `qx`, `draw_death_ages` (own seed stream), `median_death_age`. Death age = the age on the January 1 after the last year lived; `OMEGA = 111` |
```

Update the `engine.py` row to mention `simulate(plan, returns, deaths=None)`, `life_steps` vs `steps`, `average_deaths`, `draw_futures`. Add a convention bullet:

```
- **Lifespans and survivor years.** `deaths=None` means everyone lives the whole
  horizon, which is the old engine exactly; keep it that way (every pre-lifespan
  test is the regression anchor). Simulated runs use `life_steps` and drawn deaths;
  the average / bad-luck futures use `steps` and `average_deaths`. A dead person
  earns, draws and pays nothing; each January `roll_over` moves their accounts to
  the living partner untaxed; the survivor gets `survivor_cpp` (from
  `rules.CPP["survivor"]`), spends `survivor_share`, and is never split with.
  Balances are NaN after the household's `end_step`; read the estate from
  `final_investments` / `legacy`, never `investments[-1]`. Every comparison draws
  returns **and** deaths with `engine.draw_futures`.
```

Update the planning-tools bullet on `best_benefit_ages`: it now scores the expected legacy over `LIFESPAN_DRAWS` death draws at a steady median return (about 0.3 s per candidate, the same as one path), not the average future.

- [ ] **Step 2: `retirement/README.md`**

Remove items 2 and 3 from "Future improvements" and renumber. In the feature list, add a short "Lifespans and survivor years" paragraph: the headline is the chance the money lasts as long as either of you lives; deaths come from the Alberta life table with improvement; after the first death the plan follows the rollover, the CPP survivor's pension, single-filer tax and `survivor_share` spending; `sex` and `survivor_share` are plan inputs.

- [ ] **Step 3: Skill and root CLAUDE.md**

In `.claude/skills/retirement-planning/SKILL.md`, wherever the headline is explained, say "as long as either of you lives", and add `mortality.py` to the yearly rules update (the life table is re-pulled when Statistics Canada publishes a new three-year table; the CPP survivor amounts change every January with the rest of `rules.CPP`). In the root `CLAUDE.md` retirement test list, add "lifespans and survivor years".

- [ ] **Step 4: Full suite and commit**

Run: `source venv/bin/activate && pytest -q`
Expected: PASS (whole repo).

```bash
git add src/stockanalysis/retirement/CLAUDE.md src/stockanalysis/retirement/README.md .claude/skills/retirement-planning/SKILL.md CLAUDE.md
git commit -m "docs(retirement): lifespans and survivor years

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
