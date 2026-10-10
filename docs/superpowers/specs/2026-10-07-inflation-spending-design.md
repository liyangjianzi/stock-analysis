# Inflation that reaches spending: design

Date: 2026-10-07 · Area: `src/stockanalysis/retirement/` · Status: approved design, before the plan

## Goal

The planner works in today's dollars, so spending already keeps pace with inflation
and inflation's only effect so far is the nominal tax on non-registered gains
(`returns.inflation`, `rules.CPI`). The owner wants inflation to reach spending in
four ways:

1. **Future dollars on screen.** Amounts can also be shown in the dollars actually
   spent in each year.
2. **Some costs rise faster than inflation.** These are base spending, care,
   education, property tax and home insurance. A rate below inflation is also
   allowed, for spending that lags it.
3. **Inflation shocks hurt.** High-inflation stretches like 1986–91 or 2022
   reduce what the savings can pay for.
4. **Everything stays configurable.** Each rate defaults to an official figure
   and can be changed later in `plan.json` or the GUI.

**Success:** the cost of inflation shocks and of faster-rising costs shows up in the
chance the money lasts and in the legacy, and every amount can be read in either
today's or future dollars.

## Decisions taken

- **Approach.** Keep today's dollars as the working unit and give each future its
  own inflation path. A nominal-dollar rewrite and a joint historical replay were
  both rejected.
- **General inflation is Canada's all-items CPI.** CPP, OAS, tax brackets and the
  TFSA limit are indexed to it. Its history is `rules.CPI`, 1986–2025, which
  averages 2.42% a year.
- **Shocks act by making returns lag inflation in the short run.** In a
  high-inflation year the real return falls; in a low one it rises. The long-run
  average is unchanged.
- **Faster-rising costs grow by a yearly rate on top of CPI** and are the same in
  every future. The defaults, with their sources:

  | Category | Default above CPI | Source |
  |---|---|---|
  | Base spending | +1.54% (about 4% a year in dollars) | the owner's choice |
  | Care (late-life cost) | +1.1% | StatCan CPI 18-10-0004-01, health care services, Canada, 1985–2025 |
  | Education (per student) | +0.4% | same table, tuition fees, Canada, 2005–2025 |
  | Property tax | +6.5% | City of Calgary typical single-detached tax bill, 2023–2026 |
  | Home insurance | +6.2% | StatCan CPI, homeowners' home and mortgage insurance, Alberta, 2005–2025 |

  Property tax at +6.5% a year compounds hard. The owner chose it knowingly over a
  time-limited version.
- **Future dollars come from a switch** in the report and the GUI. It defaults to
  today's dollars, and the headline tiles always stay in today's dollars.

## Part 1: data and inputs

**`rules.py`**
- `CPI` (already exists): yearly all-items changes, 1986–2025.
- New `COST_GROWTH`: one `Rule` per category for the default rate above CPI. Each
  cites its source page and states the period in its comment.

**`inputs.py`**
- `Returns.inflation` (exists). Blank means random paths from `rules.CPI`. A number
  means steady inflation at that rate, with no shocks.
- `Returns.inflation_shocks: bool = True`. False gives steady inflation at the
  average (or at `inflation` if set), with no return lag.
- New dataclass `CostGrowth`: `base`, `care`, `education`, `property_tax`,
  `insurance`. Each is a yearly rate above CPI; `None` means the `rules` default.
  It lives on `PlanInputs.cost_growth`, and the plan.json section `cost_growth` is
  optional.
- Validation: each rate must be between −5% and +15%. A `PlanError` names the
  field.

**GUI (Investing tab)**
- The inflation field (exists), a "shocks" checkbox, and a "Costs rising faster
  than inflation" card with the five rates. Each field shows its default as a
  placeholder.

## Part 2: engine

1. **Inflation paths.**
   - `draw_futures(plan, paths, seed)` returns `(R, D, I)`, where `I` is a
     `(T, N)` array of yearly inflation.
   - With shocks on, each future's path is built from consecutive 5-year blocks of
     `rules.CPI`, each block starting at a random year chosen so the whole block
     fits in the data (1986–2021 for the 1986–2025 series), using its own RNG
     stream `default_rng((seed, "inflation"))`. The return and lifespan draws must
     not change.
   - With shocks off, `I` is constant: `returns.inflation` or the CPI average.
   - The average future uses the constant average. The bad-luck future uses its
     path's `I`.
2. **Returns lag inflation.**
   - Before simulating, `real_t = (1 + R_t) × (1 + π̄) / (1 + I_t) − 1`, where π̄
     is the plan's average inflation: `returns.inflation` if set, else the
     geometric mean of the full CPI history (`rules.historical_inflation()`).
   - This applies inside `draw_futures` (and wherever returns are built), so every
     caller gets the same adjusted returns.
   - With shocks off, `I_t = π̄`, so returns are unchanged.
3. **Cost growth (today's dollars, deterministic).**
   - Factor for category c in year t: `g_c(t) = (1 + rate_c) ** t`.
   - The yearly spending level is split into:
     - home costs (`home.property_tax` × g_pt + `home.insurance` × g_ins, both
       scaled by `new_value / value` after downsizing);
     - everything else (base + dated changes − home costs today) × g_base.
   - Slow-go/no-go shares, the survivor share and the guardrail/bad-market rules
     still apply to the total, as now.
   - `spending.care` × g_care.
   - Education costs per student × g_edu, applied in `education.schedule`.
4. **Price level per future.** `level_t = Π_{s<t} (1 + I_s)`, (T, N).
   - The non-registered cost base and the RESP's contributions and grants deflate
     by `1 / (1 + I_t)` per path. This replaces the current constant `deflate`.
   - Fixed-dollar amounts shrink by `1 / level_t`:
     - the federal pension credit (`rules.FEDERAL["pension_amount"]`), through a
       scale argument to `tax.income_tax`;
     - the Canada Student Grant maximum;
     - the RESP accumulated-income-payment RRSP limit.
5. **Records.** `Projection.inflation` holds `I` (T, N) and `Projection.price_level`
   holds `level` (T+1, N), for display and tests.

Unchanged: tax rules, CPP/OAS (indexed), money events (held flat in today's
dollars), and the CESG schedule (deterministic; its erosion is noted as a
simplification).

## Part 3: what-ifs, optimizer, display

- **Shared futures.** `scenarios`, `optimize` and saved scenarios use the same
  `draw_futures` output, so inflation paths are common to every comparison.
- **New what-if** in `scenarios.variants`: "Steady inflation (no shocks)", which
  sets `returns.inflation_shocks=False`.
- **Report switch.**
  - It sits in "Today's dollars / Future dollars" header controls.
  - Future dollars multiply each year's amounts by `(1 + π̄) ** t`, the average
    path.
  - It applies to the income chart and the money-left chart (two trace sets
    toggled with Plotly `updatemenus`, like the existing bad-luck switch), the
    year-by-year table, the children's-education table and the Action plan
    amounts. Each table is rendered twice, and a small inline script toggles them.
  - The headline tiles stay in today's dollars, with a note.
- **GUI switch.** Same idea. The preview endpoint returns the factor series, and the
  page multiplies chart traces and tile values on the client.
- **Assumptions table.** Every rate with its source and period, plus one line on
  how inflation reaches the plan.
- **Action plan.** `actions.Action` gains the year's average-path factor, so the
  report can show amounts in either kind of dollars.

## Part 4: testing (offline, invented households)

- **Inflation draws.** Paths are made of 5-year blocks of `rules.CPI` values; the
  same seed gives the same paths; return and lifespan draws are identical with
  shocks on or off; steady mode gives a constant path.
- **Return lag.** A year at π̄ leaves returns unchanged, a high year lowers them, a
  low year raises them; with shocks off returns are unchanged.
- **Cost growth.**
  - Each category's factor applies in the right year.
  - Home costs are split from base spending.
  - Downsizing scales the home part.
  - A rate below 0 reduces spending.
  - Education costs grow in the schedule.
- **Price level.**
  - The cost base deflates per path.
  - The pension credit, student grant and AIP limit shrink with the price level;
    with zero inflation everything matches today's results.
- **Display.** The future-dollar factor series are correct; the report contains
  both views and the switch; the GUI preview returns the factors.
- **Validation and defaults.** Defaults come from `rules.COST_GROWTH`; an
  out-of-range rate names the field.
- **Regression.** With every rate at 0 and shocks off, results equal today's.
  This is the backbone check.

## Out of scope / simplifications

- The CESG yearly and lifetime limits are held flat in today's dollars.
- Inflation doesn't change the money events' amounts.
- City-level series exist only for all-items CPI. Calgary property tax uses the
  City's published typical bills, which go back only to 2023.
- Refresh `rules.CPI` and `COST_GROWTH` each January with the other rules.
