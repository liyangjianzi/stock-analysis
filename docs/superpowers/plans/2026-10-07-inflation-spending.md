# Inflation That Reaches Spending — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inflation reaches the retirement plan's spending: random CPI paths whose shocks make returns lag, cost categories that outgrow CPI, fixed-dollar amounts that shrink with prices, and a today's/future-dollars switch in the report and GUI.

**Architecture:** The engine keeps working in today's dollars. `engine.draw_futures` adds a per-future inflation path (5-year blocks of `rules.CPI`) and lags returns by it; `simulate` takes the path and uses it for the price level (nominal cost base, eroding fixed amounts). Cost categories grow by deterministic yearly factors. Display multiplies today's-dollar series by the average path's factor `(1 + π̄) ** t`.

**Tech Stack:** Python 3.14, numpy, plotly, stdlib `http.server` GUI (vanilla JS), pytest. All under `src/stockanalysis/retirement/`.

**Spec:** `docs/superpowers/specs/2026-10-07-inflation-spending-design.md`

## Global Constraints

- Today's dollars stay the engine's unit; general inflation is Canada's all-items CPI (`rules.CPI`, 1986–2025, π̄ = `rules.historical_inflation()` ≈ 2.42%).
- Shock paths: 5-year blocks of `rules.CPI`, block starts chosen so the block fits (start index 0 … len−5), own RNG stream `np.random.default_rng([seed, INFLATION_STREAM])` with `INFLATION_STREAM = 1` (a numpy seed can't take a string; the spec's `(seed, "inflation")` means "its own stream"). Return and lifespan draws must not change.
- Return lag: `real_t = (1 + R_t) * (1 + π̄) / (1 + I_t) - 1`; with shocks off `I_t = π̄`, so returns are unchanged.
- Cost growth defaults (rate above CPI, `rules.COST_GROWTH`, each with an https source and a year 2016–2026): care +0.011, education +0.004, property_tax +0.065, insurance +0.062. **Base spending's code default is 0.0**; the owner's +0.0154 (≈4 % a year in dollars) goes in their private `retirement/plan.json` (Task 9) — `rules.py` holds only sourced figures.
- Every rate input validated to [−0.05, 0.15]; a bad value raises `inputs.PlanError` naming the field (e.g. `cost_growth.care`).
- Privacy: no owner values in tracked files, tests or commit messages (repo is public; `tests/test_privacy.py`).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `source venv/bin/activate && pytest -q <files>`.

## Review Focus

1. **A plan with `returns.inflation` set to a number** — expect steady inflation at that rate with no shocks and no return lag, even if `inflation_shocks` is true (Task 2 test `test_a_fixed_inflation_rate_means_no_shocks`).
2. **A plan horizon longer than the 40-year CPI history** (young person, end_age 110) — expect a full-length path built from repeated blocks, no index error (Task 2 test `test_paths_cover_horizons_longer_than_the_history`).
3. **A plan with no home** while property-tax/insurance growth is set — expect no crash and spending unchanged (Task 3 test `test_cost_growth_without_a_home`).
4. **All growth 0 and shocks off** — expect results identical to before this feature (Task 3 test `test_zero_growth_and_steady_inflation_change_nothing`).
5. **GUI future-dollars switch when chart traces have different lengths** (home-value line vs bands) — only traces whose y length matches the factor series are scaled (Task 8, JS guarded by length check; test asserts `future_factor` length equals the chart's x length).

---

### Task 1: Cost-growth rules and plan inputs

**Files:**
- Modify: `src/stockanalysis/retirement/rules.py` (after `historical_inflation`, and the `all_rules` name tuple)
- Modify: `src/stockanalysis/retirement/inputs.py` (`Returns`, new `CostGrowth`, `PlanInputs`, `_build`, `validate`, `defaults`)
- Test: `tests/test_retirement_inputs.py`, `tests/test_retirement_rules.py`

**Interfaces:**
- Produces: `rules.COST_GROWTH: dict[str, Rule]` with keys `care, education, property_tax, insurance`; `inputs.CostGrowth` with fields `base, care, education, property_tax, insurance: float | None` and method `rate(name) -> float`; `Returns.inflation_shocks: bool = True`; `PlanInputs.cost_growth: CostGrowth`; `inputs.COST_KINDS = ("base", "care", "education", "property_tax", "insurance")`; `inputs.defaults()["cost_growth"]` (dict of default rates) and `["returns"]` (`{"inflation_shocks": True}`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_retirement_inputs.py`)

```python
def test_cost_growth_defaults_come_from_rules_and_base_is_zero():
    g = inputs.parse(copy.deepcopy(inputs.TEMPLATE)).cost_growth
    assert g.rate("base") == 0.0
    for name in ("care", "education", "property_tax", "insurance"):
        assert g.rate(name) == rules.COST_GROWTH[name].value


def test_cost_growth_overrides_and_validation():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["cost_growth"] = {"base": 0.0154, "care": -0.01}
    g = inputs.parse(d).cost_growth
    assert g.rate("base") == 0.0154 and g.rate("care") == -0.01
    d["cost_growth"] = {"care": 0.5}
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == "cost_growth.care"


def test_inflation_shocks_default_on_and_defaults_cover_the_new_inputs():
    assert inputs.parse(copy.deepcopy(inputs.TEMPLATE)).returns.inflation_shocks is True
    dflt = inputs.defaults()
    assert dflt["returns"] == {"inflation_shocks": True}
    assert dflt["cost_growth"]["base"] == 0.0 and dflt["cost_growth"]["care"] == rules.COST_GROWTH["care"].value
```

Add `from stockanalysis.retirement import rules` to the test file's imports if missing.

Append to `tests/test_retirement_rules.py`:

```python
def test_cost_growth_rules_are_rates_above_cpi():
    assert set(rules.COST_GROWTH) == {"care", "education", "property_tax", "insurance"}
    for name, rule in rules.COST_GROWTH.items():
        assert -0.05 <= rule.value <= 0.15, name
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_inputs.py tests/test_retirement_rules.py -k "cost_growth or shocks"`
Expected: FAIL (`rules` has no attribute `COST_GROWTH`).

- [ ] **Step 3: Implement**

In `rules.py`, after `historical_inflation`:

```python
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
```

Add `"COST_GROWTH"` to the names tuple in `all_rules()` (next to `"CPI"`).

In `inputs.py`:

```python
COST_KINDS = ("base", "care", "education", "property_tax", "insurance")


@dataclass(frozen=True)
class CostGrowth:
    """Yearly growth above Canada's CPI per cost, in today's dollars; None = the default
    (rules.COST_GROWTH; base spending defaults to 0)."""
    base: float | None = None
    care: float | None = None
    education: float | None = None
    property_tax: float | None = None
    insurance: float | None = None

    def rate(self, name: str) -> float:
        value = getattr(self, name)
        if value is not None:
            return value
        return rules.COST_GROWTH[name].value if name in rules.COST_GROWTH else 0.0
```

`Returns` gains `inflation_shocks: bool = True` (after `inflation`). `PlanInputs` gains `cost_growth: CostGrowth = CostGrowth()` (beside `nonreg_income`). In `_build`, add `cost_growth=CostGrowth(**(d.get("cost_growth") or {})),`. In `validate`, after the `returns.inflation` check:

```python
    for name in COST_KINDS:
        v = getattr(plan.cost_growth, name)
        if v is not None and not (_is_number(v) and -0.05 <= v <= 0.15):
            _fail(f"cost_growth.{name}", "a yearly rate above inflation between -0.05 and 0.15")
```

In `defaults()` add two keys:

```python
            "returns": flags(Returns),
            "cost_growth": {name: CostGrowth().rate(name) for name in COST_KINDS},
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_inputs.py tests/test_retirement_rules.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/rules.py src/stockanalysis/retirement/inputs.py tests/test_retirement_inputs.py tests/test_retirement_rules.py
git commit -m "feat(retirement): cost-growth rates and inflation-shock inputs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Inflation paths, return lag and the per-path price level

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (`draw_futures`, new `draw_inflation`/`lag_returns`, `simulate` signature and its `deflate` use, `Projection`, `run`)
- Modify: `tests/test_retirement_engine.py` (lines unpacking `draw_futures`: `R, D = …` → `R, D, _ = …`; `R, _ = …` → `R, _, _ = …`)
- Test: `tests/test_retirement_engine.py`

**Interfaces:**
- Consumes: `Returns.inflation_shocks`, `Returns.inflation_rate`, `rules.CPI` (Task 1 / existing).
- Produces: `engine.INFLATION_BLOCK = 5`, `engine.INFLATION_STREAM = 1`; `engine.draw_inflation(plan, paths, years, seed) -> np.ndarray (years, paths)`; `engine.lag_returns(returns, inflation, average) -> np.ndarray`; `engine.draw_futures(plan, paths, seed) -> (returns, deaths, inflation)` (returns already lagged); `simulate(plan, returns, deaths=None, inflation=None)` where inflation is `(≥T, N)` or None (= steady π̄); `Projection.inflation (T, N)` and `Projection.price_level (T+1, N)` (price level on each January 1, 1.0 at t=0).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_retirement_engine.py`)

```python
def _shocky(**ret):
    p = plan()
    return replace(p, returns=replace(p.returns, inflation=None, inflation_shocks=True, **ret))


def test_inflation_paths_are_5_year_blocks_of_history():
    hist = [rules.CPI.value[y] for y in sorted(rules.CPI.value)]
    I = engine.draw_inflation(_shocky(), paths=50, years=12, seed=3)
    assert I.shape == (12, 50)
    for n in range(50):
        for start in (0, 5):                      # each full block is a run of history
            block = list(I[start:start + 5, n])
            assert any(hist[k:k + 5] == block for k in range(len(hist) - 4))
    assert np.array_equal(I, engine.draw_inflation(_shocky(), 50, 12, 3))   # seeded


def test_paths_cover_horizons_longer_than_the_history():
    I = engine.draw_inflation(_shocky(), paths=3, years=73, seed=1)
    assert I.shape == (73, 3) and np.isfinite(I).all()


def test_a_fixed_inflation_rate_means_no_shocks():
    I = engine.draw_inflation(_shocky(inflation=0.03), paths=4, years=6, seed=1)
    assert np.all(I == 0.03)
    off = replace(_shocky(), returns=replace(_shocky().returns, inflation_shocks=False))
    assert np.allclose(engine.draw_inflation(off, 4, 6, 1), rules.historical_inflation())


def test_returns_lag_inflation_only_in_the_short_run():
    avg = 0.025
    R = np.array([[0.05], [0.05], [0.05]])
    I = np.array([[avg], [0.068], [0.003]])
    lagged = engine.lag_returns(R, I, avg)
    assert lagged[0, 0] == pytest.approx(0.05)
    assert lagged[1, 0] < 0.05 and lagged[2, 0] > 0.05


def test_inflation_draws_leave_returns_and_lifespans_alone():
    p = _shocky()
    R1, D1, I1 = engine.draw_futures(p, 30, 5)
    steady = replace(p, returns=replace(p.returns, inflation_shocks=False))
    R0, D0, I0 = engine.draw_futures(steady, 30, 5)
    assert np.array_equal(D1, D0)
    unlagged = (1 + R1) * (1 + I1) / (1 + rules.historical_inflation()) - 1
    assert np.allclose(unlagged, R0)


def test_price_level_and_nominal_cost_base_follow_each_path():
    p = plan(accounts=[Account("A", "nonreg", 1_000_000.0, cost=1_000_000.0)], base=0.0, end_age=63)
    p = replace(p, withdrawal=replace(p.withdrawal, tfsa_top_up=False))
    T = engine.steps(p)
    infl = np.array([[0.10], [0.0], [0.0]])[:T]
    proj = engine.simulate(p, np.zeros((T, 1)), None, infl)
    assert proj.price_level[0, 0] == 1.0 and proj.price_level[1, 0] == pytest.approx(1.10)
    assert proj.inflation[0, 0] == 0.10
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_engine.py -k "inflation or lag or price_level or horizons"`
Expected: FAIL (`engine` has no `draw_inflation`).

- [ ] **Step 3: Implement**

In `engine.py`, near `draw_returns`:

```python
INFLATION_BLOCK = 5      # years drawn together, so a 1986-91 or 2021-23 run stays a run
INFLATION_STREAM = 1     # its own random stream: return and lifespan draws don't change


def draw_inflation(plan: PlanInputs, paths: int, years: int, seed: int) -> np.ndarray:
    """(years, paths) yearly inflation. Shocks on and no fixed rate: consecutive
    INFLATION_BLOCK-year runs of rules.CPI, each starting at a random year that fits.
    Otherwise a steady rate (returns.inflation, else the CPI average)."""
    r = plan.returns
    if not r.inflation_shocks or r.inflation is not None:
        return np.full((years, paths), r.inflation_rate)
    hist = np.array([rules.CPI.value[y] for y in sorted(rules.CPI.value)])
    blocks = -(-years // INFLATION_BLOCK)
    rng = np.random.default_rng([seed, INFLATION_STREAM])
    starts = rng.integers(0, len(hist) - INFLATION_BLOCK + 1, size=(paths, blocks))
    idx = (starts[:, :, None] + np.arange(INFLATION_BLOCK)).reshape(paths, -1)[:, :years]
    return hist[idx].T


def lag_returns(returns: np.ndarray, inflation: np.ndarray, average: float) -> np.ndarray:
    """Real returns when investments don't react to inflation within the year: a
    year at the average is unchanged, a high-inflation year loses, a low one gains."""
    return (1 + returns) * (1 + average) / (1 + inflation) - 1
```

Replace `draw_futures`:

```python
def draw_futures(plan: PlanInputs, paths: int, seed: int) -> tuple:
    """(lagged real returns, death ages, inflation) over life_steps: one set of
    futures every comparison shares."""
    r, years = plan.returns, life_steps(plan)
    inflation = draw_inflation(plan, paths, years, seed)
    returns = lag_returns(draw_returns(r.mean, r.sd, paths, years, seed), inflation, r.inflation_rate)
    return returns, mortality.draw_death_ages(plan.people, plan.start_year, paths, seed), inflation
```

`simulate` signature and docstring:

```python
def simulate(plan: PlanInputs, returns: np.ndarray, deaths: np.ndarray | None = None,
             inflation: np.ndarray | None = None) -> Projection:
    """Project ``plan`` over ``returns``: (T, N) real yearly returns. ``deaths`` is a
    (P, N) array of death ages (see mortality); None lets everyone live all T years.
    ``inflation`` (≥T, N) is each future's yearly CPI change; None is a steady
    returns.inflation_rate."""
```

Inside `simulate`, replace the line `deflate = 1 / (1 + plan.returns.inflation_rate)   # …` with:

```python
    infl = (np.full((T, N), plan.returns.inflation_rate) if inflation is None
            else np.asarray(inflation, dtype=float)[:T])
    level = np.ones((T + 1, N))                       # price level on January 1, today = 1
    level[1:] = np.cumprod(1 + infl, axis=0)
```

and in the growth step replace the three `*= deflate` lines with `/= 1 + infl[t]`:

```python
        cost /= 1 + infl[t]
        ...
            resp_in /= 1 + infl[t]               # contributions come back at face value
            resp_grant /= 1 + infl[t]
```

Add to `Projection` (after `tfsa_top_up`):

```python
    inflation: np.ndarray | None = None          # (T, N) yearly CPI change in each future
    price_level: np.ndarray | None = None        # (T+1, N) price level on January 1 (today = 1)
```

and pass `inflation=infl, price_level=level` in the `Projection(...)` return.

In `run`:

```python
    R, D, I = draw_futures(plan, r.paths if paths is None else paths, r.seed if seed is None else seed)
    simulated = simulate(plan, R, D, I)
    T, g, fixed = steps(plan), median_return(r.mean, r.sd), average_deaths(plan)
    average = simulate(plan, np.full((T, 1), g), fixed)
    k = bad_luck_index(simulate(plan, R[:T], np.repeat(fixed, R.shape[1], axis=1), I[:T]))
    return PlanResult(plan, simulated, average, simulate(plan, R[:T, [k]], fixed, I[:T, [k]]), g, k)
```

Update the two test unpackings in `tests/test_retirement_engine.py` (`R, D = engine.draw_futures(p, 200, 3)` → `R, D, _ = …`; `R, _ = engine.draw_futures(p, 300, 3)` → `R, _, _ = …`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_engine.py tests/test_retirement_scenarios.py tests/test_retirement_optimize.py`
Expected: PASS (scenarios/optimize use `*draw_futures`, so they receive the third array).

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/test_retirement_engine.py
git commit -m "feat(retirement): inflation paths from CPI history; returns lag shocks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Costs that grow faster (or slower) than inflation

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (spending block `# 2. The household's after-tax spending need`, the care line)
- Modify: `src/stockanalysis/retirement/education.py` (`schedule`, the line `cost[t] += e.costs[kid.living]`)
- Test: `tests/test_retirement_engine.py`, `tests/test_retirement_education.py`

**Interfaces:**
- Consumes: `PlanInputs.cost_growth.rate(name)` (Task 1).
- Produces: `engine.growth(plan, name, t) -> float` = `(1 + plan.cost_growth.rate(name)) ** t`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_retirement_engine.py`)

```python
from stockanalysis.retirement.inputs import CostGrowth


def _grow(p, **rates):
    return replace(p, cost_growth=CostGrowth(**rates))


def test_zero_growth_and_steady_inflation_change_nothing():
    p = plan(accounts=[Account("A", "rrsp", 500_000.0)], base=40_000.0, end_age=66,
             home=Home(value=800_000.0, property_tax=6_000.0, insurance=2_000.0))
    zero = _grow(p, base=0.0, care=0.0, education=0.0, property_tax=0.0, insurance=0.0)
    np.testing.assert_allclose(run_flat(zero).need[:, 0], 40_000.0)


def test_base_and_home_costs_grow_at_their_own_rates():
    p = plan(base=40_000.0, end_age=66, home=Home(value=800_000.0, property_tax=6_000.0, insurance=2_000.0),
             accounts=[Account("A", "tfsa", 2_000_000.0)])
    g = _grow(p, base=0.01, property_tax=0.05, insurance=0.0, care=0.0, education=0.0)
    need = run_flat(g).need[:, 0]
    t = 4
    expected = (40_000 - 8_000) * 1.01 ** t + 6_000 * 1.05 ** t + 2_000
    assert need[t] == pytest.approx(expected)


def test_downsizing_scales_the_grown_home_costs():
    home = Home(value=800_000.0, downsize_age=62, new_value=400_000.0, property_tax=6_000.0, insurance=2_000.0)
    p = plan(base=40_000.0, end_age=66, home=home, accounts=[Account("A", "tfsa", 2_000_000.0)])
    g = _grow(p, base=0.0, property_tax=0.05, insurance=0.0, care=0.0, education=0.0)
    need = run_flat(g).need[:, 0]
    t = 3                                            # after downsizing at 62 (age 60 at t=0)
    expected = 32_000 + (6_000 * 1.05 ** t + 2_000) * 0.5
    assert need[t] == pytest.approx(expected)


def test_spending_can_lag_inflation():
    p = plan(base=40_000.0, end_age=66, accounts=[Account("A", "tfsa", 2_000_000.0)])
    need = run_flat(_grow(p, base=-0.01, care=0.0)).need[:, 0]
    assert need[5] == pytest.approx(40_000 * 0.99 ** 5)


def test_cost_growth_without_a_home():
    p = plan(base=40_000.0, end_age=63, accounts=[Account("A", "tfsa", 1_000_000.0)])
    need = run_flat(_grow(p, property_tax=0.1, insurance=0.1, base=0.0)).need[:, 0]
    np.testing.assert_allclose(need, 40_000.0)


def test_care_costs_grow():
    p = plan(base=0.0, end_age=66, accounts=[Account("A", "tfsa", 2_000_000.0)],
             no_go_age=60, care=10_000.0)
    need = run_flat(_grow(p, care=0.02, base=0.0)).need[:, 0]
    assert need[3] == pytest.approx(10_000 * 1.02 ** 3)
```

Append to `tests/test_retirement_education.py`:

```python
def test_education_costs_grow_with_their_rate():
    from stockanalysis.retirement.inputs import CostGrowth
    p = plan_with([{"name": "K", "age": 15, "living": "home"}], costs={"home": 10_000, "away": 30_000})
    s = education.schedule(replace(p, cost_growth=CostGrowth(education=0.02)), engine.steps(p))
    assert s.cost[3] == pytest.approx(10_000 * 1.02 ** 3)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_engine.py tests/test_retirement_education.py -k "grow or lag or without_a_home or zero_growth or care_costs"`
Expected: FAIL (needs don't grow).

- [ ] **Step 3: Implement**

In `engine.py`, first rename the local array `growth` in the RESP leftover block of `simulate` (the lines `back, growth = leftover`, `np.minimum(growth, aip[...])`, `taxable = growth - to_rrsp`) to `resp_growth` — otherwise Python treats `growth` as a local name throughout `simulate` and the helper below raises `UnboundLocalError`. Then add near `stage_share`:

```python
def growth(plan: PlanInputs, name: str, t: int) -> float:
    """A cost's factor in year t, in today's dollars: its rate above CPI, compounded."""
    return (1 + plan.cost_growth.rate(name)) ** t
```

Replace the start of the spending block:

```python
        # 2. The household's after-tax spending need, each part growing at its own rate.
        level = spend.base + sum(c.amount for c in spend.changes if c.year <= year)
        if downsized:
            level -= (home.property_tax + home.insurance) * (1 - home.new_value / home.value)
        need = np.full(N, max(level, 0.0) * stage_share(spend, ages[0]))
```

with:

```python
        # 2. The household's after-tax spending need, each part growing at its own rate.
        level = spend.base + sum(c.amount for c in spend.changes if c.year <= year)
        if home is not None:
            tax_now, ins_now = home.property_tax, home.insurance
            shrink = home.new_value / home.value if downsized else 1.0
            level = ((level - tax_now - ins_now) * growth(plan, "base", t)
                     + (tax_now * growth(plan, "property_tax", t)
                        + ins_now * growth(plan, "insurance", t)) * shrink)
        else:
            level = level * growth(plan, "base", t)
        need = np.full(N, max(level, 0.0) * stage_share(spend, ages[0]))
```

Replace `need = need + spend.care` with `need = need + spend.care * growth(plan, "care", t)`.

In `education.py`, inside `schedule`, replace `cost[t] += e.costs[kid.living]` with:

```python
                cost[t] += e.costs[kid.living] * (1 + plan.cost_growth.rate("education")) ** t
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_engine.py tests/test_retirement_education.py tests/test_retirement_actions.py tests/test_retirement_report.py`
Expected: PASS. (Existing tests use plans whose cost-growth defaults now apply; any test that pins an exact `need` with a home or care must set `cost_growth=CostGrowth(base=0, care=0, education=0, property_tax=0, insurance=0)` in its plan — add that to the engine test helper `plan()` by passing `cost_growth=CostGrowth(0.0, 0.0, 0.0, 0.0, 0.0)` to `PlanInputs(...)` so zero-return tests stay exact.)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/engine.py src/stockanalysis/retirement/education.py tests/test_retirement_engine.py tests/test_retirement_education.py
git commit -m "feat(retirement): base, home, care and education costs grow at their own rates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Fixed-dollar amounts shrink with the price level

**Files:**
- Modify: `src/stockanalysis/retirement/tax.py` (`income_tax` param `fixed_scale`, `_person`)
- Modify: `src/stockanalysis/retirement/engine.py` (set `part["fixed_scale"]` after each `_parts(...)` call; student grant line; AIP limit line)
- Test: `tests/test_retirement_tax.py`, `tests/test_retirement_engine.py`, `tests/test_retirement_education.py`

**Interfaces:**
- Consumes: `level` (Task 2) inside `simulate`.
- Produces: `tax.income_tax(..., fixed_scale=1.0)`; parts dict key `"fixed_scale"` read by `tax._person`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retirement_tax.py`:

```python
def test_the_federal_pension_credit_shrinks_with_prices():
    full = t(pension=40_000, age=70)
    shrunk = float(tax.income_tax(pension=40_000, age=70, fixed_scale=0.5))
    # Half the C$2,000 credit at 14%: 140 more federal tax.
    assert shrunk - full == pytest.approx(0.14 * 1_000, abs=0.01)
```

Append to `tests/test_retirement_engine.py`:

```python
def test_fixed_amounts_shrink_in_the_engine():
    p = plan(people=[person(age=70, rrif_start_age=65)], accounts=[Account("A", "rrsp", 400_000.0)],
             base=30_000.0, end_age=75)
    p = _grow(p, base=0.0, care=0.0, education=0.0, property_tax=0.0, insurance=0.0)
    T = engine.steps(p)
    steady = engine.simulate(p, np.zeros((T, 1)), None, np.zeros((T, 1)))
    hot = engine.simulate(p, np.zeros((T, 1)), None, np.full((T, 1), 0.10))
    assert hot.tax[4, 0] > steady.tax[4, 0]              # a smaller pension credit
```

Append to `tests/test_retirement_education.py`:

```python
def test_the_student_grant_shrinks_with_prices():
    p = _retired_family("tfsa")
    T = engine.steps(p)
    steady = engine.simulate(p, np.zeros((T, 1)), None, np.zeros((T, 1)))
    hot = engine.simulate(p, np.zeros((T, 1)), None, np.full((T, 1), 0.10))
    assert hot.student_grant[1, 0] == pytest.approx(steady.student_grant[1, 0] / 1.10)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_tax.py tests/test_retirement_engine.py tests/test_retirement_education.py -k "shrink"`
Expected: FAIL (`income_tax` has no `fixed_scale`).

- [ ] **Step 3: Implement**

In `tax.py`, add `fixed_scale=1.0` to `income_tax`'s keyword params (document: "the fixed C$2,000 federal pension amount scaled by this; it isn't indexed") and change the federal pension line to:

```python
    fed_pension = np.where(over_65, np.minimum(pension, fed["pension_amount"].value * fixed_scale), 0.0)
```

In `_person`, pass `fixed_scale=part.get("fixed_scale", 1.0)`.

In `engine.py`, right after each `parts = _parts(...)` line in the tax loop (and before `_attribute`), add:

```python
                for part in parts:
                    part["fixed_scale"] = 1 / level[t]       # unindexed amounts shrink with prices
```

Student grant line becomes:

```python
                csg_rec[t] = np.minimum(school.students[t] * education.student_grant(last_income, family)
                                        / level[t], cost_t)
```

AIP limit line becomes:

```python
                to_rrsp = (np.minimum(resp_growth, aip["rrsp_transfer_max"] / level[t])
```

(`resp_growth` is the RESP-growth array renamed in Task 3.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_*.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/tax.py src/stockanalysis/retirement/engine.py tests/test_retirement_tax.py tests/test_retirement_engine.py tests/test_retirement_education.py
git commit -m "feat(retirement): unindexed amounts shrink with each future's prices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: "Steady inflation (no shocks)" what-if

**Files:**
- Modify: `src/stockanalysis/retirement/scenarios.py` (`variants`)
- Test: `tests/test_retirement_scenarios.py`

**Interfaces:**
- Produces: variant key `"steady_inflation"`, label `"Steady inflation (no shocks)"`.

- [ ] **Step 1: Write the failing test**

```python
def test_steady_inflation_is_offered_when_shocks_are_on(plan):
    keys = [k for k, _, _ in scenarios.variants(plan)]
    assert "steady_inflation" in keys
    steady = replace(plan, returns=replace(plan.returns, inflation_shocks=False))
    assert "steady_inflation" not in [k for k, _, _ in scenarios.variants(steady)]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_scenarios.py -k steady`
Expected: FAIL.

- [ ] **Step 3: Implement** — at the end of `variants` before `return out`:

```python
    r = plan.returns
    if r.inflation_shocks and r.inflation is None:
        out.append(("steady_inflation", "Steady inflation (no shocks)",
                    replace(plan, returns=replace(r, inflation_shocks=False))))
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_scenarios.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/scenarios.py tests/test_retirement_scenarios.py
git commit -m "feat(retirement): steady-inflation what-if

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Action plan amounts in either kind of dollars

**Files:**
- Modify: `src/stockanalysis/retirement/actions.py` (`Action`, every construction with an amount)
- Test: `tests/test_retirement_actions.py`

**Interfaces:**
- Produces: `Action.amounts: tuple = ()` (today's dollars) and `Action.text(factor: float = 1.0) -> str` returning `what` with `{}` placeholders filled by `C$` amounts × factor; `what` keeps placeholders. `actions.future_factor(plan, year) -> float` = `(1 + plan.returns.inflation_rate) ** (year - plan.start_year)`.

- [ ] **Step 1: Write the failing test**

```python
def test_action_amounts_render_in_future_dollars(result):
    acts = [a for a in actions.plan_actions(result) if a.amounts]
    assert acts
    a = acts[0]
    f = actions.future_factor(result.inputs, a.year)
    assert a.text() != a.text(f) or f == 1.0
    assert f"C${a.amounts[0] * f:,.0f}" in a.text(f)
    assert "{" not in a.text()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_actions.py -k future`
Expected: FAIL (`Action` has no `amounts`).

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class Action:
    year: int
    until: int | None
    who: str
    what: str                # may hold {} placeholders, one per amount
    why: str
    amounts: tuple = ()      # today's dollars, in placeholder order

    def text(self, factor: float = 1.0) -> str:
        return self.what.format(*(_money(a * factor) for a in self.amounts))


def future_factor(plan, year: int) -> float:
    """Today's dollars → that year's dollars on the average inflation path."""
    return (1 + plan.returns.inflation_rate) ** (year - plan.start_year)
```

Change `_amount(typical, until)` to return a template: `"about {} a year" if until else "{}"`, and every Action built with it passes `amounts=(typical,)`. The downsizing action becomes `what="Sell the home and buy one for about {}; invest the {} freed up"`, `amounts=(h.new_value, h.released)`. RESP contribution: `what="Contribute {} to the RESP in January"`, `amounts=(c,)` (its `why` keeps `_money(g)` as plain text — the grant is a fixed rule amount). Action lines without amounts are unchanged (`amounts=()`, `text()` returns `what`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_actions.py`
Expected: PASS (existing tests that read `a.what` for text containing amounts: change them to `a.text()`).

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/actions.py tests/test_retirement_actions.py
git commit -m "feat(retirement): action amounts render in today's or future dollars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Report — today's / future dollars switch and inflation assumptions

**Files:**
- Modify: `src/stockanalysis/retirement/report.py` (`income_chart`, `money_left_chart`, `_year_table`, `_education`, `_actions`, `_assumptions`, `build_report`, `_STYLE`)
- Test: `tests/test_retirement_report.py`

**Interfaces:**
- Consumes: `actions.Action.text`, `actions.future_factor` (Task 6); `Returns.inflation_rate`; `rules.COST_GROWTH`.
- Produces: `report.future_scale(plan, n) -> np.ndarray` = `(1 + π̄) ** arange(n)`; optional `scale: np.ndarray | None = None` on `income_chart`, `money_left_chart`, `_year_table`, `_education`; `report.DOLLARS_ID = "dollars"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_future_scale_compounds_the_average_inflation():
    p = inputs.parse(inputs.TEMPLATE)
    s = report.future_scale(p, 3)
    pi = p.returns.inflation_rate
    np.testing.assert_allclose(s, [1, 1 + pi, (1 + pi) ** 2])


def test_report_has_both_dollar_views_and_a_switch(built):
    html = _html(built)
    assert 'id="dollars"' in html and "dollars-today" in html and "dollars-future" in html
    assert "Future dollars" in html and "Today's dollars" in html


def test_charts_scale_into_future_dollars(built):
    _, result, _, _ = built
    avg, bad = result.average, result.bad_luck
    scale = report.future_scale(result.inputs, len(avg.years))
    today = report.income_chart(avg, bad)
    future = report.income_chart(avg, bad, scale)
    bar = next(i for i, tr in enumerate(today.data) if tr.type == "bar")
    assert future.data[bar].y[-1] == pytest.approx(today.data[bar].y[-1] * scale[-1])


def test_assumptions_name_the_inflation_and_cost_growth(built):
    html = _html(built)
    assert "Costs rising faster than inflation" in html and "Canada's CPI" in html
```

(`built` and `_html` are this test file's existing fixture/helper; add `import numpy as np` and `from stockanalysis.retirement import inputs` if missing.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_report.py -k "future or dollar or cost_growth"`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
DOLLARS_ID = "dollars"


def future_scale(plan, n: int) -> np.ndarray:
    """Today's dollars → each year's dollars on the average inflation path."""
    return (1 + plan.returns.inflation_rate) ** np.arange(n)
```

- `income_chart(avg, bad, scale=None)`: `k = 1 if scale is None else np.asarray(scale)[:len(proj.years)]`; bar `y=proj.income[s][:, 0] * k`; tax customdata `proj.tax[:, 0] * k`.
- `money_left_chart(sim, plan, scale=None)`: after `inv = sim.investments[:rows]`, `if scale is not None: inv = inv * np.asarray(scale)[:rows, None]`; home-value trace y also `* scale[:rows]`.
- `_year_table(proj, scale=None)`: build `cols` as now, then `if scale is not None: cols = [(g, h, s * np.asarray(scale)[:len(s)]) for g, h, s in cols]`.
- `_education(plan, avg, bad, scale=None)`: multiply each money cell `s.cost[t]`, `grants[t]`, `resp_pays[t]`, `uncovered[t]`, `avg.resp[t, 0]` by `k = 1 if scale is None else scale[t]` in the row f-string (tiles stay today's dollars).
- `_actions(result, future=False)`: use `a.text(actions.future_factor(result.inputs, a.year) if future else 1.0)`; the intro note says "Amounts are in today's dollars" / "in each year's dollars (average inflation path)".
- In `build_report`, `scale = future_scale(plan, len(avg.years) + 1)` and a helper:

```python
    def both(today_html: str, future_html: str) -> str:
        return (f"<div class='dollars-today'>{today_html}</div>"
                f"<div class='dollars-future'>{future_html}</div>")
```

  Use `both(...)` for the actions section, the education section, the income chart (`_fig(income_chart(avg, bad), False)` / `_fig(income_chart(avg, bad, scale), False)`), the money-left chart, and the year table.
- Header gets the switch (inside `<header>` after the generated line):

```python
    switch = (f"<div id='{DOLLARS_ID}' class='switch'>"
              "<button class='on' onclick=\"setDollars(false,this)\">Today's dollars</button>"
              "<button onclick=\"setDollars(true,this)\">Future dollars</button></div>"
              "<script>function setDollars(f,b){document.body.classList.toggle('future',f);"
              "for(const x of b.parentNode.children)x.classList.toggle('on',x===b);"
              "window.dispatchEvent(new Event('resize'));}</script>")
```

- `_STYLE` adds: `body:not(.future) .dollars-future{{display:none}} body.future .dollars-today{{display:none}} .switch button{{border:1px solid {AXIS};background:#fff;padding:4px 10px;border-radius:6px}} .switch button.on{{background:{SERIES[0]};color:#fff}}`.
- `_assumptions`: after the "Inflation" fact add

```python
        ("Costs rising faster than inflation", ", ".join(
            f"{label} {plan.cost_growth.rate(name):+.2%}" for name, label in (
                ("base", "base spending"), ("care", "care"), ("education", "education"),
                ("property_tax", "property tax"), ("insurance", "home insurance")))
         + " a year; shown in today's dollars, so the Future dollars view adds inflation on top"),
```

  and change the Inflation fact's wording to mention shocks: `f"{r.inflation_rate:.2%} a year on average (Canada's CPI, last 40 years)" + ("; each future replays 5-year runs of past inflation and returns lag it in high-inflation years" if r.inflation_shocks and r.inflation is None else "; steady")`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_report.py tests/test_retirement_actions.py tests/test_cli_retire.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/report.py tests/test_retirement_report.py
git commit -m "feat(retirement): report switch between today's and future dollars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: GUI — cost-growth inputs, shocks checkbox and the dollars switch

**Files:**
- Modify: `src/stockanalysis/retirement/gui.py` (`summarize`, `plan_state`)
- Modify: `src/stockanalysis/retirement/static/planner.html` (Investing tab card, results switch, `showResults`)
- Test: `tests/test_retirement_gui.py`

**Interfaces:**
- Consumes: `report.future_scale` (Task 7), `inputs.defaults()["cost_growth"]` and `["returns"]` (Task 1).
- Produces: preview JSON keys `future_factor: list[float]` (one per point of the money-left chart's x), `legacy_factor: float`, `retire_factor: float`.

- [ ] **Step 1: Write the failing test**

```python
def test_preview_carries_future_dollar_factors(server):
    status, r = _req(server, "POST", "/api/preview", {"plan": inputs.TEMPLATE})
    assert status == 200
    x = r["chart"]["data"][0]["x"]
    assert len(r["future_factor"]) == len(x) and r["future_factor"][0] == 1.0
    assert r["legacy_factor"] > 1.0 and r["retire_factor"] >= 1.0
    page = _req(server, "GET", "/")[1].decode()
    assert 'id="dollars-switch"' in page and "cost_growth." in page
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/test_retirement_gui.py -k future`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `gui.summarize`, add:

```python
    avg = result.average
    chart = report.money_left_chart(sim, plan)
    n = len(chart.data[0].x)
    scale = report.future_scale(plan, max(n, len(avg.years) + 1))
    end = int(avg.end_step[0]) if avg.end_step is not None else len(avg.years)
```

and the keys `"chart": json.loads(chart.to_json())`, `"future_factor": scale[:n].tolist()`, `"legacy_factor": float(scale[min(end, len(scale) - 1)])`, `"retire_factor": float(scale[min(avg.retire_step, len(scale) - 1)])` (replacing the existing `"chart"` line).

In `planner.html`:
- Investing tab, after the inflation field:

```js
      ${check("returns.inflation_shocks", "Inflation shocks: each future replays runs of past inflation, and returns lag it", drawDefaults.inflation_shocks ?? true)}
    </div>
    <div class="card"><h3>Costs rising faster than inflation (% a year above CPI)</h3><div class="grid2">
      ${[["base", "Base spending"], ["care", "Care"], ["education", "Education"],
         ["property_tax", "Property tax"], ["insurance", "Home insurance"]].map(([k, l]) =>
        number(`cost_growth.${k}`, l, {min: -0.05, max: 0.15, step: 0.001, unit: "%", nullable: true,
               placeholder: pct(costDefaults[k], 2)})).join("")}
    </div><p class="note">Blank uses the default shown (Statistics Canada / City of Calgary data).
      A negative rate means the cost lags inflation.</p>
```

  and in `load()`: `({education: eduDefaults, kid: kidDefaults, withdrawal: drawDefaults, cost_growth: costDefaults, returns: retDefaults} = state.defaults);` with `let costDefaults = {}, retDefaults = {};` declared beside `drawDefaults`; use `retDefaults.inflation_shocks` as the checkbox fallback instead of `drawDefaults.inflation_shocks ?? true`.
- Results panel header (above the gauge): `<div id="dollars-switch" class="answer"><label class="check"><input type="checkbox" id="future-dollars"> Show future dollars</label></div>`; a `change` listener on `#future-dollars` re-runs `showResults(lastPreview)`.
- In `showResults(r)`, when `$("future-dollars").checked`: clone `r.chart`, and for each trace whose `y` length equals `r.future_factor.length`, set `y = y.map((v, i) => v * r.future_factor[i])`; scale the legacy tile value by `r.legacy_factor` and investments-at-retirement by `r.retire_factor`; append " (future dollars)" to those tiles' labels; lifetime taxes stays in today's dollars with "(today's dollars)".

- [ ] **Step 4: Run to verify pass**

Run: `pytest -q tests/test_retirement_gui.py`
Expected: PASS. Then start `stock-analysis retire --gui --no-browser --port 8796 --inputs <tmp copy of inputs.TEMPLATE>` and screenshot the Investing tab and the results with the switch on (headless Chrome, as in earlier sessions) to confirm the card renders and the chart rescales.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/gui.py src/stockanalysis/retirement/static/planner.html tests/test_retirement_gui.py
git commit -m "feat(retirement): GUI cost-growth inputs, shocks toggle, future-dollars switch

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Docs, the owner's plan, and the measured effect

**Files:**
- Modify: `src/stockanalysis/retirement/README.md` (Inflation section, plan.json table), `src/stockanalysis/retirement/CLAUDE.md` (engine row + Inflation convention), `.claude/skills/retirement-planning/SKILL.md` (plan.json table row)
- Modify (private, untracked): `retirement/plan.json` — add `"cost_growth": {"base": 0.0154}`

**Interfaces:** none new.

- [ ] **Step 1: Update the docs**

README "Inflation" section: replace with — general inflation is Canada's CPI (40-year average 2.42%); each future replays 5-year runs of past inflation and returns lag it (a high-inflation year cuts that year's real return); costs can grow faster than CPI via `cost_growth` (defaults care +1.1%, education +0.4%, property tax +6.5%, home insurance +6.2%, base 0) with sources; fixed amounts (C$2,000 pension credit, student grant, RESP-to-RRSP limit) shrink with each future's prices; the report and GUI switch between today's and future dollars; set `returns.inflation` to a number or `inflation_shocks: false` for steady inflation. plan.json table: add `cost_growth` row and `inflation_shocks` to `returns`. CLAUDE.md: engine row mentions `draw_futures → (returns, deaths, inflation)`, `draw_inflation`, `lag_returns`, `growth`; the Inflation convention bullet says `infl`/`level` per path drive the cost base and the fixed-amount erosion, and that `rules.CPI` and `rules.COST_GROWTH` are refreshed each January. SKILL.md: add `cost_growth` row.

- [ ] **Step 2: The owner's plan (private)**

```bash
python3 - <<'EOF'
import json
p = "retirement/plan.json"
d = json.load(open(p))
d.setdefault("cost_growth", {})["base"] = 0.0154    # the owner's ~4% a year in dollars
open(p, "w").write(json.dumps(d, indent=2) + "\n")
EOF
git status --short retirement/   # must print nothing (gitignored)
```

- [ ] **Step 3: Full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 4: Measure on the owner's plan** (print only; numbers go to the chat, never to tracked files)

```bash
python - <<'EOF'
from dataclasses import replace
import numpy as np
from stockanalysis import config
from stockanalysis.retirement import inputs, engine, cli
from stockanalysis.retirement.inputs import CostGrowth
plan, _ = cli._with_balances(inputs.load_inputs(config.DEFAULT_RETIREMENT_INPUTS), None)
flat = CostGrowth(0.0, 0.0, 0.0, 0.0, 0.0)
cases = [("before (steady, no cost growth)", replace(plan, cost_growth=flat, returns=replace(plan.returns, inflation_shocks=False))),
         ("shocks only", replace(plan, cost_growth=flat)),
         ("cost growth only", replace(plan, returns=replace(plan.returns, inflation_shocks=False))),
         ("both (the plan)", plan)]
for label, p in cases:
    r = engine.run(p, paths=10_000, seed=plan.returns.seed); a = r.average
    print(f"{label:<34} success {r.simulated.success:.1%}  legacy C${a.legacy[0]/1e6:.2f}M  median C${np.median(r.simulated.legacy)/1e6:.2f}M")
EOF
```

- [ ] **Step 5: Regenerate the report and commit the docs**

```bash
stock-analysis retire | tail -1
git add src/stockanalysis/retirement/README.md src/stockanalysis/retirement/CLAUDE.md .claude/skills/retirement-planning/SKILL.md
git commit -m "docs(retirement): inflation reaching spending

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
