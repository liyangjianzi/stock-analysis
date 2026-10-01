# Canadian Retirement Planner (`stock-analysis retire`) — Design

**Date:** 2026-09-29
**Status:** Approved in conversation (pending spec review)
**Feature:** A `stockanalysis.retirement` subpackage and a `stock-analysis retire`
command that project a Canadian household's retirement year by year, following
Canadian tax law and retirement-benefit rules. The command writes a
self-contained HTML dashboard with:

- the chance the plan succeeds,
- the legacy it leaves,
- a stacked yearly income projection by source,
- ranked what-if suggestions.

## Problem

A one-pot Monte Carlo with a flat tax rate can say *whether* savings last. It
can't say *where each year's income comes from*: CPP, OAS, RRIF minimums, RRSP,
TFSA or non-registered money. It also can't say what taxes cost over a lifetime,
or which withdrawal order is best. Those answers depend on Canadian rules:

- the RRSP must become a RRIF at 71, with minimum withdrawals after that;
- locked-in pension money follows LIF rules;
- a spouse can receive part of your pension income (pension splitting);
- OAS is clawed back above an income threshold;
- TFSA room comes back after a withdrawal;
- CPP and OAS grow if you start them later.

A flat rate hides every one of these.

## Goal

1. **One consistent model.** The chance of success, the yearly chart, the
   suggestions and the KPIs all come from the same year-by-year model. The model
   tracks each spouse's accounts separately and computes federal and provincial
   tax for each person.
2. **A dashboard in the style of Canadian planning software**, with:
   - a success gauge and a legacy gauge;
   - a KPI strip: short years, investments at retirement, rate of return,
     lifetime taxes;
   - "expert planning" suggestions;
   - a stacked income projection with an Average / Bad-luck switch;
   - a money-left chart;
   - a year-by-year table;
   - the assumptions and rules used.
3. **Public code, private data.**
   - The package holds no personal numbers.
   - The owner's inputs (`retirement/plan.json`) and outputs
     (`retirement/output/`) live in the gitignored `retirement/` folder, and
     `tests/test_privacy.py` enforces that.
   - Tests use invented numbers only.

**Non-goals (v1, YAGNI):**
- mortality (the plan assumes both spouses live to `end_age`), and so
  survivor/CPP survivor benefits;
- GIS;
- QPP and provinces other than Alberta (the rules table is keyed by province so
  others can be added later);
- annuities and RDSP;
- spousal-RRSP attribution;
- CPP pension sharing;
- tax drag on non-registered distributions;
- inflation that varies from year to year (everything is in today's dollars);
- probate fees.

## Architecture

It follows `thesis/`: a subpackage with its own CLI module, wired into
`cli.py` through `add_parser` / `dispatch`.

| Module | Responsibility |
|---|---|
| `retirement/rules.py` | **Every Canadian rule value, in one table.** Each entry records the tax year and its official source URL (canada.ca / CRA / Service Canada / Alberta). No rule values appear anywhere else. |
| `retirement/inputs.py` | `PlanInputs` dataclass plus plain-Python validation (no `jsonschema`, matching `thesis.model`). `load_inputs(path)` reads JSON. `balances_from_holdings(holdings, mapping)` sorts `holdings.load()` rows into (owner, account type). `write_template(path)` writes a starter plan with example values. |
| `retirement/tax.py` | Pure, numpy-vectorized tax for one person and one year: federal + provincial brackets, non-refundable credits, OAS recovery tax. Plus `split_pension`, which picks the pension-splitting allocation that minimises the couple's combined tax. |
| `retirement/engine.py` | Pure year-by-year model over N return paths. Returns per-year, per-source income arrays, taxes, balances, shortfalls, success, and the average and bad-luck futures. |
| `retirement/scenarios.py` | Builds the what-if variants of `PlanInputs`, runs each, and ranks them. |
| `retirement/report.py` | Pure: builds the self-contained HTML string (Plotly through a CDN `<script>` tag, like `stockanalysis/report.py`). Also has `save_report`. |
| `retirement/cli.py` | `stock-analysis retire`: load inputs and holdings, run the baseline and scenarios, write the report and `summary.json`, and print the headline numbers. |

`config.py` gains `DEFAULT_RETIREMENT_INPUTS` (`retirement/plan.json`) and
`DEFAULT_RETIREMENT_OUT` (`retirement/output/`).

**Data flow:**
1. `plan.json` + `holdings.load()` → `PlanInputs`
2. → `engine.run` (N paths + average + bad-luck)
3. → `scenarios.rank`
4. → `report.build_report`
5. → `retirement/output/<YYYY-MM-DD_HHMMSS>/retirement_report.html` + `summary.json`

## Inputs (`plan.json`)

```json
{
  "province": "AB",
  "start_year": 2026,
  "end_age": 95,
  "people": [
    {"id": "A", "name": "Person A", "age": 50, "retire_age": 60,
     "cpp_at_65": null, "cpp_years": 25, "cpp_earnings_ratio": 1.0,
     "years_in_canada_at_65": 40, "cpp_start_age": 70, "oas_start_age": 70,
     "contributions": {"pension": 10000, "tfsa": 7000, "rrsp": 15000, "nonreg": 0},
     "contributions_when_partner_retired": {"pension": 10000, "tfsa": 7000}}
  ],
  "spending": {"base": 80000,
               "changes": [{"year": 2036, "amount": -10000, "label": "Kids leave home"}],
               "stages": {"slow_go_age": 75, "slow_go_share": 0.85,
                          "no_go_age": 85, "no_go_share": 0.70, "care": 25000},
               "bad_market_cut": 0.10, "bad_market_trigger": 0.80},
  "home": {"value": 900000, "downsize_age": 65, "new_value": 600000,
           "selling_cost": 0.04, "moving_cost": 20000, "property_tax": 6000},
  "returns": {"mean": 0.05, "sd": 0.15, "paths": 10000, "seed": 7},
  "withdrawal": {"strategy": "rrsp_first", "steady_income_target": 58000},
  "holdings": {"owners": {"A": ["keyword"], "B": ["keyword"]},
               "accounts": {"Some account": {"owner": "B", "type": "pension"}},
               "ignore": []},
  "balances": null,
  "scenarios": {"downsize_ages": [null, 65], "cheaper_home_share": 0.8}
}
```

**Field notes:**
- **Ages:** a person's age is the age they reach during the calendar year.
  `retire_age` is the first year with no earned income.
- **CPP:** `cpp_at_65` takes the figure from the person's *My Service Canada
  Account* statement, which is preferred. If it's null, CPP is estimated as
  `max_cpp_at_65 × min(1, cpp_years_at_retirement / (47 − dropout_years)) × cpp_earnings_ratio`.
- **Savings:** `contributions` apply while the person works.
  `contributions_when_partner_retired` replaces them in the years when this
  person still works but the partner has retired.
- **Spending:** `spending.base` is after-tax household spending in today's
  dollars. `changes` are permanent steps from a given year.
- **Account types:** `rrsp`, `pension` (locked-in → LIRA → LIF), `tfsa`,
  `nonreg`, `exclude` (e.g. the RESP).
- **Sorting holdings accounts:** first the explicit `accounts` map, then
  keywords: `RRSP` / `RRIF` → rrsp, `TFSA` → tfsa, `RESP` → exclude,
  `LIRA` / `LIF` / `DCPP` / `pension` → pension. Anything else is non-registered
  only if it's listed; otherwise loading fails (see Error handling). The owner
  comes from the `holdings.owners` keywords.
- **Balances:** `balances` (a list of `{owner, type, balance, cost}`) replaces
  the holdings lookup when no workbook is available.

## Canadian rules (`rules.py`)

Each value is **verified against the official source during implementation**,
never taken from memory, and stored with `year` and `source`. The table is keyed
by tax year and, where relevant, by province.

| Area | Rule modelled |
|---|---|
| Federal income tax | Brackets and rates; basic personal amount; credits at the lowest rate. |
| Alberta income tax | Brackets and rates (including the 8% bracket introduced in 2025); Alberta personal amount; credits at Alberta's credit rate. |
| Age amount | 65+, federal and Alberta; reduced by 15% of net income above the threshold. |
| Pension income amount | Federal and Alberta credit on eligible pension income (RRIF/LIF withdrawals from 65). |
| Capital gains | 50% inclusion. The proposed increase was cancelled in 2025. |
| Pension splitting | From 65, up to 50% of eligible pension income can be allocated to the spouse. The engine searches allocations in 5% steps each way and keeps the one with the lowest combined tax. |
| OAS recovery tax | 15% of net income above the threshold, capped at the OAS received. |
| RRSP → RRIF | Converted by December 31 of the year the person turns 71. The RRIF minimum applies from the following year: `1/(90 − age)` below 71, then the CRA prescribed factors (71: 5.28% … 95+: 20%) on the January 1 balance. |
| Locked-in pension (Alberta) | LIRA → LIF: earliest LIF age, minimum (= RRIF minimum), maximum (Alberta formula), and Alberta's one-time unlocking of up to 50% on transfer to a LIF. Money can't be withdrawn beyond what these allow. |
| TFSA | Annual limit. Withdrawals come back as room on January 1 of the next year. Withdrawals are tax-free. |
| CPP | Start at 60–70: −0.6% for each month before 65, +0.7% for each month after. General dropout of 8 years out of the 47-year contributory period. Maximum pension at 65. |
| OAS | Starts 65–70, +0.6% for each month deferred. Partial pension = years in Canada after 18 / 40 (minimum 10). **+10% from age 75.** Monthly amount by year. |
| Principal residence | Sale is tax-free: downsizing proceeds go to non-registered with a cost base equal to the amount, so no gain. |
| Death | Registered accounts roll over to the spouse tax-free. At the second death, the remaining registered balance is taxed as income; unrealized non-registered gains are taxed at the capital-gains inclusion rate. Both use the top combined marginal rate from the table, which is used for legacy. |

Every value is assumed to rise with inflation, so it's constant in today's
dollars. If `rules.py`'s latest tax year is older than the current year, the
report shows a warning banner.

## Engine (`engine.py`)

**State:** for each person and path, balances for `rrsp`, `pension`, `tfsa` and
`nonreg`, plus the `nonreg` cost base and TFSA room. Everything is in today's
dollars.

**Returns:** a lognormal real return per year and path, with the arithmetic mean
and sd from `plan.json`, the same across all accounts. Seeded.

**Each year, in order:**
1. **Life events.** Downsizing adds net proceeds to `nonreg` (split evenly
   between the spouses; the cost base rises by the same amount). RRSP → RRIF
   and pension → LIF conversions happen at their ages.
2. **Working years.** If anyone still works, contributions are added. Spending
   is covered by earned income; salary and its tax are outside the model,
   because pre-retirement is on a net-pay basis. For the chart,
   earned income = spending.
3. **Retired years:**
   - **Spending need.** Start from `base`, apply the `changes`, then the stage
     share (slow-go / no-go). Apply the bad-market cut if investments are below
     `trigger ×` their value on retirement day. Then add care costs in no-go
     years; care is never cut.
   - **Guaranteed income:** CPP + OAS by start ages; RRIF and LIF minimums.
   - **Top-up withdrawals** by strategy:
     - `rrsp_first`: rrsp/RRIF → LIF up to its maximum → nonreg → tfsa.
     - `proportional`: all accessible accounts in proportion to their balances.
     - `steady_income`: registered up to `steady_income_target` taxable income
       per person. Any excess over the need goes to the TFSA (up to room), then
       to nonreg. Then nonreg → tfsa.

     The need is split between the spouses in proportion to their accessible
     balances. If one runs dry, the other covers.
   - **Tax:** per person, using the pension-splitting allocation that minimises
     combined tax. Because tax depends on withdrawals, the engine repeats
     `withdrawals = need + tax − guaranteed income` up to 8 times, stopping at a
     C$1 tolerance.
   - **Shortfall:** anything left unfunded once every accessible account is
     empty.
4. **Growth:** balances × (1 + return).

**Outputs:**
- `success` = share of paths with no shortfall year through `end_age`.
- Per-year, per-source income arrays: earned, CPP, OAS, minimums, registered,
  TFSA, nonreg, shortfall.
- Tax, balances and legacy.

**The two single futures:**
- **Average future:** a constant return equal to the lognormal *median*
  (geometric) return, e.g. about 3.9% for a mean of 5% and sd of 15%. Reported
  as "Rate of return".
- **Bad-luck future:** the simulated path at the 10th percentile. Paths are
  ranked first by the age the money runs out (earliest = worst), then by money
  left at the end.

**KPIs, all from the average future unless noted:**
- investments at retirement, when the later spouse retires;
- lifetime taxes (retirement-year income taxes plus tax at the second death);
- legacy at `end_age` (home + TFSA + after-tax nonreg + after-tax registered);
- shortfall years (average and bad-luck futures).

## Scenarios (`scenarios.py`)

Each scenario changes exactly one thing from the baseline:
- the two withdrawal strategies the baseline doesn't use;
- spending −5%;
- person A retires +1 year;
- person B retires +1 year;
- each downsizing age in `scenarios.downsize_ages` (null = never sell), apart
  from the baseline's own;
- a cheaper home: `new_value × cheaper_home_share`.

Each result shows success, its change against the baseline, lifetime taxes and
legacy. They're ranked by change in success, then by legacy.

## Report (`report.py`)

One HTML page:
1. **Top row:**
   - success gauge, with the change against the previous run's `summary.json`
     in the same output root;
   - legacy gauge, with the bad-luck legacy underneath;
   - KPI strip: shortfall years, investments at retirement, rate of return,
     lifetime taxes.
2. **Expert planning:** the ranked suggestions list.
3. **Detailed income projection:** a stacked bar for each year.
   - x labels are both ages (`"62/60"`).
   - Layers: Earned income · CPP · OAS · RRIF/LIF minimums · Registered · TFSA ·
     Non-registered · **Shortfall (red)**.
   - Plotly buttons switch between **Average** and **Bad-luck**; there's a range
     slider.
   - The hover shows each source and that year's tax.
4. **Money left:** investments by age as a 10th / 50th / 90th percentile band,
   plus a home-value line, with markers at retirement(s), downsizing and the
   CPP/OAS starts.
5. **Year-by-year table** (`<details>`): average future, with ages, spending,
   tax, each source and balances by account type.
6. **Assumptions & rules:** the inputs, plus the rules table with year and
   source. Also the holdings file and its age, and the rules-year warning if it
   applies.

Colours and chart marks follow the project's `dataviz` skill, which is read
before any chart code is written. The shortfall layer is always red.

## CLI

```
stock-analysis retire [--inputs PATH] [--holdings PATH] [--out DIR]
                      [--paths N] [--seed N] [--init]
```

- **Defaults** come from `config`.
- **`--init`** writes a starter `plan.json` with example values, then exits.
  It refuses to overwrite an existing file.
- **Output:** a fresh `--out/<timestamp>/` folder holding `retirement_report.html`
  and `summary.json` (success, legacy, lifetime taxes, investments at
  retirement, date).
- **Printed:** the success %, legacy, lifetime taxes and the report path.
- **Library entry point:** `retirement.run(inputs, holdings=None, paths=None) -> PlanResult`.

## Error handling

- **A holdings account that can't be sorted** (no keyword or explicit mapping,
  and not ignored) raises `ValueError` listing every such account name. Money is
  never silently dropped.
- **Missing `plan.json`:** the error gives the path and the `--init` hint.
- **Missing holdings file:** the run uses `balances` from `plan.json`; if those
  are null too, it raises an error that names both options.
- **Validation errors** name the field. Examples: `retire_age` below the current
  age, negative amounts, shares outside [0, 1], an unknown strategy, a province
  not in `rules.py`.
- **Rules year older than the current year:** a warning banner (not an error).
- **Non-convergence of the tax iteration:** capped at 8 rounds, with the largest
  gap left over exposed in the result. Tests assert it stays under C$1 for the
  fixtures.

## Testing (offline, invented numbers)

| File | Covers |
|---|---|
| `test_retirement_rules.py` | Every entry has `year` and `source`; brackets ascend; rates are within 0–1; the RRIF table covers ages 71–95+. |
| `test_retirement_tax.py` | Hand-worked cases: zero tax below the personal amounts; a mid-income federal + Alberta case; age amount phase-out; pension credit; OAS recovery starting at the threshold and capped at the OAS received; 50% inclusion; the optimal split never exceeds the no-split tax and is symmetric. |
| `test_retirement_engine.py` | With zero volatility and zero return:<br>• sources sum to need + tax, or a shortfall appears;<br>• RRIF conversion at 71 and the minimum from 72;<br>• LIF earliest age, maximum and unlocking;<br>• TFSA untaxed and room restored the next year;<br>• each strategy draws from the right accounts;<br>• downsizing is tax-free;<br>• a single-path run equals the same path inside an N-path run;<br>• a rich plan scores 1.0 and an impossible plan 0.0;<br>• the average future uses the geometric return. |
| `test_retirement_inputs.py` | JSON round trip; validation messages; keyword and explicit sorting; RESP excluded; an unsorted account raises; `write_template` refuses to overwrite. |
| `test_retirement_scenarios.py` | Each variant differs from the baseline in exactly one field; ranking order. |
| `test_retirement_report.py` | All six sections present; Average/Bad-luck buttons; a red shortfall trace; the rules-year banner when stale. |
| `test_cli_retire.py` | Flags, defaults, `--init`, output folder and `summary.json`, with `holdings.load` monkeypatched. |

## Docs

In `CLAUDE.md`:
- a module-map row for `retirement/`;
- a "Retirement planner" run section (`stock-analysis retire`);
- conventions:
  - `rules.py` is the only home for Canadian rule values, each verified against
    and cited to an official source, and updated each tax year;
  - personal inputs and outputs stay in `retirement/`.

## Verify during implementation (official sources)

Each of these is confirmed from its official source before it goes into
`rules.py`:
- 2026 federal brackets, rates, basic personal amount, age amount and
  threshold, pension income amount;
- 2026 Alberta brackets, rates, personal amount, credit rate, age amount,
  pension amount;
- 2026 OAS monthly amount (65–74, and 75+) and recovery-tax threshold;
- 2026 CPP maximum at 65, and the adjustment factors;
- 2026 TFSA limit;
- the RRIF prescribed factors;
- Alberta LIF rules: earliest age, maximum formula, 50% unlocking;
- the capital-gains inclusion rate.
