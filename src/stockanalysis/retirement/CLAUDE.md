# CLAUDE.md — `stockanalysis.retirement`

Guidance for working on the Canadian retirement planner. User-facing docs are in
`README.md` beside this file. The command workflow (running a plan, explaining
the report, the yearly rules update) is the project skill
`.claude/skills/retirement-planning/SKILL.md`.

## Privacy — the repo is public

The household's plan and reports live only in the gitignored root folder
`retirement/` (`/retirement/` in `.gitignore`, root-anchored so this package stays
tracked). Never paste plan values, balances or report numbers into tracked files,
tests, docs or commit messages. Tests use invented households (`inputs.TEMPLATE`).
`tests/test_privacy.py` fails the suite if anything under `retirement/` is tracked.

## Module map

| Module | Responsibility |
|---|---|
| `rules.py` | Every statutory value as `Rule(value, year, source)`: federal/Alberta tax, OAS, CPP, RRIF factors, TFSA, Alberta LIF. `TAX_YEAR`, `is_stale` |
| `mortality.py` | Lifespans: the Statistics Canada Alberta life table (`QX`, 2021/2023) and CPP-report improvement (`IMPROVEMENT`) as `Rule`s; `qx`, `death_cdf`, `draw_death_ages` (own seed stream), `median_death_age`. Death age = the age on the January 1 after the last year lived; `OMEGA = 111` |
| `tax.py` | Vectorized person tax, household tax, the 5%-step pension-split search (`best_split`) |
| `inputs.py` | `plan.json` → validated `PlanInputs` (`parse(dict)` / `load_inputs(path)`); `validate` raises `PlanError` (a `ValueError` with `.field`, e.g. `people[0].age`); `limits(province)` is the statutory age/share limits, read from rules.py, that `validate` and the GUI's sliders share; `balances_from_holdings` / `with_holdings` sort holdings into (owner, account type); `TEMPLATE` is the invented `--init` plan |
| `engine.py` | Year-by-year accounts over N paths (`simulate(plan, returns, deaths=None)`); `draw_futures` (returns over `life_steps` + death ages); `run` simulates the drawn futures and adds the average future (steady median return) and the bad-luck future (10th-percentile path's returns replayed alone), both over `steps` with `average_deaths` → `PlanResult` |
| `scenarios.py` | One-change what-ifs on common random numbers; `rank` orders them by change in success |
| `education.py` | The RESP's deterministic schedule (`schedule(plan, T)`): January contributions that earn the largest CESG still available, the grants, each year's school cost, and the start-of-plan contributed/grants (estimated by `estimated_grant_received` when not given). The engine walks the path-dependent RESP balance in `_resp_year` |
| `optimize.py` | Planning tools: `affordability` (`max_spending` + `earliest_retirement`, bisection on common random numbers) and `best_benefit_ages` (per-person CPP x OAS grid on the expected legacy over `LIFESPAN_DRAWS` lifespans, coordinate search, `ProcessPoolExecutor`; `workers=1` runs serially) |
| `report.py` | `build_report` (pure; self-contained HTML string), `save_report` / `write_summary` / `latest_summary` (I/O). `headline` (the numbers `summary.json` stores), `success_meter` (omits a "0 pts" delta) and `money_left_chart` are reused by the GUI |
| `cli.py` | `stock-analysis retire`. `generate(plan, source, ...)` is **the one report path** (run → rank → build → save → summary); the CLI and the GUI both call it. `_with_balances(plan, path, load=_load_book)` is the one balance-precedence rule; the GUI passes a caching `load` |
| `gui.py` + `static/planner.html` | `--gui`: stdlib `http.server` plan editor (see below) |

## Conventions that are easy to get wrong

- **Rule values live only in `rules.py`.** Each one is read from the official CRA /
  Service Canada / Alberta page, **never from memory**. Update them every January:
  CRA's T4127 and TD1/TD1AB forms carry the indexed amounts, and OAS changes
  quarterly. Bump `TAX_YEAR` and run `pytest tests/test_retirement_*.py`. The report
  shows a banner once `TAX_YEAR` is behind the calendar. The planner works in
  today's dollars, so rules are held flat for later years. Don't hardcode a rule
  value anywhere else, including the GUI page.
- **Plain RRSP withdrawals are not eligible pension income.** Only RRIF/LIF
  payments at 65+ get the pension credit and pension splitting, which is why
  `rrif_start_age` defaults to 65.
- **The OAS repayment** is deducted (line 23500) before net and taxable income.
- **RRIF minimums** start the year *after* conversion, on the January 1 balance
  with the age on January 1.
- **LIF:** no minimum in its first year. The maximum is the greater of last year's
  return and the Alberta % × the January 1 balance. Alberta allows a one-time 50%
  unlock at 50+.
- **Non-registered payouts are taxed every year** (`plan.nonreg_income`, shares of
  the balance; they are part of `returns.mean`, not extra growth). Eligible
  Canadian dividends go through `tax.income_tax(dividends=...)`: the 38% gross-up
  counts toward net income, so they raise the OAS recovery and trim the age amount,
  and they earn the federal and Alberta credits (`rules.DIVIDENDS`,
  `PROVINCIAL[..]["dividend_credit"]`, from the CRA 5000-D1 and AB428 worksheets).
  Foreign dividends and interest are ordinary income.
  - A **retiree's** payouts enter the household tax loop, so pension splitting and
    the clawback see them.
  - A **worker's** payouts are taxed as `tax(salary + payouts) − tax(salary)`. That
    "drag" is sold out of the account and booked as tax paid and as non-reg
    income, so the income bars still add up.
  - Payouts are added to the cost base. Lifetime tax can therefore *fall* while
    the legacy falls too: tax moves from death to every year and loses compounding.
    That is correct, not a leak.
- **The RESP is outside the household's investments.** The holdings' RESP
  accounts are left out of `accounts` and read only into `education.resp_balance`.
  School is paid from the RESP first; the shortfall is household need (from savings
  while anyone works, via `cash_need`). Withdrawals take growth and grants first
  (taxed to the student: about nothing), then contributions. At `end_step`:
  contributions go back to people[0]'s non-registered account, grants are repaid,
  and growth goes to the RRSP (up to `rules.RESP["aip"]`). The rest is taxed plus
  20%: on top of salary while people[0] works (`tax.extra_tax`), otherwise as
  their ordinary income inside the household tax loop, alongside CPP, OAS,
  withdrawals, the clawback and splitting.
  - The `Schedule` rides on `Projection.school`; the report and GUI read it from
    there, never rebuild it.
  - Income-tested amounts use `tax.total_income` (line 15000), the same function
    `income_tax` uses for gross income. Don't hand-assemble income again.
  Contributions start the year after `start_year`.
  - The **Canada Student Grant** (`education.student_grant`, `rules.STUDENT_GRANT`)
    is path-dependent: each year the engine records `last_income`, the household's
    line-15000 income (workers' salary and payouts; each part's ordinary + pension
    + OAS + grossed-up dividends + half of gains). The next school year's grant
    comes from it. A missing salary while working counts as income too high, and
    so does the year before a retired household's plan starts.
  - Statutory values live in `rules.RESP` and `rules.STUDENT_GRANT`; the cost per student-year is a plan input (`inputs.EDUCATION_COSTS`
  defaults), not a rule.
- **Lifespans and survivor years.** `deaths=None` means everyone lives the whole
  horizon, which is the old engine exactly; keep it that way (every pre-lifespan
  test is the regression anchor). Simulated runs use `life_steps` and drawn deaths;
  the average / bad-luck futures use `steps` and `average_deaths` (the earlier
  median death in a couple, the survivor to `end_age`). A dead person earns, draws
  and pays nothing; each January `roll_over` moves their accounts to the living
  partner untaxed, plus the CPP death benefit once; the survivor gets
  `survivor_cpp` (from `rules.CPP["survivor"]`, none if the deceased's CPP at 65 is
  0), spends `survivor_share`, and is never split with. Balances are NaN after the
  household's `end_step`; read the estate from `final_investments` / `legacy`,
  never `investments[-1]`. Every comparison draws returns **and** deaths with
  `engine.draw_futures`.
- **No registered draws while working.** Nobody's RRSP/LIF is drawn while they
  still work.
- **Salary drives the working years** (`engine.payroll`) when every worker has a
  `salary`: earned income is the gross salary; income tax (on salary less RRSP and
  the worker's own pension share, `own_pension` = pension ÷ (1 + `pension_match`))
  plus CPP/CPP2/EI premiums (`tax.payroll_premiums`, `rules.PAYROLL`) go in `tax`;
  take-home pay covers the need, then the planned contributions (`saved` includes
  them), and the gap is drawn or the surplus saved, TFSA room going to planned
  contributions first. The employer's match enters the pension without touching
  cash. Premiums are recorded in `Projection.premiums` and left out of
  `lifetime_tax` (they buy CPP/EI). If any worker's salary is missing, that year
  falls back to the old rule (earned income = the need), and the report says so.
- **Comparisons share futures.** What-ifs (`scenarios`), the report's
  "current plan" row and the GUI's saved-vs-edited tiles all use the same seed and
  path count. That is what makes a difference the change's effect. Keep it that
  way: `--scenario-paths` defaults to `--paths`, and the GUI previews both plans
  with `preview_paths` and the plan's seed.
- **An unsorted holdings account stops the run.** It never drops silently.
- **Household events key on people[0]'s age.** That covers spending stages and
  `home.downsize_age`.

## Planning tools (`optimize.py`)

- **Success is checked against one fixed set of returns, `R`.** It is drawn once
  and every candidate is simulated on it, so success changes monotonically with
  spending and retirement shift, and bisection is valid. Don't redraw per
  candidate.
- `max_spending` bisects over whole `SPENDING_STEP` multiples, so "one step more
  fails" holds exactly; a test pins this.
- `best_benefit_ages` scores each candidate by the **expected after-tax legacy
  over `LIFESPAN_DRAWS` (300) death draws** at a steady median return, so a late
  start counts only where the person lives to collect it. One candidate is one
  vectorized run, about 0.4 s (300 paths cost about as much as one), so the full
  66-point grid stays: about 20 s on 10 cores. Success is only compared for today's ages vs the best ages.
  Ties keep today's ages. The answer is often a near tie, so the GUI shows every
  age's cost; keep that rather than presenting one "right" age.
- The process pool uses spawn on macOS, so `best_benefit_ages` must be called
  from an importable module or a `__main__`-guarded script, not from stdin. Tests
  use small grids with `workers=1`, plus one pool-equivalence test.
- Deferred ideas are listed under "Future improvements" in `README.md`. Add new
  ones there, not here.

## The GUI (`gui.py`)

- **Structure:** `PlannerApp` holds all behaviour and no HTTP, so it is easy to
  test. `_Handler` routes requests; `PlannerServer(app, port)` binds 127.0.0.1;
  `serve()` is what `--gui` calls.
- **Endpoints:**
  - `GET /api/plan` returns the raw dict plus a preview of the saved plan.
  - `POST /api/preview` runs a quick estimate and writes nothing.
  - `POST /api/plan` saves.
  - `POST /api/report` runs a background job, polled through
    `GET /api/report/status`.
  - `GET /reports/<path>` serves files that stay inside `out_root`.
  - `POST /api/optimize/affordability` and `POST /api/optimize/benefits` run the
    planning tools on the posted draft.
- **Saving:** validate first (including balances), copy the old file to
  `plan.json.bak`, then write tmp + `os.replace`. The page posts back the **whole
  original dict** with only known fields changed, so keys it doesn't edit
  (`holdings`, `balances`, `scenarios`, `_readme`) round-trip untouched. Don't
  rebuild the dict from `PlanInputs`; that would drop them.
- **Security:** every request must carry the server's own `Host` (blocks DNS
  rebinding). Every POST must be `application/json` with a same-origin `Origin`
  (blocks another site rewriting the plan). Keep both checks.
- **Errors:** a `PlanError` goes to the page as
  `{field: "people[0].retire_age", message}`. The page maps `[i]` → `.i` to find the
  `data-field` input to highlight. Statutory slider limits come from `inputs.limits`
  via `/api/plan`; don't hardcode them in the page.
- **Previews:** the saved plan's preview arrives with `/api/plan`, so an unedited
  draft never needs a run. The page keeps one preview in flight at a time, and an
  edit made meanwhile is picked up when it returns.
- **The page:** vanilla JS with no build step. Plotly comes from
  `plotly.offline.get_plotlyjs()` at `/plotly.js`, so the page works offline.
  Colours copy `report.py`'s constants. Percent fields store fractions; a slider
  scales value, min, max and step by 100 together.
- **Packaging:** `static/*.html` is listed under `[tool.setuptools.package-data]`
  in `pyproject.toml`.

## Tests (offline)

`tests/test_retirement_{rules,tax,inputs,engine,scenarios,optimize,report,gui}.py` and
`tests/test_cli_retire.py`:
- Tax is checked against hand-worked federal + Alberta figures.
- The GUI tests start a real server on port 0 against `TEMPLATE` in `tmp_path`,
  and monkeypatch `holdings.load` to fail if it is read.

Use small path counts (40 for runs, 20 for scenarios) to keep them fast.
