---
name: retirement-planning
description: Run, explain, or change the Canadian retirement planner in the StockAnalysis project — `stock-analysis retire` / `scripts/retire.sh`, the private `retirement/plan.json`, and the HTML report (chance of success, legacy, income by source, what-if suggestions). Use when the user asks to "run the retirement plan", "can we retire at N", "what if we spend / downsize / retire later", "how much can we spend", "explain the retirement report", asks about CPP / OAS / RRIF / TFSA / LIF / pension splitting in their plan, or when the yearly tax and benefit values in `retirement/rules.py` need updating. Native to this repo (no API key) — the `stockanalysis.retirement` subpackage.
---

# Retirement Planning

A year-by-year Canadian retirement projection for a one- or two-person
household, under federal + Alberta tax and the CPP / OAS / RRIF / TFSA / Alberta
LIF rules, over 10,000 simulated return paths. The code is public; the
household's numbers are not.

## Privacy — read first

The repo is **public**. Everything personal lives in the gitignored root folder
`retirement/` (`/retirement/` in `.gitignore`; `tests/test_privacy.py` fails if
anything there is ever tracked):

- `retirement/plan.json` — the household's plan (ages, savings, spending, home).
- `retirement/output/<timestamp>/` — `retirement_report.html` + `summary.json`.

Never paste plan values, balances or report numbers into tracked files, tests,
commit messages, docs or this skill. Tests use invented households only.

## Run it

```bash
scripts/retire.sh                  # run the plan, log to logs/, open the report
scripts/retire.sh --paths 2000     # faster; extra args go to `stock-analysis retire`
NO_OPEN=1 scripts/retire.sh        # don't open the browser
stock-analysis retire --init       # first time only: starter plan.json (invented values)
```

### Planning tools (`--optimize`)

```bash
stock-analysis retire --optimize               # target: 90% chance the money lasts
stock-analysis retire --optimize --target 85
```

Prints the highest safe base spending, the earliest safe retirement (everyone
moved together) and the best CPP/OAS start ages, ranked by the average future's
after-tax legacy. It takes about 20 s and writes no report. The GUI's
**Optimize** tab runs the same tools and adds a table of what every other start
age costs. When the gain is under 0.5% of the legacy, call it a near tie and say
so: health, longevity and wanting the income sooner should decide instead. The
optimizer assumes everyone lives to `end_age`. Deferred ideas are listed in
`src/stockanalysis/retirement/README.md` under "Future improvements".

### Edit the plan in a browser (`--gui`)

```bash
stock-analysis retire --gui                 # opens http://127.0.0.1:8765/
stock-analysis retire --gui --port 9000 --no-browser
```

A local page (only reachable from this machine) with tabs for People, Spending,
Home, Investing and Advanced. Every edit re-runs a quick 1,000-future estimate:
the report's gauge and money-left chart, plus tiles that show the change against
the *saved* plan on the same futures. An invalid value is named and highlighted
and never saved. **Save** writes plan.json (the previous file is kept as
`plan.json.bak`); **Save & generate report** also runs the full report and links
to it. `holdings`, `balances` and `scenarios` aren't editable there and are
kept exactly as they are.

Balances come from, in order: `--holdings FILE` → `balances` in plan.json → the
holdings workbook (`data/holdings_workbook.xlsx`). Before a run with live
balances, **refresh holdings** from the Google Sheet (see CLAUDE.md, Household
risk) so the run doesn't use stale positions.

Library: `from stockanalysis.retirement import load_inputs, run` then
`inputs.with_holdings(plan, holdings.load()["holdings"])` and `run(plan)`;
`scenarios.rank(plan, paths=N, seed=S)` for the what-ifs.

## plan.json in brief

| Section | Holds |
|---|---|
| `people[]` (1–2) | `age`, `retire_age`, `cpp_start_age` / `oas_start_age` (60–70 / 65–70), `cpp_at_65` (the My Service Canada figure — prefer it) or `cpp_years` + `cpp_earnings_ratio`, `years_in_canada_at_65`, `rrif_start_age` (default 65), `lif_start_age` (50–71; default max(50, retire_age)), `unlock_share` (≤ 0.5), `tfsa_room` (unused room from past years, *before* this year's limit), `contributions` / `contributions_when_partner_retired` per account (`pension`, `rrsp`, `tfsa`, `nonreg`) |
| `spending` | after-tax `base` (today's $), dated `changes`, go-go / slow-go / no-go (`slow_go_age`, `slow_go_share`, `no_go_age`, `no_go_share`, `care`), bad-market rule (`bad_market_cut` when investments fall below `bad_market_trigger` × retirement-day value) |
| `home` | `value`, `downsize_age` (people[0]'s age; can't be in the past), `new_value`, costs, `property_tax`, `insurance` |
| `returns` | real `mean`, `sd`, `paths`, `seed` |
| `withdrawal` | `rrsp_first` / `proportional` / `steady_income` (+ `steady_income_target`) |
| `holdings` | owner keywords (whole words) and explicit `accounts` for names that don't say RRSP / TFSA / RESP / LIRA-LIF / Locked-in; an unsorted account **stops the run** |
| `scenarios` | `downsize_ages` to test (null = never), `cheaper_home_share` |

## Reading the report (explain it in plain words)

- **Chance the money lasts** — share of futures with no short year to `end_age`.
- **Legacy** — after-tax estate at `end_age` incl. the home (registered money
  and unrealized gains taxed at the top rate at the second death).
- **Short years / investments at retirement** — average future, and the
  bad-luck future (the 1-in-10 bad run of returns, replayed alone).
- **Rate of return** — the typical (median) real return, below the arithmetic mean.
- **Expert planning** — each row changes one thing on the *same* futures, so a
  difference is the change's effect; ranked by change in success.
- **Detailed income projection** — each bar is that year's spending + tax by
  source; the Bad-luck button shows short years in red.

Everything is in **today's dollars**. Frame results as estimates, not
guarantees; suggest checking the real CPP statement and a fee-only planner for
big decisions.

## Canadian rules (the easy-to-get-wrong part)

- Every statutory value lives **only** in `src/stockanalysis/retirement/rules.py`
  as `Rule(value, year, source)`. When updating (each January, or when the
  report shows the stale-rules banner), read each value from its cited official
  page (CRA T4127 + TD1/TD1AB forms, Service Canada OAS/CPP pages, the CRA RRIF
  factor chart, Alberta's Superintendent of Pensions interest-rate tables) —
  **never from memory** — bump `TAX_YEAR`, and run `pytest tests/test_retirement_*.py`.
- Only **RRIF / LIF payments at 65+** are eligible pension income (pension
  credit + splitting); plain RRSP withdrawals are not — hence `rrif_start_age` 65.
- The OAS repayment is deducted (line 23500) before net and taxable income.
- RRIF minimum starts the year *after* conversion, on the January 1 balance with
  the age on January 1. LIF: no minimum in its first year; max = greater of last
  year's return and the Alberta % × January 1 balance; Alberta allows a one-time
  50% unlock at 50+.
- Nobody's RRSP / LIF is drawn while they still work (earned income covers them).

## Known simplifications

Both spouses live to `end_age` (no survivor benefits); no GIS, QPP or provinces
other than Alberta; no tax drag inside non-registered accounts; full-year CPP/OAS
in the start year; household events (stages, downsizing) key on people[0]'s age.
