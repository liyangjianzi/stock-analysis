# Historical Returns and a Stock/Bond Mix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw each simulated future's returns and inflation together from 1928–2025 history (US real stock/bond returns + Canadian CPI, shifted to the owner's averages) and blend them by a stock/bond mix that glides with people[0]'s age, keeping today's lognormal model as a switch.

**Architecture:** New historical series and FP Canada defaults live in `rules.py`. `inputs.Returns` gains `model`, `stocks`, `bonds`, `mix`. `engine.draw_futures` builds stock/bond/inflation paths from shared 5-year block indices, blends them into the single (years, paths) return array `simulate` already takes, so `simulate` is untouched. One helper `engine.average_returns` replaces every `median_return(r.mean, r.sd)` call.

**Tech Stack:** Python 3, numpy, stdlib `http.server` GUI with vanilla JS, pytest (offline).

**Spec:** `docs/superpowers/specs/2026-10-09-historical-returns-design.md`

## Global Constraints

- Every rule value is read from its source, never from memory (package CLAUDE.md). Sources: StatCan table 18-10-0004-01 (`CPI`), Damodaran `histretSP` (`US_RETURNS`), BLS CPI-U CUUR0000SA0 annual average M13 (`US_CPI`), FP Canada 2026 Projection Assumption Guidelines PDF (`RETURN_ASSUMPTIONS`: inflation 2.1%, fixed income 3.2%, Canadian equities 6.3%, nominal, geometric, before fees).
- Never send the owner's email or any personal data to an outside service; download with a generic User-Agent.
- The repo is public: no plan values, balances or report numbers from `retirement/` in tracked files, tests, docs or commits. Tests use invented plans (`inputs.TEMPLATE`, the engine test `plan()` helper).
- `deaths=None` stays the old engine; the lognormal model stays bit-for-bit today's code path (regression anchor).
- Comparisons share futures: same seed, same block indices; history mode never applies `lag_returns`.
- Run tests with `venv/bin/python -m pytest` from the project root; small path counts (40 runs, 20 scenarios).
- The working tree already has uncommitted dollars-switch and 1950-CPI work (`engine.py`, `report.py`, `rules.py`, docs, tests). Commit that first as its own commit (Task 0) so each task's commit holds only its own change.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- An existing `plan.json` without `model`/`stocks`/`bonds`/`mix` (the owner's) loads, runs in history mode with the defaults, and the GUI shows the default mix → Task 2 test `test_a_plan_without_the_new_fields_loads_with_the_defaults`, Task 6 test `test_defaults_carry_the_return_assumptions`.
- A saved scenario that changes a mix point (`returns.mix.1.1`) applies to the tuple-of-tuples mix → Task 2 test `test_a_saved_scenario_can_change_a_mix_point`.
- A horizon longer than the 98-year record wraps instead of crashing → Task 3 test `test_history_wraps_for_horizons_longer_than_the_record`.
- A fixed `returns.inflation` or shocks turned off in history mode gives steady inflation but still historical returns → Task 3 test `test_history_with_steady_inflation_keeps_historical_returns`.
- A mix already at 0% stocks gets no "10 points more bonds" what-if (it would be a no-op) → Task 4 test `test_more_bonds_skipped_when_already_all_bonds`.

---

### Task 0: Commit the pending work

**Files:** the already-modified files in `git status`.

- [ ] **Step 1: Run the retirement suite**

Run: `venv/bin/python -m pytest -q tests/test_retirement_*.py tests/test_cli_retire.py tests/test_privacy.py`
Expected: all pass (340+ tests, ~5 min).

- [ ] **Step 2: Ask the owner before committing** whether the dollars-switch work and the 1950 inflation draw go in one commit or two; commit as told, e.g.

```bash
git add -u
git commit -m "feat(retirement): inflation draws reach the 1970s (CPI from 1950, centred)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Historical series and FP Canada defaults in `rules.py`

**Files:**
- Modify: `src/stockanalysis/retirement/rules.py` (the `CPI` block near line 258, `all_rules` name list near line 315)
- Test: `tests/test_retirement_rules.py`, `tests/test_retirement_engine.py:968-973`

**Interfaces:**
- Produces: `rules.CPI.value` keys 1928..2025; `rules.US_RETURNS: Rule({year: (stocks, bonds)})` nominal fractions 1928..2025; `rules.US_CPI: Rule({year: change})` 1928..2025; `rules.RETURN_ASSUMPTIONS: Rule({"inflation": 0.021, "fixed_income": 0.032, "canadian_equities": 0.063}, 2026, src)`; `rules.real_return(kind: str) -> float`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_retirement_rules.py`; it already imports `pytest` and `rules`)

```python
def test_return_history_covers_the_same_years_as_cpi():
    years = list(range(1928, rules.CPI.year + 1))
    assert sorted(rules.CPI.value) == years
    assert sorted(rules.US_RETURNS.value) == years
    assert sorted(rules.US_CPI.value) == years
    assert rules.US_RETURNS.value[1931] == (-0.4384, -0.0256)    # S&P 500, 10-year Treasury
    assert rules.US_CPI.value[1932] == pytest.approx(-0.09868)
    assert rules.CPI.value[1948] == pytest.approx(0.14263)
    assert rules.CPI.value[1986] == pytest.approx(0.04195)       # stored years untouched


def test_return_assumptions_are_fp_canadas_made_real():
    assert rules.RETURN_ASSUMPTIONS.value == {"inflation": 0.021, "fixed_income": 0.032,
                                              "canadian_equities": 0.063}
    assert rules.real_return("canadian_equities") == pytest.approx(1.063 / 1.021 - 1)
    assert rules.real_return("fixed_income") == pytest.approx(1.032 / 1.021 - 1)


def test_new_series_are_listed_for_the_yearly_refresh():
    names = {n.split(".")[0] for n, _ in rules.all_rules()}
    assert {"us_returns", "us_cpi", "return_assumptions"} <= names
```

In `tests/test_retirement_engine.py` `test_default_inflation_is_canadas_40_year_average`, change `min(rules.CPI.value) == 1950` to `min(rules.CPI.value) == 1928`.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_rules.py -k "history or assumptions or refresh"`
Expected: FAIL with `AttributeError: module ... has no attribute 'US_RETURNS'`.

- [ ] **Step 3: Download the sources into a fresh temp dir** (generic User-Agent, never an email)

```bash
mkdir -p /tmp/rethist && cd /tmp/rethist
curl -s -A "Mozilla/5.0" -o histret.html https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histretSP.html
curl -s -A "Mozilla/5.0" -o cu.txt https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems
curl -s -A "Mozilla/5.0" -o cpi.zip https://www150.statcan.gc.ca/n1/tbl/csv/18100004-eng.zip && unzip -o -q cpi.zip
```

- [ ] **Step 4: Generate the literals** with this script, saved outside the download dir as `/tmp/rethist_build.py`, run with `cd /tmp && /Users/liyanglu/PycharmProjects/StockAnalysis/venv/bin/python -I /tmp/rethist_build.py /tmp/rethist/histret.html /tmp/rethist/cu.txt /tmp/rethist/18100004.csv`

```python
"""Print rules.py literals for US_RETURNS, US_CPI and CPI 1928-1949 from the source files."""
import collections, csv, sys
import pandas as pd

dam, bls, statcan = sys.argv[1:4]
us = {}
for _, r in pd.read_html(dam)[0].iloc[2:].iterrows():
    try:
        y = int(str(r[0]).strip())
    except ValueError:
        continue
    pct = lambda v: round(float(str(v).replace("%", "").strip()) / 100, 5)
    us[y] = (pct(r[1]), pct(r[4]))           # S&P 500 incl. dividends, US T. Bond (10-year)
level = {int(f[1]): float(f[3]) for f in (l.split("\t") for l in open(bls))
         if f[0].strip() == "CUUR0000SA0" and f[2].strip() == "M13"}
usch = {y: round(level[y] / level[y - 1] - 1, 5) for y in level if y - 1 in level}
m = collections.defaultdict(list)
for r in csv.DictReader(open(statcan, encoding="utf-8-sig")):
    if r["GEO"] == "Canada" and r["Products and product groups"] == "All-items" and r["VALUE"]:
        m[int(r["REF_DATE"][:4])].append(float(r["VALUE"]))
avg = {y: sum(v) / len(v) for y, v in m.items() if len(v) == 12}
ca = {y: round(avg[y] / avg[y - 1] - 1, 5) for y in avg if y - 1 in avg}
last = max(us)
print("US_RETURNS =", {y: us[y] for y in range(1928, last + 1)})
print("US_CPI =", {y: usch[y] for y in range(1928, last + 1)})
print("CPI 1928-1949:", {y: ca[y] for y in range(1928, 1950)})
print("CHECK", last, max(usch), max(ca), "1986 =", ca[1986])   # expect 2025 2025 2025, 1986 = 0.04195
```

Expected spot values: `US_RETURNS[1931] == (-0.4384, -0.0256)`, `US_CPI[1932] == -0.09868`, CPI `1948: 0.14263`, `1986 = 0.04195` (proves the CPI method matches the stored years).

- [ ] **Step 5: Edit `rules.py`**

1. Prepend the 1928–1949 CPI values to the `CPI` dict (same 6-per-line layout), keep 1950–2025 as they are. Change the comment above `CPI` to: `# From 1928 so the inflation and return draws reach the 1930s and 1970s; the default rate is still the last 40 years (historical_inflation).`
2. After `historical_inflation`, add (paste the generated dicts, wrapped 4–6 years per line):

```python
_DAMODARAN = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histretSP.html"
_BLS_CPI = "https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems"   # CUUR0000SA0, M13

# US nominal yearly returns (stocks = S&P 500 with dividends, bonds = 10-year Treasury),
# Aswath Damodaran, "Historical Returns on Stocks, Bonds and Bills". The planner turns
# them real with US_CPI and pairs each year with Canada's CPI (engine.history_real).
US_RETURNS = Rule({...generated...}, 2025, _DAMODARAN)

# US CPI-U, all items, U.S. city average: yearly change of the annual average (BLS).
US_CPI = Rule({...generated...}, 2025, _BLS_CPI)

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
```

3. In `all_rules`, add `"US_RETURNS", "US_CPI", "RETURN_ASSUMPTIONS"` to the name tuple after `"CPI"`.

- [ ] **Step 6: Run the tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_rules.py tests/test_retirement_engine.py -k "history or assumptions or refresh or inflation or lag"`
Expected: PASS (the lognormal inflation draw now covers 1928–2025, still centred on 2.42%).

- [ ] **Step 7: Commit**

```bash
git add src/stockanalysis/retirement/rules.py tests/test_retirement_rules.py tests/test_retirement_engine.py
git commit -m "feat(retirement): 1928-2025 return and CPI history, FP Canada return assumptions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Return inputs (`model`, `stocks`, `bonds`, `mix`)

**Files:**
- Modify: `src/stockanalysis/retirement/inputs.py` (`Returns` ~line 125, `TEMPLATE` `"returns"` ~line 289, `_build` ~line 314, `limits` ~line 395, `defaults` ~line 413, `validate` returns block ~line 536)
- Test: `tests/test_retirement_inputs.py` (incl. the existing `dflt["returns"] == {"inflation_shocks": True}` assertion ~line 380)

**Interfaces:**
- Consumes: `rules.real_return`.
- Produces: `inputs.RETURN_MODELS = ("history", "lognormal")`, `inputs.DEFAULT_MIX = ((0, 0.8),)`, `inputs.RETURN_RANGE = (-0.05, 0.15)`; `Returns.model: str = "history"`, `Returns.stocks: float | None`, `Returns.bonds: float | None`, `Returns.mix: tuple[tuple[float, float], ...]`, properties `Returns.stock_return -> float`, `Returns.bond_return -> float`; `inputs.stock_share(mix, age) -> float | np.ndarray`; `limits()["expected_return"] == RETURN_RANGE`; `defaults()["returns"]` has keys `inflation_shocks, model, stocks, bonds, mix`.

- [ ] **Step 1: Write the failing tests** (append; `copy`, `np`, `pytest`, `inputs`, `rules` — add any missing imports at the top)

```python
def test_returns_default_to_history_with_fp_canada_averages():
    r = inputs.parse(copy.deepcopy(inputs.TEMPLATE)).returns
    assert r.model == "history" and r.mix == ((0, 0.8),)
    assert r.stock_return == pytest.approx(rules.real_return("canadian_equities"))
    assert r.bond_return == pytest.approx(rules.real_return("fixed_income"))


def test_a_plan_without_the_new_fields_loads_with_the_defaults():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["returns"] = {"mean": 0.05, "sd": 0.15, "paths": 100, "seed": 7}
    r = inputs.parse(d).returns
    assert r.model == "history" and r.stocks is None and r.bonds is None and r.mix == inputs.DEFAULT_MIX


def test_stock_share_glides_between_points_and_is_flat_outside():
    mix = ((45, 0.9), (65, 0.6), (80, 0.4))
    assert inputs.stock_share(mix, 30) == 0.9
    assert inputs.stock_share(mix, 55) == pytest.approx(0.75)
    assert inputs.stock_share(mix, 90) == 0.4
    assert np.allclose(inputs.stock_share(mix, np.array([45, 65, 72.5])), [0.9, 0.6, 0.5])
    assert inputs.stock_share(((0, 0.8),), 70) == 0.8


@pytest.mark.parametrize("ret, field", [
    ({"model": "bootstrap"}, "returns.model"),
    ({"stocks": 0.2}, "returns.stocks"),
    ({"bonds": -0.1}, "returns.bonds"),
    ({"mix": []}, "returns.mix"),
    ({"mix": [[60]]}, "returns.mix[0]"),
    ({"mix": [[60, 1.2]]}, "returns.mix[0]"),
    ({"mix": [[200, 0.5]]}, "returns.mix[0]"),
    ({"mix": [[60, 0.5], [60, 0.4]]}, "returns.mix[1]"),
])
def test_bad_return_inputs_name_the_field(ret, field):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["returns"].update(ret)
    with pytest.raises(inputs.PlanError) as e:
        inputs.parse(d)
    assert e.value.field == field


def test_a_saved_scenario_can_change_a_mix_point():
    d = copy.deepcopy(inputs.TEMPLATE)
    d["returns"]["mix"] = [[50, 0.9], [70, 0.5]]
    p = inputs.apply_changes(inputs.parse(d), {"returns.mix.1.1": 0.4})
    assert p.returns.mix == ((50, 0.9), (70, 0.4))
```

Change the existing `assert dflt["returns"] == {"inflation_shocks": True}` to:

```python
    assert dflt["returns"] == {"inflation_shocks": True, "model": "history",
                               "stocks": pytest.approx(rules.real_return("canadian_equities")),
                               "bonds": pytest.approx(rules.real_return("fixed_income")),
                               "mix": [[0, 0.8]]}
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_inputs.py`
Expected: FAIL (`Returns` has no `model`; `stock_share` missing).

- [ ] **Step 3: Implement in `inputs.py`**

Above `class Returns`:

```python
RETURN_MODELS = ("history", "lognormal")
DEFAULT_MIX = ((0, 0.8),)          # 80% stocks: about today's 15% yearly swings
RETURN_RANGE = (-0.05, 0.15)       # plan bounds for an expected real return, not rules
```

Add to `Returns` after `inflation_shocks`:

```python
    model: str = "history"           # "history": joint runs of 1928- returns and inflation; "lognormal": mean/sd
    stocks: float | None = None      # expected compound real return; None: FP Canada (rules.RETURN_ASSUMPTIONS)
    bonds: float | None = None
    mix: tuple = DEFAULT_MIX         # ((people[0]'s age, stock share), ...): straight lines between, flat outside

    @property
    def stock_return(self) -> float:
        return rules.real_return("canadian_equities") if self.stocks is None else self.stocks

    @property
    def bond_return(self) -> float:
        return rules.real_return("fixed_income") if self.bonds is None else self.bonds
```

After the `Returns` class:

```python
def stock_share(mix, age):
    """The stock share at ``age`` (a number or an array): straight lines between the
    mix points, flat before the first and after the last."""
    ages, shares = zip(*mix)
    return np.interp(age, ages, shares)


def _returns(d: dict) -> Returns:
    d = dict(d)
    if "mix" in d:      # JSON lists -> tuples, so the frozen plan stays hashable and apply_changes works
        d["mix"] = tuple(tuple(p) if isinstance(p, (list, tuple)) else p for p in d["mix"] or ())
    return Returns(**d)
```

In `_build`, replace `returns=Returns(**d.get("returns", {})),` with `returns=_returns(d.get("returns", {})),`.

`TEMPLATE["returns"]` becomes:

```python
    "returns": {"model": "history", "mix": [[0, 0.8]], "mean": 0.05, "sd": 0.15, "paths": 10000, "seed": 7},
```

In `limits`, add `"expected_return": RETURN_RANGE,` (next to the other plan bounds). In `defaults`, replace `"returns": flags(Returns),` with:

```python
            "returns": {**flags(Returns), "model": Returns.model,
                        "stocks": rules.real_return("canadian_equities"),
                        "bonds": rules.real_return("fixed_income"),
                        "mix": [list(p) for p in DEFAULT_MIX]},
```

In `validate`, after the `returns.inflation` check:

```python
    if r.model not in RETURN_MODELS:
        _fail("returns.model", f"one of {', '.join(RETURN_MODELS)}")
    lo, hi = RETURN_RANGE
    for name in ("stocks", "bonds"):
        v = getattr(r, name)
        if v is not None and not (_is_number(v) and lo <= v <= hi):
            _fail(f"returns.{name}", f"an expected real return between {lo} and {hi} (e.g. 0.04)")
    if not r.mix:
        _fail("returns.mix", "needs at least one [age, stock share] point")
    prev = None
    for i, pt in enumerate(r.mix):
        if not (isinstance(pt, tuple) and len(pt) == 2 and all(_is_number(x) for x in pt)):
            _fail(f"returns.mix[{i}]", "an [age, stock share] pair, e.g. [65, 0.6]")
        age, share = pt
        if not 0 <= age <= mortality.OMEGA:
            _fail(f"returns.mix[{i}]", f"age between 0 and {mortality.OMEGA}")
        if not 0 <= share <= 1:
            _fail(f"returns.mix[{i}]", "stock share between 0 and 1 (e.g. 0.6)")
        if prev is not None and age <= prev:
            _fail(f"returns.mix[{i}]", "ages must increase down the list")
        prev = age
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_inputs.py`
Expected: PASS. If `apply_changes` can't index into a tuple of tuples, extend its tuple branch to rebuild the inner tuple the same way it does for `people`, and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/inputs.py tests/test_retirement_inputs.py
git commit -m "feat(retirement): return model, expected stock/bond returns and an age-based mix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Historical draws in the engine

**Files:**
- Modify: `src/stockanalysis/retirement/engine.py` (imports ~line 53; `draw_inflation` ~line 75; new functions after `median_return` ~line 101; `PlanResult` ~line 1038; `draw_futures`/`run` ~line 1071)
- Test: `tests/test_retirement_engine.py` (the `plan()` helper ~line 24; new tests at the end of the inflation section)

**Interfaces:**
- Consumes: `inputs.stock_share`, `Returns.stock_return/bond_return/model/mix`, `rules.US_RETURNS/US_CPI/CPI`.
- Produces: `engine.HISTORY_FROM = 1928`; `engine._block_index(n, paths, years, seed) -> (years, paths) int array`; `engine._centre(x, target) -> array`; `engine.history_real() -> (stocks, bonds, inflation)` 1-D arrays; `engine.draw_history(plan, paths, years, seed) -> (stocks, bonds, inflation)` each (years, paths); `engine.portfolio_returns(plan, stocks, bonds) -> (years, paths)`; `engine.average_returns(plan, T) -> (T, 1)`; `PlanResult.bad_luck_returns: np.ndarray | None` (shape `(steps,)`).

- [ ] **Step 1: Pin the regression anchor.** In the `plan()` helper change `returns=Returns(0.0, 0.0, 1, 1, inflation=0.0)` to `returns=Returns(0.0, 0.0, 1, 1, inflation=0.0, model="lognormal")`. In `_shocky` add `"model": "lognormal"` to the dict it merges.

- [ ] **Step 2: Write the failing tests** (after `test_shock_paths_average_the_history_so_the_lag_is_unbiased`)

```python
# -- historical returns ---------------------------------------------------------------------

def _hist(**ret):
    p = plan()
    return replace(p, returns=replace(p.returns, **{"model": "history", "inflation": None,
                                                    "inflation_shocks": True, **ret}))


def test_history_blocks_are_whole_matched_years():
    s, b, ca = engine.history_real()
    S, B, I = engine.draw_history(_hist(stocks=0.04, bonds=0.01), paths=30, years=12, seed=5)
    cs, cb = engine._centre(s, 0.04), engine._centre(b, 0.01)
    ci = engine._centre(ca, rules.historical_inflation())
    for n in range(30):
        ks = [int(np.flatnonzero(np.isclose(cs, S[t, n], rtol=0, atol=1e-12))[0]) for t in range(12)]
        assert np.allclose(cb[ks], B[:, n]) and np.allclose(ci[ks], I[:, n])   # one year, three series
        assert all(ks[t + 1] == (ks[t] + 1) % len(cs) for t in range(11) if t % 5 != 4)


def test_history_is_seeded_and_centred_on_the_targets():
    p = _hist(stocks=0.04, bonds=0.01)
    S, B, I = engine.draw_history(p, paths=20_000, years=40, seed=2)
    geo = lambda x: np.exp(np.log1p(x).mean()) - 1    # noqa: E731
    assert geo(S) == pytest.approx(0.04, abs=2e-3)
    assert geo(B) == pytest.approx(0.01, abs=1e-3)
    assert geo(I) == pytest.approx(rules.historical_inflation(), abs=1e-3)
    assert all(np.array_equal(x, y) for x, y in zip((S, B, I), engine.draw_history(p, 20_000, 40, 2)))


def test_history_reaches_the_worst_stretches():
    S, _, _ = engine.draw_history(_hist(), paths=2_000, years=40, seed=4)
    worst5 = min(np.prod(1 + S[t:t + 5], axis=0).min() for t in range(36)) - 1
    assert worst5 < -0.40      # 1937-41 loses 46% in real terms even at the FP Canada average


def test_history_with_steady_inflation_keeps_historical_returns():
    S, _, I = engine.draw_history(_hist(inflation=0.02), 10, 8, 1)
    assert np.allclose(I, 0.02) and S.std() > 0.05
    S2, _, I2 = engine.draw_history(_hist(inflation_shocks=False), 10, 8, 1)
    assert np.allclose(I2, rules.historical_inflation()) and np.array_equal(S, S2)


def test_history_wraps_for_horizons_longer_than_the_record():
    S, B, I = engine.draw_history(_hist(), paths=3, years=150, seed=1)
    assert S.shape == B.shape == I.shape == (150, 3) and np.isfinite(S).all()


def test_lognormal_inflation_uses_the_same_blocks_as_history():
    _, _, I = engine.draw_history(_hist(), 50, 12, 3)
    assert np.allclose(engine.draw_inflation(_shocky(), 50, 12, 3), I)


def test_portfolio_is_the_mix_of_stocks_and_bonds_by_people0_age():
    S, B = np.full((4, 2), 0.10), np.full((4, 2), 0.02)
    a = person().age
    R = engine.portfolio_returns(_hist(mix=((a, 1.0), (a + 2, 0.0))), S, B)
    assert np.allclose(R[:, 0], [0.10, 0.06, 0.02, 0.02])
    assert np.allclose(engine.portfolio_returns(_hist(mix=((0, 1.0),)), S, B), S)
    assert np.allclose(engine.portfolio_returns(_hist(mix=((0, 0.0),)), S, B), B)


def test_average_returns_lognormal_is_unchanged_and_history_follows_the_mix():
    lo = plan()
    lo = replace(lo, returns=replace(lo.returns, mean=0.05, sd=0.15))
    assert np.array_equal(engine.average_returns(lo, 5), np.full((5, 1), engine.median_return(0.05, 0.15)))
    a = person().age
    p = _hist(stocks=0.04, bonds=0.01, mix=((a, 1.0), (a + 2, 0.0)))
    assert np.allclose(engine.average_returns(p, 3)[:, 0], [0.04, 0.025, 0.01])


def test_history_mode_feeds_simulate_without_the_lag():
    p = _hist(stocks=0.04, bonds=0.01, mix=((0, 0.6),))
    R, _, I = engine.draw_futures(p, paths=10, seed=3)
    S, B, I2 = engine.draw_history(p, 10, engine.life_steps(p), 3)
    assert np.allclose(R, 0.6 * S + 0.4 * B) and np.array_equal(I, I2)


def test_run_records_the_bad_luck_returns_and_a_steady_average():
    res = engine.run(_hist(), paths=40, seed=1)
    assert res.bad_luck_returns.shape == (engine.steps(res.inputs),)
    lo = engine.run(plan(), paths=40, seed=1)
    assert lo.average_return == engine.median_return(0.0, 0.0)
```

- [ ] **Step 3: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_engine.py -k "history or portfolio or average_returns or bad_luck_returns"`
Expected: FAIL (`engine` has no `history_real`).

- [ ] **Step 4: Implement in `engine.py`**

Import: `from .inputs import PlanInputs, event_span, stock_share`.

Replace the body of `draw_inflation` after the steady-rate early return with:

```python
    hist = np.array([rules.CPI.value[y] for y in sorted(rules.CPI.value)])
    return _centre(hist, r.inflation_rate)[_block_index(len(hist), paths, years, seed)]
```

Add after `median_return`:

```python
HISTORY_FROM = 1928      # first year of rules.US_RETURNS, US_CPI and CPI


def _block_index(n: int, paths: int, years: int, seed: int) -> np.ndarray:
    """(years, paths) indices into an n-year history: INFLATION_BLOCK-year runs from a
    random start, wrapping from the last year to the first, on the inflation stream.
    History draws and inflation-only draws share them, so the two models see the same years."""
    blocks = -(-years // INFLATION_BLOCK)
    rng = np.random.default_rng([seed, INFLATION_STREAM])
    starts = rng.integers(0, n, size=(paths, blocks))
    return ((starts[:, :, None] + np.arange(INFLATION_BLOCK)) % n).reshape(paths, -1)[:, :years].T


def _centre(x: np.ndarray, target: float) -> np.ndarray:
    """``x`` shifted by one factor so its compound average over the whole history is
    ``target``: the runs keep their shape, the average is the plan's."""
    return (1 + x) * (1 + target) / np.exp(np.log1p(x).mean()) - 1


def history_real() -> tuple:
    """(stocks, bonds, inflation), one entry per year from HISTORY_FROM: US real returns,
    (1 + nominal) / (1 + US CPI) - 1, and Canada's CPI change in the same year."""
    years = range(HISTORY_FROM, rules.CPI.year + 1)
    nominal = np.array([rules.US_RETURNS.value[y] for y in years])
    us_cpi = np.array([rules.US_CPI.value[y] for y in years])
    real = (1 + nominal) / (1 + us_cpi)[:, None] - 1
    return real[:, 0], real[:, 1], np.array([rules.CPI.value[y] for y in years])


def draw_history(plan: PlanInputs, paths: int, years: int, seed: int) -> tuple:
    """(stocks, bonds, inflation), each (years, paths): runs of whole historical years,
    every series shifted to its target (returns.stock_return, bond_return,
    inflation_rate). Steady inflation when shocks are off or the rate is fixed."""
    r = plan.returns
    s, b, ca = history_real()
    idx = _block_index(len(ca), paths, years, seed)
    infl = (_centre(ca, r.inflation_rate)[idx] if r.inflation_shocks and r.inflation is None
            else np.full((years, paths), r.inflation_rate))
    return _centre(s, r.stock_return)[idx], _centre(b, r.bond_return)[idx], infl


def _mix_path(plan: PlanInputs, T: int) -> np.ndarray:
    """(T,) stock share each year, on people[0]'s age."""
    return stock_share(plan.returns.mix, plan.people[0].age + np.arange(T))


def portfolio_returns(plan: PlanInputs, stocks: np.ndarray, bonds: np.ndarray) -> np.ndarray:
    """(years, paths) household returns: the year's mix, rebalanced every January."""
    w = _mix_path(plan, stocks.shape[0])[:, None]
    return w * stocks + (1 - w) * bonds


def average_returns(plan: PlanInputs, T: int) -> np.ndarray:
    """(T, 1) steady returns of the average future: the median lognormal return, or the
    year's mix of the expected stock and bond returns."""
    r = plan.returns
    if r.model == "lognormal":
        return np.full((T, 1), median_return(r.mean, r.sd))
    w = _mix_path(plan, T)[:, None]
    return w * r.stock_return + (1 - w) * r.bond_return
```

`PlanResult`: add a last field `bad_luck_returns: np.ndarray | None = None   # (steps,) the bad-luck future's yearly returns`.

`draw_futures` body:

```python
    r, years = plan.returns, life_steps(plan)
    if r.model == "history":
        stocks, bonds, inflation = draw_history(plan, paths, years, seed)
        returns = portfolio_returns(plan, stocks, bonds)      # already real: no lag
    else:
        inflation = draw_inflation(plan, paths, years, seed)
        returns = lag_returns(draw_returns(r.mean, r.sd, paths, years, seed), inflation, r.inflation_rate)
    return returns, mortality.draw_death_ages(plan.people, plan.start_year, paths, seed), inflation
```

In `run`, replace the `T, g, fixed = ...` / `average = ...` lines and the `return` with:

```python
    T, fixed = steps(plan), average_deaths(plan)
    g = average_returns(plan, T)
    average = simulate(plan, g, fixed)
```

```python
    steady = float(g[0, 0]) if np.all(g == g[0, 0]) else float(g.mean())   # a steady rate reads back exactly
    return PlanResult(plan, simulated, average, simulate(plan, R[:T, [k]], fixed, I[:T, [k]]), steady, k,
                      R[:T, k].copy())
```

Update the `draw_futures` and `run` docstrings to mention the history mode, and the `draw_inflation` docstring's "(since 1950 ...)" to "(since 1928 ...)".

- [ ] **Step 5: Run the new tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_engine.py`
Expected: PASS.

- [ ] **Step 6: Run the whole retirement suite and pin regressions**

Run: `venv/bin/python -m pytest -q tests/test_retirement_*.py tests/test_cli_retire.py`
For each failure, decide: (a) the test pins an exact number or a property only the lognormal draws had → pin it with `d["returns"]["model"] = "lognormal"` (dict plans) or `replace(p, returns=replace(p.returns, model="lognormal"))`; (b) anything else is a real bug — stop and fix it with a test. List the pinned tests in the commit message.

- [ ] **Step 7: Commit**

```bash
git add src/stockanalysis/retirement/engine.py tests/
git commit -m "feat(retirement): draw returns and inflation together from 1928-2025 history

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: What-ifs and planning tools use the new averages

**Files:**
- Modify: `src/stockanalysis/retirement/scenarios.py` (`variants` end ~line 63, `evaluate` ~line 73), `src/stockanalysis/retirement/optimize.py` (`_lifespan_run` ~line 43, `_drawdown_row` ~line 235)
- Test: `tests/test_retirement_scenarios.py`

**Interfaces:**
- Consumes: `engine.average_returns`, `Returns.model/mix`.
- Produces: what-if keys `"smooth_returns"` (label `"Smooth returns (old model)"`) and `"more_bonds"` (label `"10 points more bonds"`).

- [ ] **Step 1: Write the failing tests** (append; import `copy`, `numpy as np`, `replace`, `engine`, `inputs` if missing)

```python
def _hist_plan():
    p = inputs.parse(copy.deepcopy(inputs.TEMPLATE))
    return replace(p, returns=replace(p.returns, model="history", mix=((50, 0.8), (80, 0.05))))


def test_history_plans_get_the_model_and_bond_what_ifs():
    v = {k: (label, plan) for k, label, plan in scenarios.variants(_hist_plan())}
    assert v["smooth_returns"][0] == "Smooth returns (old model)"
    assert v["smooth_returns"][1].returns.model == "lognormal"
    assert v["more_bonds"][0] == "10 points more bonds"
    assert np.allclose(v["more_bonds"][1].returns.mix, ((50, 0.7), (80, 0.0)))


def test_lognormal_plans_skip_the_history_what_ifs():
    p = _hist_plan()
    p = replace(p, returns=replace(p.returns, model="lognormal"))
    assert not {k for k, _, _ in scenarios.variants(p)} & {"smooth_returns", "more_bonds"}


def test_more_bonds_skipped_when_already_all_bonds():
    p = _hist_plan()
    p = replace(p, returns=replace(p.returns, mix=((0, 0.0),)))
    assert "more_bonds" not in {k for k, _, _ in scenarios.variants(p)}


def test_evaluate_uses_the_mix_for_the_average_future():
    p = _hist_plan()
    _, tax, legacy = scenarios.evaluate(p, paths=20, seed=1)
    avg = engine.simulate(p, engine.average_returns(p, engine.steps(p)), engine.average_deaths(p))
    assert legacy == pytest.approx(float(avg.legacy[0])) and tax == pytest.approx(float(avg.lifetime_tax[0]))
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_scenarios.py`
Expected: FAIL (`KeyError: 'smooth_returns'`; evaluate legacy mismatch).

- [ ] **Step 3: Implement**

`scenarios.variants`, after the `steady_inflation` block:

```python
    if r.model == "history":
        out.append(("smooth_returns", "Smooth returns (old model)",
                    replace(plan, returns=replace(r, model="lognormal"))))
        more = tuple((age, max(0.0, share - 0.10)) for age, share in r.mix)
        if more != r.mix:
            out.append(("more_bonds", "10 points more bonds", replace(plan, returns=replace(r, mix=more))))
```

`scenarios.evaluate`: replace `np.full((T, 1), engine.median_return(r.mean, r.sd))` with `engine.average_returns(plan, T)`; drop the unused `r`.

`optimize._lifespan_run`:

```python
def _lifespan_run(plan: PlanInputs, deaths: np.ndarray):
    """The plan at the average future's steady returns over each of ``deaths``' lifespans."""
    g = engine.average_returns(plan, engine.life_steps(plan))
    return engine.simulate(plan, np.repeat(g, deaths.shape[1], axis=1), deaths)
```

`optimize._drawdown_row`: replace `np.full((T, 1), engine.median_return(r.mean, r.sd))` with `engine.average_returns(plan, T)`; drop the unused `r`. Update the `_expected_legacy` docstring "at a steady median return" → "at the average future's steady returns".

Check nothing else calls `median_return(r.mean, r.sd)` outside `engine.average_returns`:
Run: `grep -rn "median_return(" src/stockanalysis/retirement/`
Expected: only the definition and `average_returns`.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_scenarios.py tests/test_retirement_optimize.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/scenarios.py src/stockanalysis/retirement/optimize.py tests/test_retirement_scenarios.py
git commit -m "feat(retirement): what-ifs for the return model and more bonds; tools use the mix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Report lines

**Files:**
- Modify: `src/stockanalysis/retirement/report.py` (`_assumptions` ~line 785: the `"Returns"` and `"Inflation"` facts)
- Test: `tests/test_retirement_report.py`

**Interfaces:**
- Consumes: `PlanResult.bad_luck_returns`, `Returns.*`, `rules.RETURN_ASSUMPTIONS.year`.
- Produces: `report.mix_label(mix) -> str`, `report.worst_stretch(returns, years=5) -> float`.

- [ ] **Step 1: Write the failing tests** (append; the `built` fixture runs `TEMPLATE`, now history mode)

```python
def test_mix_label_reads_the_glide():
    assert report.mix_label(((0, 0.8),)) == "80% stocks"
    assert report.mix_label(((45, 0.9), (65, 0.6), (80, 0.4))) == \
        "90% stocks to age 45, gliding to 60% at 65, then to 40% at 80"


def test_worst_stretch_is_the_worst_5_year_real_change():
    r = np.array([0.1, -0.2, -0.3, 0.0, 0.1, -0.1, 0.2])
    assert report.worst_stretch(r) == pytest.approx(min(np.prod(1 + r[t:t + 5]) for t in range(3)) - 1)
    assert report.worst_stretch(np.array([0.1, 0.1])) is None        # shorter than 5 years


def test_assumptions_describe_the_history_model(built):
    plan, result, _, _ = built
    html = report._assumptions(plan, result, None)
    assert "80% stocks" in html and f"FP Canada {rules.RETURN_ASSUMPTIONS.year}" in html
    assert "1928" in html and "Worst stretch" in html
    assert "returns lag it" not in html


def test_assumptions_keep_the_old_lines_for_the_lognormal_model(built):
    plan, result, _, _ = built
    lo = replace(plan, returns=replace(plan.returns, model="lognormal"))
    html = report._assumptions(lo, replace(result, inputs=lo, bad_luck_returns=None), None)
    assert "yearly swings" in html and "returns lag it" in html and "Worst stretch" not in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_report.py -k "mix_label or worst_stretch or history_model or lognormal_model"`
Expected: FAIL (`report` has no `mix_label`).

- [ ] **Step 3: Implement in `report.py`** (above `_assumptions`)

```python
def mix_label(mix) -> str:
    """'80% stocks', or '90% stocks to age 45, gliding to 60% at 65, then to 40% at 80'."""
    (a0, s0), rest = mix[0], mix[1:]
    if not rest:
        return f"{s0:.0%} stocks"
    return ", ".join([f"{s0:.0%} stocks to age {a0:g}"]
                     + [f"{'gliding' if i == 0 else 'then'} to {s:.0%} at {a:g}" for i, (a, s) in enumerate(rest)])


def worst_stretch(returns, years: int = 5) -> float | None:
    """The worst ``years``-year compound change in ``returns``; None when too short."""
    r = np.asarray(returns, dtype=float)
    if len(r) < years:
        return None
    return float(min(np.prod(1 + r[t:t + years]) for t in range(len(r) - years + 1)) - 1)


def _returns_fact(plan, result) -> str:
    r = plan.returns
    if r.model == "lognormal":
        return (f"{r.mean:.1%} average, {r.sd:.0%} yearly swings; typical "
                f"{result.average_return:.2%} a year after inflation")
    source = (f"FP Canada {rules.RETURN_ASSUMPTIONS.year} guidelines, before fees"
              if r.stocks is None and r.bonds is None else "your figures")
    return (f"{mix_label(r.mix)}, rebalanced each January; stocks {r.stock_return:.2%} and bonds "
            f"{r.bond_return:.2%} a year after inflation ({source}); each future strings together "
            f"5-year runs of 1928–2025 US stock and bond returns shifted to those averages; typical "
            f"{result.average_return:.2%} a year")
```

In `_assumptions`, replace the `("Returns", ...)` tuple with `("Returns", _returns_fact(plan, result)),`, and change the inflation shocks clause to:

```python
                      + (("; each future replays the same 5-year runs of 1928–2025 as the returns, "
                          "centred on that average, so high inflation comes with the returns it brought"
                          if r.model == "history" else
                          "; each future replays 5-year runs of inflation since 1928 (centred on that "
                          "average) and returns lag it in high-inflation years")
                         if r.inflation_shocks and r.inflation is None else "; steady")
```

After the `"Inflation"` fact, insert (only in history mode with a recorded path):

```python
    worst = worst_stretch(result.bad_luck_returns) if (
        r.model == "history" and result.bad_luck_returns is not None) else None
    if worst is not None:
        facts.insert(next(i for i, f in enumerate(facts) if f[0] == "Inflation") + 1,
                     ("Worst stretch", f"the bad-luck future's worst 5 years change investments by "
                                       f"{worst:+.0%} after inflation"))
```

(If `facts` is built in one literal, put this right after the literal.)

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_report.py`
Expected: PASS. The existing inflation test (`"Canada&#x27;s CPI"`) still passes.

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/retirement/report.py tests/test_retirement_report.py
git commit -m "feat(retirement): report describes the mix, the averages' source and the worst stretch

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: GUI Investing tab

**Files:**
- Modify: `src/stockanalysis/retirement/static/planner.html` (`investingPanel` ~line 343, the `change`/`click` handlers ~lines 587–640, the inflation check label ~line 372)
- Test: `tests/test_retirement_gui.py`

**Interfaces:**
- Consumes: `/api/plan` → `defaults.returns` (`model`, `stocks`, `bonds`, `mix`), `limits.expected_return`.
- Produces: inputs with `data-path="returns.model"`, `returns.stocks`, `returns.bonds`, `returns.mix.<j>.0|1`; buttons `#add-mix`, `[data-remove-mix]`.

- [ ] **Step 1: Write the failing tests** (append; reuse the module's `server`/`_req` helpers)

```python
def test_defaults_carry_the_return_assumptions(server):
    status, st = _req(server, "GET", "/api/plan")
    assert status == 200
    ret = st["defaults"]["returns"]
    assert ret["model"] == "history" and ret["mix"] == [[0, 0.8]]
    assert st["limits"]["expected_return"] == [-0.05, 0.15]


def test_mix_and_model_round_trip_through_save(server, tmp_path):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["returns"].update(model="history", stocks=0.04, mix=[[50, 0.9], [75, 0.4]])
    status, _ = _req(server, "POST", "/api/plan", {"plan": d})
    assert status == 200
    _, st = _req(server, "GET", "/api/plan")
    assert st["plan"]["returns"]["mix"] == [[50, 0.9], [75, 0.4]] and st["plan"]["returns"]["stocks"] == 0.04


def test_page_has_the_mix_table_and_model_switch(server):
    html = _get_text(server, "/")
    assert 'data-path="returns.model"' in html or "returns.model" in html
    assert "add-mix" in html and "remove-mix" in html
```

If the module lacks a GET-text helper, add `_get_text(server, path)` next to `_req` using the same `http.client` connection and `Host` header. If saving writes to a fixture path, read the file the fixture uses instead of `/api/plan` (follow the existing save test ~line 100).

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest -q tests/test_retirement_gui.py -k "return_assumptions or mix"`
Expected: the defaults/limits test passes already (Task 2); the page test FAILS (`add-mix` missing). Round-trip passes if the page-agnostic save path works — fine.

- [ ] **Step 3: Implement the page**

Replace the first card of `investingPanel` (`<div class="card"><h3>Returns after inflation</h3> ... </div>`) with `${returnsCard()}` and add:

```js
function returnsCard() {
  const r = draft.returns, hist = (r.model ?? retDefaults.model) === "history";
  const [lo, hi] = limits.expected_return;
  const mix = r.mix ?? retDefaults.mix;
  const rows = mix.map((p, j) => `<tr>
      <td><input type="number" data-path="returns.mix.${j}.0" step="1" min="0" max="110" value="${p[0]}"></td>
      <td><input type="number" data-path="returns.mix.${j}.1" data-scale="100" step="5" min="0" max="100"
          value="${+(p[1] * 100).toFixed(4)}"></td>
      <td>${mix.length > 1 ? `<button class="x" data-remove-mix="${j}" title="Remove">×</button>` : ""}</td></tr>`).join("");
  return `<div class="card"><h3>Returns after inflation</h3>
      ${choice("returns.model", "Model", [["history", "Historical (recommended)"], ["lognormal", "Smooth (old model)"]])}
      ${hist ? `
      ${number("returns.stocks", "Stocks: expected yearly return", {min: lo, max: hi, step: 0.001, unit: "%", nullable: true,
               placeholder: `FP Canada: ${pct(retDefaults.stocks, 2)}`})}
      ${number("returns.bonds", "Bonds: expected yearly return", {min: lo, max: hi, step: 0.001, unit: "%", nullable: true,
               placeholder: `FP Canada: ${pct(retDefaults.bonds, 2)}`})}
      <table class="changes"><tr class="note"><td>From age (${esc(draft.people[0].name)})</td><td>% stocks</td><td></td></tr>${rows}</table>
      <button id="add-mix" style="margin-top:6px">+ Add an age</button>
      <p class="note">Each future strings together 5-year runs of 1928–2025 stock and bond returns with the
        inflation of the same years, shifted to these averages. The mix moves in a straight line between ages
        and is rebalanced every January · typical return
        <span id="median">${lastPreview ? pct(lastPreview.median_return, 2) : "–"}</span>.</p>` : `
      ${slider("returns.mean", "Average yearly return", {min: 0, max: 0.10, step: 0.0025, unit: "%", digits: 2})}
      ${slider("returns.sd", "Ups and downs (volatility)", {min: 0, max: 0.30, step: 0.01, unit: "%"})}
      <p class="note">Volatility pulls the typical return below the average · typical (median) return
        <span id="median">${lastPreview ? pct(lastPreview.median_return, 2) : "–"}</span>.</p>`}
    </div>`;
}
```

In the `change` handler add: `if (el.dataset.path === "returns.model") edited();   // swap the history / smooth inputs`

In the `click` handler, next to `add-event`:

```js
  if (el.id === "add-mix") {
    const mix = draft.returns.mix = clone(draft.returns.mix ?? retDefaults.mix);
    const [age, share] = mix[mix.length - 1];
    mix.push([Math.min(age + 10, 110), Math.max(0, +(share - 0.2).toFixed(2))]);
    edited();
  }
  if (el.dataset.removeMix != null) {
    draft.returns.mix = clone(draft.returns.mix ?? retDefaults.mix);
    draft.returns.mix.splice(Number(el.dataset.removeMix), 1);
    edited();
  }
```

Typing into a mix cell when `draft.returns.mix` is undefined: in the `input` handler, before `set(draft, el.dataset.path, v)`, add
`if (el.dataset.path.startsWith("returns.mix.") && !draft.returns.mix) draft.returns.mix = clone(retDefaults.mix);`
(`set` creates objects, not arrays, for missing keys).

Change the inflation checkbox label (line ~372) to `"Inflation shocks: each future replays runs of past inflation (with the matching returns in the historical model)"`.

- [ ] **Step 4: Run the GUI tests**

Run: `venv/bin/python -m pytest -q tests/test_retirement_gui.py`
Expected: PASS.

- [ ] **Step 5: Check it in a browser** — `venv/bin/stock-analysis retire --gui` against a temp copy: `--plan` pointing at a `write_template` file in `/tmp` (never the owner's plan). Switch models, add/remove mix rows, confirm the preview updates and Save writes `returns.mix`. Stop the server after.

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/static/planner.html tests/test_retirement_gui.py
git commit -m "feat(retirement): GUI return model switch, expected returns and mix table

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, full suite, effect check

**Files:**
- Modify: `src/stockanalysis/retirement/README.md` (`returns` input row ~line 112; Inflation section ~line 258; add a Returns section before it), `src/stockanalysis/retirement/CLAUDE.md` (module map `engine.py` row; Inflation bullet ~line 201; a new "Historical returns" bullet), `.claude/skills/retirement-planning/SKILL.md` (input table ~line 85; yearly refresh list), project `CLAUDE.md` retirement paragraph only if it mentions the return model (it doesn't today — leave it)

- [ ] **Step 1: README.** `returns` row: add `model` (`"history"` default / `"lognormal"`), `stocks`/`bonds` (expected compound real returns; default FP Canada 2026: equities 6.3% and fixed income 3.2% nominal, 2.1% inflation → 4.11% / 1.08% real, before fees), `mix` (`[[age, stock share], ...]` on people[0]'s age, default `[[0, 0.8]]`); `mean`/`sd` only for `"lognormal"`. New "Returns" section, plain language: history mode strings together 5-year runs of 1928–2025 US real stock and bond returns with Canada's inflation from the same years, shifted to your averages; the Depression, 1970s and 2008 can happen; it's US market history for a Canadian portfolio and no future is worse than the worst five years on record; fees aren't modelled (lower the averages by your fees). Inflation section: "from 1950" → "from 1928"; in history mode returns are not lagged (history already holds what inflation did).

- [ ] **Step 2: Package CLAUDE.md.** Module map `engine.py` row: add `draw_history` / `history_real` / `portfolio_returns` / `average_returns` (the one source of the average future's returns). Inflation bullet: CPI from 1928; `_block_index` and `_centre` are shared by both models. New bullet **Historical returns**: `returns.model == "history"` draws `(stocks, bonds, inflation)` from the same block indices, never applies `lag_returns` (double count), shifts every series with `_centre`; defaults come from `rules.RETURN_ASSUMPTIONS` via `rules.real_return`, never hardcoded; `mix` is keyed on people[0]'s age through `inputs.stock_share`; the lognormal model is the regression anchor — the engine test `plan()` helper pins it; refresh `US_RETURNS`, `US_CPI` (each January, with `CPI`) and `RETURN_ASSUMPTIONS` (each spring, when FP Canada publishes).

- [ ] **Step 3: Skill.** Input table `returns` row: add `model`, `stocks`, `bonds`, `mix`. Yearly refresh list: add `US_RETURNS`, `US_CPI`, `RETURN_ASSUMPTIONS`, with the sources from Global Constraints.

- [ ] **Step 4: Full suite**

Run: `venv/bin/python -m pytest -q tests/`
Expected: all pass (the retirement suite takes ~5 min; run in the background and wait once).

- [ ] **Step 5: Effect check on the template plan** (report to the owner, don't commit numbers from the owner's plan)

```bash
cat > /tmp/ret_effect.py <<'EOF'
import copy, numpy as np
from dataclasses import replace
from stockanalysis.retirement import inputs, engine
p = inputs.parse(copy.deepcopy(inputs.TEMPLATE))
for label, q in (("history 80/20", p), ("smooth (old)", replace(p, returns=replace(p.returns, model="lognormal")))):
    r = engine.run(q, paths=2000)
    leg = r.simulated.legacy
    print(f"{label:14} success {r.simulated.success:.4f}  median legacy {np.nanmedian(leg):,.0f}  "
          f"p10 legacy {np.nanpercentile(leg, 10):,.0f}  typical {r.average_return:.2%}")
EOF
venv/bin/python /tmp/ret_effect.py
```

Then offer to run `stock-analysis retire` on the owner's plan; report its change in chat only.

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/retirement/README.md src/stockanalysis/retirement/CLAUDE.md .claude/skills/retirement-planning/SKILL.md
git commit -m "docs(retirement): historical returns, the stock/bond mix and their sources

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
