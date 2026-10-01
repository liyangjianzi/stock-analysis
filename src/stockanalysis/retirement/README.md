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
scripts/retire.sh                 # the same, logged to logs/retire_<ts>.log, then opens the report
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

- **Tabs:** People, Spending, Home, Investing and Advanced. Retirement, CPP/OAS and
  RRIF ages, spending and downsizing are sliders.
- **Live estimate:** each edit re-runs a 1,000-future estimate (~0.8 s). It shows
  the report's gauge and money-left chart, plus tiles that give the change against
  the *saved* plan on the same futures.
- **Validation:** an invalid value is named, its field is highlighted, and it is
  never saved.
- **Save** writes plan.json and keeps the previous file as `plan.json.bak`.
- **Save & generate report** also runs the full report (~20–25 s) and links to it.
- **Read-only sections:** `holdings`, `balances` and `scenarios` can't be edited
  on the page and are kept exactly as they are.

Press Ctrl-C in the terminal to stop it.

## Planning tools (`--optimize`, the GUI's Optimize tab)

The planning tools search the plan instead of changing one thing at a time.
Every candidate sees the same random futures, so differences between candidates
come from the change, not luck.

- **Highest safe spending:** the most after-tax base spending, rounded down to
  C$1,000, that keeps the chance the money lasts at or above the target.
- **Earliest safe retirement:** the earliest retirement that keeps that chance.
  Everyone moves by the same number of years (it can also come out *later* than
  planned).
- **Best CPP and OAS start ages:** tries every legal pair for each person and
  keeps the pair that leaves the most after-tax money at `end_age` in the average
  future. Each person is searched with the others held fixed, and the search
  repeats until nobody's ages move; a process pool keeps it to about 10 s. The page
  shows the cost of every other start age, because the choice is often nearly a
  tie. It assumes everyone lives to `end_age`, which favours starting late.

In the GUI, **Use this** copies an answer into the plan you're editing; **Save**
keeps it. Library: `optimize.affordability(plan, target=0.9)` and
`optimize.best_benefit_ages(plan)`.

## plan.json

| Section | Holds |
|---|---|
| `people[]` (1–2) | `age`, `retire_age`, `cpp_start_age` / `oas_start_age` (60–70 / 65–70), `cpp_at_65` (the My Service Canada figure — prefer it) or `cpp_years` + `cpp_earnings_ratio`, `years_in_canada_at_65`, `rrif_start_age` (default 65), `lif_start_age` (50–71; default max(50, retire_age)), `unlock_share` (≤ 0.5), `tfsa_room` (unused room from past years, *before* this year's limit), `contributions` / `contributions_when_partner_retired` per account (`pension`, `rrsp`, `tfsa`, `nonreg`) |
| `spending` | after-tax `base` (today's $), dated `changes`, go-go / slow-go / no-go (`slow_go_age`, `slow_go_share`, `no_go_age`, `no_go_share`, `care`), bad-market rule (`bad_market_cut` when investments fall below `bad_market_trigger` × retirement-day value) |
| `home` | `value`, `downsize_age` (people[0]'s age; can't be in the past), `new_value`, `selling_cost`, `moving_cost`, `property_tax`, `insurance` |
| `returns` | real (after-inflation) `mean`, `sd`, `paths`, `seed` |
| `withdrawal` | `rrsp_first` / `proportional` / `steady_income` (+ `steady_income_target`) |
| `balances` (optional) | `{owner, type, balance, cost}` rows; omit to read the holdings workbook |
| `holdings` | owner keywords (whole words) and explicit `accounts` for names that don't say what they are |
| `scenarios` | `downsize_ages` to test (null = never), `cheaper_home_share` |

## The report

- **Chance the money lasts:** the share of futures with no short year up to `end_age`.
- **Legacy:** the after-tax estate at `end_age`, home included. Registered money and
  unrealized gains are taxed at the top rate at the second death.
- **Short years / investments at retirement:** shown for the average future and
  the bad-luck future.
- **Rate of return:** the typical (median) real return. It sits below the
  arithmetic mean because of volatility.
- **Expert planning:** each row changes one thing on the *same* futures, so a
  difference is that change's effect. Rows are ranked by change in success.
- **Income projection:** each year's spending + tax, stacked by source. The
  Bad-luck button shows short years in red.
- **Money left by age**, a **year-by-year table**, and **every rule** with its
  official source.

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

## Known simplifications

- Both spouses live to `end_age` (no survivor benefits).
- No GIS, no QPP, and no provinces other than Alberta.
- No tax drag inside non-registered accounts.
- CPP/OAS count as a full year in the start year.
- Household events (spending stages, downsizing) key on people[0]'s age.

## Future improvements

Considered on 2026-09-30, after the planning tools; not built yet, roughly in
order of value:

1. **Drawing the RRSP down early.** Between retirement and 65, draw the RRSP in
   amounts that stay in a low tax bracket, before CPP, OAS and RRIF minimums stack
   up. It could beat `rrsp_first` / `steady_income` on lifetime tax and the OAS
   clawback.
2. **Survivor years.** The first death ends pension splitting and one OAS, and the
   CPP survivor benefit is capped. This is usually the largest tax jump in a
   couple's plan. Today both live to `end_age`.
3. **Lifespan as a range.** Draw ages at death from Canadian life tables instead of
   a fixed `end_age`. That makes "chance the money lasts" literal, and the CPP/OAS
   optimizer could then weigh longevity instead of assuming it.
4. **Yearly tax on non-registered accounts.** Dividends, interest and realized
   gains are taxed each year. Ignoring that flatters plans that save heavily
   outside RRSPs and TFSAs.
5. **Guardrail spending rules** (Guyton-Klinger style) in place of the single
   bad-market cut.
6. **Saved scenarios side by side** in the GUI, e.g. "Retire at 48" vs "Retire at
   50, downsize at 60", each with its gauge and legacy.
7. **Historical replay.** Run the plan through actual Canadian/US return
   sequences (1970→) beside the random futures.
8. **One-time money events.** Inheritances, education costs, a car every 10 years,
   part-time work income.

Treat results as estimates, not guarantees. Check the real CPP statement, and see
a fee-only planner before big decisions.
