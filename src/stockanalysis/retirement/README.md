# `stockanalysis.retirement` — Canadian retirement planner

Projects a one- or two-person Canadian household year by year under federal +
Alberta tax, CPP, OAS (clawback, deferral, +10% at 75), RRSP→RRIF minimums, TFSA
room and Alberta LIF rules, over 10,000 simulated return paths plus an "average"
future (a steady median return) and a "bad-luck" future (the 10th-percentile
path, replayed alone).

It is a **separate, on-demand feature**, not part of `pipeline.run`. Everything is
in **today's dollars**. The code is public; the household's numbers are not.

## Privacy

Everything personal lives in the gitignored root folder `retirement/`
(`/retirement/` in `.gitignore`, root-anchored so this package stays tracked):

- `retirement/plan.json`: the household's plan (and `plan.json.bak`, written by the GUI)
- `retirement/output/<timestamp>/`: `retirement_report.html` + `summary.json`

`tests/test_privacy.py` fails the suite if anything under `retirement/` is ever
tracked. Tests use invented households only (`inputs.TEMPLATE`).

## Run

```bash
stock-analysis retire --init      # first time only: starter retirement/plan.json (invented values)
stock-analysis retire             # -> retirement/output/<ts>/retirement_report.html + summary.json
scripts/retire.sh                 # the same, logged to retirement/logs/retire_<ts>.log, then opens the report
stock-analysis retire --gui       # edit plan.json in a local web page (see below)
stock-analysis retire --optimize  # highest safe spending, earliest safe retirement, best CPP/OAS ages
```

| Flag | Meaning |
|---|---|
| `--inputs PATH` | plan.json (default `retirement/plan.json`) |
| `--holdings PATH` | holdings file for live balances (overrides plan.json `balances`) |
| `--out DIR` | output root (default `retirement/output/`) |
| `--paths N` | simulated futures (default plan.json `returns.paths`) |
| `--scenario-paths N` | futures per what-if (default: the same as `--paths`) |
| `--seed N` | random seed (default plan.json `returns.seed`) |
| `--optimize` / `--target PCT` | print the planning tools' answers at a success target (default 90%), no report |
| `--gui` / `--port N` / `--no-browser` | the plan editor page (port 8765 by default) |

**Balances** come from, in order: `--holdings FILE` → plan.json `balances` → the
holdings workbook (`data/holdings_workbook.xlsx`). Workbook accounts are sorted by
name (RRSP / RRIF / TFSA / RESP / LIRA / LIF / Locked-in / DCPP / Pension keywords,
plus `holdings.owners` / `holdings.accounts` in plan.json). An account that can't
be sorted **stops the run** rather than being dropped silently.

## The plan editor (`--gui`)

A local page at `http://127.0.0.1:8765/` (only reachable from this machine):

- **Tabs:** People, Spending (money events, guardrails), Education, Home, Investing,
  Inflation (the rate, shocks, costs above CPI), Optimize, Compare (saved scenarios) and Advanced (pension match, ESPP, RRSP room).
  Retirement, CPP/OAS and RRIF ages, spending and downsizing are sliders.
- **Live estimate:** each edit re-runs a 1,000-future estimate (~0.8 s). It shows
  the report's gauge and money-left chart, plus tiles that give the change against
  the *saved* plan on the same futures.
- **Validation:** an invalid value is named, its field is highlighted, and it is
  never saved.
- **Save** writes plan.json and keeps the previous file as `plan.json.bak`.
- **Save & generate report** also runs the full report (~20–25 s) and links to it.
- **Read-only sections:** `holdings`, `balances` and `scenarios` can't be edited
  on the page and are kept exactly as they are; `saved_scenarios` is edited on the
  Compare tab.

Press Ctrl-C in the terminal to stop it.


Both the editor and the report have a **Theme** button: Auto (follow the system's light or
dark setting), Dark or Light. The choice is remembered by the browser.

## Planning tools (`--optimize`, the GUI's Optimize tab)

The planning tools search the plan instead of changing one thing at a time.
Every candidate sees the same random futures (returns and lifespans), so
differences between candidates come from the change, not luck.

- **Highest safe spending:** the most after-tax base spending, rounded down to
  C$1,000, that keeps the chance the money lasts at or above the target.
- **Earliest safe retirement:** the earliest retirement that keeps that chance.
  Everyone moves by the same number of years (it can also come out *later* than
  planned).
- **Best CPP and OAS start ages:** tries every legal pair for each person and
  keeps the pair that leaves the most after-tax money on average over 300 drawn
  lifespans (at a steady median return), so starting late pays only in the futures
  where you live to collect it. Each person is searched with the others held fixed,
  and the search repeats until nobody's ages move; a process pool keeps it to about
  20 s. The page shows the cost of every other start age, because the choice is
  often nearly a tie.

- **How much to draw from RRSPs:** tries every yearly target from $0 to $100k per
  person (each of you tops taxable income up to it with RRSP money, so most is drawn
  before CPP, OAS and the RRIF minimums start), next to your current setting. Each
  target shows the expected legacy and lifetime tax over 300 drawn lifespans and the
  chance the money lasts; pick the goal (most legacy, least lifetime tax, safest)
  and the best target is highlighted. **Use this** switches the plan to that
  steady-income target. About 15 s.

In the GUI, **Use this** copies an answer into the plan you're editing; **Save**
keeps it. Library: `optimize.affordability(plan, target=0.9)` and
`optimize.best_benefit_ages(plan)`, `optimize.rrsp_drawdown(plan).best("legacy")`.

## plan.json

| Section | Holds |
|---|---|
| `people[]` (1–2) | `age`, `retire_age`, `sex` (`female` / `male` for the life table; omit to average), `cpp_start_age` / `oas_start_age` (60–70 / 65–70), `cpp_at_65` (the My Service Canada figure — prefer it) or `cpp_years` + `cpp_earnings_ratio`, `years_in_canada_at_65`, `rrif_start_age` (default 65), `lif_start_age` (50–71; default max(50, retire_age)), `unlock_share` (≤ 0.5), `tfsa_room` (unused room from past years, *before* this year's limit), `salary` (gross pay while working: it pays income tax and CPP/EI premiums, then spending, then contributions; the rest is saved or the gap drawn), `pension_match` (employer match as a multiple of your own pension contribution, e.g. 1.75; only your part comes out of salary), `espp` (`{"rate": 0.25, "cap": 25000, "discount": 0.15}`: paid from salary, shares at market value into non-registered, the discount taxed as salary), `rrsp_room` (the "RRSP deduction limit" on your Notice of Assessment; contributions above your room go to the TFSA; left out, room isn't checked), `contributions` / `contributions_when_partner_retired` per account (`pension`, `rrsp`, `tfsa`, `nonreg`, and for a couple `spousal_rrsp`) |
| `spending` | after-tax `base` (today's $), dated `changes`, go-go / slow-go / no-go (`slow_go_age`, `slow_go_share`, `no_go_age`, `no_go_share`, `care`), `survivor_share` (0.70), `rule`: `bad_market` (cut `bad_market_cut` when investments fall below `bad_market_trigger` × their value the first year nobody earns) or `guardrails` (`guardrail_band`, `guardrail_step`, `guardrail_stop_years`, `guardrail_floor`, `guardrail_ceiling`) |
| `home` | `value`, `downsize_age` (people[0]'s age; can't be in the past), `new_value`, `selling_cost`, `moving_cost`, `property_tax`, `insurance` |
| `returns` | `model`: `history` (default, see Returns below) or `lognormal`; `stocks` / `bonds`: expected compound real returns (default FP Canada 2026: equities 6.3% and fixed income 3.2% nominal with 2.1% inflation, so 4.11% / 1.08% real, before fees); `mix`: `[[age, stock share], ...]` on people[0]'s age (default `[[0, 0.8]]`); `mean`, `sd` (real, `lognormal` model only), `paths`, `seed`; `inflation` (yearly; default Canada's CPI average over the last 40 years, 2.42% for 1986–2025); `inflation_shocks` (default `true`: each future replays 5-year runs of 1928–2025 inflation, centred on the 40-year average) |
| `cost_growth` (optional) | yearly growth above inflation per cost: `base`, `care`, `education`, `property_tax`, `insurance` (between −0.05 and 0.15). Omitted ones use the defaults in `rules.COST_GROWTH`; `base` defaults to 0 |
| `withdrawal` | `rrsp_first` / `proportional` / `steady_income` (+ `steady_income_target`); `tfsa_top_up` (default on): each January a retiree fills new TFSA room from non-registered money |
| `education` (optional) | `kids[]` (`name`, `age`, `start_age` 18, `years` 4, `living` `home`/`away`, `cesg_received`), `costs` per student-year (`home` 11,000 / `away` 25,000 today's $, editable estimates), RESP `resp_balance` (default: the holdings' RESP), `contributed` / `grants` so far (default: estimated as if every year's grant was collected), `contribute` (each January while it still earns the grant), `aip_to_rrsp`, `student_grant` (apply for the Canada Student Grant), `childcare` (yearly child care already in spending, for the deduction). The kids also drive the Canada Child Benefit |
| `nonreg_income` (optional) | yearly payouts of the non-registered accounts as shares of their balance: `eligible_dividends` (Canadian companies), `foreign_dividends`, `interest`. Part of `returns.mean`, reinvested, taxed every year. Omit for none |
| `balances` (optional) | `{owner, type, balance, cost}` rows; omit to read the holdings workbook |
| `holdings` | owner keywords (whole words) and explicit `accounts` for names that don't say what they are |
| `events` (optional) | money in / out and side income: `label`, `amount`, `year` or `age`, `every`, `until` / `until_age`, `kind` (`cash` / `income`), `person` |
| `saved_scenarios` (optional) | `{name, changes: {dotted.path: value}}` versions compared side by side |
| `scenarios` | the what-if settings: `downsize_ages` to test (null = never), `cheaper_home_share` |

## Children's education (the RESP)

The family RESP pays school first. Each January it takes the contribution that
earns the largest government grant (CESG) still available: 20% of up to C$2,500
per child, or C$5,000 when catching up missed years, until the child reaches the
C$7,200 lifetime grant or the year they turn 17. Anything that earns no grant isn't
contributed. This year's contribution is assumed already in the balance.

- **Shortfall:** school costs the RESP can't cover are your spending those years
  (from savings while you still work).
- **Leftover:** after the last child's school, your contributions come back
  tax-free and unused grants are repaid. Growth goes to the subscriber's RRSP, up
  to C$50,000 (that needs RRSP room), and the rest is taxed as income plus 20%.

- **Canada Student Grant** (`student_grant`, on by default): up to C$4,200 per
  student per school year. It is full below the first family-income threshold
  (C$76,952 for a family of 4), none at the cut-off (C$129,769), and assumed to
  fall in a straight line between. It is tested each year on the plan's own
  taxable family income for the year before:
  - salary while working;
  - RRSP/RRIF withdrawals, CPP, OAS, payouts and half of realized gains count;
  - TFSA withdrawals don't.

  So the withdrawal order matters: drawing `proportional` instead of
  `rrsp_first` can keep income low enough in school years. A retired household's
  first year has no known prior income, and gets no grant that year. The grant
  reduces the school cost before the RESP pays. The amount is the one announced
  to the end of 2026–27, held flat like every rule.
- **Not modelled:** the additional CESG for lower incomes, which adds 10–20% on
  the first C$500 a year but counts toward the same C$7,200 lifetime cap, and
  Alberta's own student aid.

Withdrawals for school are taxed in the student's hands, which the planner treats
as no tax. The plan assumes the 16–17 grant condition is met (C$2,000
contributed before the year the child turns 15).

## The report

The headline numbers (chance of success, legacy, lifespans) stay at the top. Everything
else is in tabs: **Plan** (the action plan), **Options** (expert planning, saved
scenarios), **Children's education** (when the plan has children), **Income
projection**, **Net worth** (with the range of outcomes), **Cash flow** (the
year-by-year statement) and **Details** (assumptions, the refund check, the rules). A link ending in a
section's id, such as `#money-left`, opens its tab. Printing shows every tab.

- **Action plan:** the plan as a dated to-do list. It lists everything the
  projection assumes you do, with years and amounts from the average future:
  - retiring and the TFSA top-ups;
  - how much to withdraw from RRSPs/RRIFs in each phase;
  - converting to a RRIF and moving the LIRA into a LIF;
  - starting CPP and OAS;
  - electing pension splitting;
  - downsizing;
  - RESP contributions, Canada Student Grant applications and closing the RESP.

  Below it, **Information to add** lists the inputs the plan still estimates
  (Notice of Assessment RRSP limits, the CPP statement, RESP statement figures).
- **Chance the money lasts as long as either of you lives:** the share of futures
  with no short year while anyone is alive. Each future draws when each person dies
  (see Lifespans below); `end_age` is only the "plan to" age of the average and
  bad-luck futures and the charts.
- **Lifespans:** each person's median age at death, the chance one of you reaches 95,
  and the median years a survivor lives alone.
- **Legacy:** the after-tax estate at `end_age` in the average future, home included. Registered money and
  unrealized gains are taxed at the top rate at the second death.
- **Short years / investments at retirement:** shown for the average future and
  the bad-luck future.
- **Rate of return:** the typical (median) real return. It sits below the
  arithmetic mean because of volatility.
- **Expert planning:** each row changes one thing on the *same* futures, so a
  difference is that change's effect. Rows are ranked by change in success.
- **Income projection:** each year's spending + tax, stacked by source. The
  Bad-luck button shows short years in red.
- **Every rule** with its official source.
- **Cash flow:** one row per year of the average future. Sources (income and
  withdrawals, plus any shortfall) add up to the uses (spending, tax, saved). The
  investments then roll from one January 1 to the next row's: less the drawn
  sources, plus what was saved and the year's growth, plus Other (an employer's
  pension match, home-sale money, RESP leftovers, the CPP death benefit). No column
  repeats another, so there is no separate uses total, drawn or next-January-1 column.
- **Net worth:** a balance sheet for each January: every account, the home, and the
  tax due if everything were cashed in that day (RRSP/RRIF and pension/LIF at the top
  rate, plus the taxable part of investment gains). It's the same rule as the legacy,
  so the Estate column is the Legacy tile. Drawing the money out slowly usually costs
  less tax than this. A chart by age and a table at key moments (today, retirements,
  65, 72, the first death, the estate).
  Below them, **How wide the range is** shows investments by age across all the
  simulated futures: the 1-in-10 bad to 1-in-10 good band, the typical line, the home.
- **Child benefit:** the Canada Child Benefit for the children in `education.kids`
  under 18, on last year's net family income, as its own income bar. Set
  `education.childcare` (yearly child care already in your spending) to get the
  child care deduction, claimed by the lower earner while a child is under 16.
- **Saved scenarios** (`saved_scenarios` in plan.json; GUI: Compare tab): named
  versions that store only the fields they change as dotted paths, e.g.
  `{"name": "Retire at 50", "changes": {"people.0.retire_age": 50}}`. Any single field
  works, including contributions (`people.0.contributions.rrsp`); edits to lists
  (events, children, dated changes) aren't captured. The report and
  the Compare tab show the plan and every scenario side by side (chance the money
  lasts, legacy, lifetime tax, spending, retirement ages) on the same futures.
- **Guardrail spending** (`spending.rule: "guardrails"`; GUI: Spending tab, "When
  markets move"): instead of one cut in a bad market, once nobody earns the plan
  compares each year's withdrawal rate with the first one; 20% above it spending is
  cut 10% (not in the last 15 years), 20% below it spending is raised 10%, always
  between 75% and 150% of plan (`guardrail_band`, `guardrail_step`,
  `guardrail_stop_years`, `guardrail_floor`, `guardrail_ceiling`). A year that still
  can't be paid at the floor counts as short. The report shows the
  lowest spending level reached in the typical and the 1-in-10 bad future.
- **Money events** (`events` in plan.json; GUI: Spending tab): one-off or repeating
  amounts in (untaxed, saved) or out (spent that year), e.g.
  `{"label": "Car", "amount": -40000, "year": 2030, "every": 10}`, and temporary
  income taxed like salary, e.g. `{"label": "Part-time", "kind": "income",
  "amount": 30000, "person": "B", "age": 60, "until_age": 64}`. Money in shows as
  "One-time money" in the income chart.
- **Tax refund check:** the refund the model expects from your RRSP contributions
  beside the "TAX REFUND" deposits in your bank CSVs (`--bank`, default
  `retirement/bank/`, private). A gap points to a deduction or credit the plan
  doesn't know about. It never changes the projection.

## Library

```python
from stockanalysis import holdings
from stockanalysis.retirement import inputs, engine, scenarios, cli

plan = inputs.load_inputs("retirement/plan.json")            # or inputs.parse(dict)
plan = inputs.with_holdings(plan, holdings.load()["holdings"])  # if plan.json has no balances
result = engine.run(plan, paths=2000, seed=7)                 # PlanResult
result.simulated.success, result.average.legacy[0]
baseline, ranked = scenarios.rank(plan, paths=2000, seed=7)   # one-change what-ifs
out, result = cli.generate(plan, "plan.json balances")        # writes report + summary
```

## Returns

- **Historical model (the default).** Each simulated future strings together
  5-year runs of real years from 1928–2025: the US stock return (S&P 500 with
  dividends), the US bond return (10-year Treasury), both after US inflation, and
  Canada's inflation in the same year. So the Depression (1929–32), the 1970s
  (high inflation *and* poor returns together), 2000–02 and 2008 can all happen.
- **Your averages, history's shape.** Each series is shifted so it averages your
  `stocks` and `bonds` returns (defaults: the FP Canada Projection Assumption
  Guidelines, which planners in Canada use). The swings, streaks and crashes stay.
- **The mix.** `mix` lists `[age, stock share]` points on people[0]'s age; the share
  moves in a straight line between them and stays flat outside. The portfolio is
  rebalanced every January. More bonds lowers both the swings and the average.
- **Limits.** It's US market history for a Canadian portfolio, and no future can be
  worse than the worst five years on record (1937–41: about −46% for stocks after
  inflation, even at the FP Canada average). Fees aren't modelled: lower `stocks`
  and `bonds` by what you pay.
- **The old model.** `model: "lognormal"` uses `mean` and `sd` (a bell curve of
  returns, unrelated to inflation). The what-ifs include "Smooth returns (old
  model, same average and swings)", the old model given your mix's own average and
  yearly swings, which shows how much the model alone changes the answer, and "10 points more
  bonds".

## Inflation

Everything is in today's dollars, so spending, CPP, OAS, tax brackets and TFSA
limits keep pace with general inflation without further input. Inflation still
reaches the plan in four ways.

- **The rate.** General inflation is Canada's all-items CPI from Statistics Canada
  table 18-10-0004-01, stored in `rules.CPI` from 1928: 2.42% a year on average
  over the last 40 years (0.2% to 6.8% year by year), the planner's default rate.
- **Shocks.** Each simulated future replays 5-year runs of 1928–2025, so a
  1930s-, 1970s-, 1980s- or 2022-style stretch can happen. The runs are shifted so
  the whole history averages the last 40 years' 2.42% (1928–2025 averaged about
  3%): they keep their shape, so 1974–81 still reads as eight years of high
  inflation, but the average rate doesn't rise. In the historical model the returns
  of the same years come along, so they already hold what inflation did. In the
  `lognormal` model investments don't keep up within the year instead: a
  high-inflation year cuts that year's real return, a low one adds to it.
  Set `returns.inflation_shocks` to `false`, or `returns.inflation` to a number,
  for steady inflation. The what-ifs include "Steady inflation (no shocks)".
- **Costs that outpace it.** `cost_growth` sets each cost's yearly growth above
  CPI: base spending 0 (the default), care +1.1% (health care services),
  education +0.4% (tuition, last 20 years), property tax +6.5% (City of Calgary's
  typical bill, 2023–2026) and home insurance +6.2% (Alberta, last 20 years).
  Sources are in `rules.COST_GROWTH`. A negative rate means the cost lags inflation.
- **Amounts fixed in dollars.** A non-registered investment's book value, the RESP's
  contributions and grants, the C$2,000 federal pension credit, the student grant
  and the C$50,000 RESP-to-RRSP limit aren't indexed, so they shrink with each
  future's prices. Taxed gains are therefore nominal gains, inflation included.
  The RESP grant limits are still held flat, a small overstatement.

The report and the GUI switch between today's dollars and future dollars (each
year's dollars on the average inflation path).

## Spousal RRSP and pension splitting

- **Pension income splitting** is automatic. Each year, once someone is 65+ with
  RRIF/LIF income, the tax tries every split from 0% to 50% and uses the
  cheapest. The Action plan dates the first year, when you elect it on Form T1032.
  Plain RRSP withdrawals and anything before 65 can't be split.
- **A spousal RRSP** (`contributions.spousal_rrsp`, for a couple) moves money to
  the partner *before* 65, when splitting isn't allowed:
  - The contributor deducts it, and it uses their RRSP room together with their
    own RRSP contributions; the own RRSP is trimmed first when room runs out.
  - The money lands in the partner's RRSP and is taxed in their hands later.
  - The plan draws the partner's own RRSP money first.
  - Spousal money withdrawn within 3 calendar years of a contribution is taxed
    back to the contributor (the attribution rule), except RRIF minimums and
    after the contributor's death.
  - The Action plan says until when to leave it alone.

  It helps most when one spouse would retire with much more registered money
  or a much higher income than the other.

## TFSA top-up

New TFSA room appears every January (the annual limit, plus last year's
withdrawals). While you work, your planned TFSA contributions fill it. Once
you're retired there's no surplus left to save, so with `tfsa_top_up` on (the
default) the plan moves non-registered money into each retiree's room every
January: their own first, then their partner's. That money's growth and payouts
are tax-free from then on, and less is left to be taxed at death. Moving shares
in counts as selling them, so the gain on them is taxed that year with the
household's other income. A loss isn't claimed. Turn it off on the Investing tab
or with `"tfsa_top_up": false`.

## Known simplifications

- Lifespans are independent of each other and of health; the year of death counts
  as a full year.
- No GIS, no QPP, and no provinces other than Alberta.
- Non-registered payouts are a fixed share of the balance. Foreign withholding tax
  is ignored (the foreign tax credit roughly offsets it), and selling to pay the
  payout tax while working doesn't realize gains. Funds' capital-gains
  distributions aren't modelled.
- CPP/OAS count as a full year in the start year.
- Household events (spending stages, downsizing) key on people[0]'s age.
- Working years: the ESPP discount is taxed but carries no CPP/EI; contributions
  the household can't fund are cut pro rata with the year's tax left as planned;
  the pension is treated as defined-contribution for RRSP room.
- The Child Benefit is counted per calendar year (it really runs July to June).
- Guardrails act on the base spending need, so planned drops and big inflows can
  trigger a raise.

## Lifespans and survivor years

- **When each person dies** is drawn per future from Statistics Canada's Alberta
  life table (2021–2023, by sex) with death rates falling at the CPP actuarial
  report's long-term rates (1.0% a year under 90, 0.6% at 90–94, 0.2% at 95+). Set
  `people[].sex` (`"female"` / `"male"`); left out, the two tables are averaged.
- **After the first death** the plan carries on for the survivor: every account
  rolls over untaxed, the deceased's CPP and OAS stop, the survivor gets the CPP
  survivor's pension (60% from 65, a flat rate plus 37.5% before, capped with their
  own pension) and the C$2,500 death benefit, files alone with no pension
  splitting, and spends `spending.survivor_share` (default 70%) of the couple's
  budget; care costs stay whole.
- **The average future** loses the person with the earlier median death at that
  age, and the survivor lives to `end_age`, so the charts and the year-by-year
  table (a † marks the deceased) show the survivor years.
- **Simplified:** the two lifespans are independent, the year of death is a full
  year, and the CPP survivor's pension uses the deceased's CPP at 65.

## Future improvements

Everything planned on 2026-10-03 is built; historical replay was skipped by the
owner (2026-10-04). Add new ideas here.

Treat results as estimates, not guarantees. Check the real CPP statement, and see
a fee-only planner before big decisions.
