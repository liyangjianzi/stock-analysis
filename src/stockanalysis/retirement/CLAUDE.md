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
| `tax.py` | Vectorized person tax, household tax, the 5%-step pension-split search (`best_split`) |
| `inputs.py` | `plan.json` → validated `PlanInputs` (`parse(dict)` / `load_inputs(path)`); `validate` raises `PlanError` (a `ValueError` with `.field`, e.g. `people[0].age`); `limits(province)` is the statutory age/share limits, read from rules.py, that `validate` and the GUI's sliders share; `balances_from_holdings` / `with_holdings` sort holdings into (owner, account type); `TEMPLATE` is the invented `--init` plan |
| `engine.py` | Year-by-year accounts over N paths (`simulate`); `run` adds the average future (steady median return) and the bad-luck future (10th-percentile path replayed alone) → `PlanResult` |
| `scenarios.py` | One-change what-ifs on common random numbers; `rank` orders them by change in success |
| `optimize.py` | Planning tools: `affordability` (`max_spending` + `earliest_retirement`, bisection on common random numbers) and `best_benefit_ages` (per-person CPP x OAS grid on the average-future legacy, coordinate search, `ProcessPoolExecutor`; `workers=1` runs serially) |
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
- **No registered draws while working.** Nobody's RRSP/LIF is drawn while they
  still work; earned income covers them.
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
- `best_benefit_ages` scores each candidate by the **average future's**
  after-tax legacy, a single path at about 0.15 s. That makes 66 grid points per
  person affordable. Success is only compared for today's ages vs the best ages.
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
