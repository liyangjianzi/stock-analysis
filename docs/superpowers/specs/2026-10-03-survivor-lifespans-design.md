# Survivor Years and Lifespan as a Range — Design

**Date:** 2026-10-03
**Status:** Approved in conversation (pending spec review)
**Feature:** README "Future improvements" items 2 and 3 of the retirement
planner (`stockanalysis.retirement`), built as one change. Each simulated future
draws when each person dies from Canadian life tables, and a couple's plan
continues for the survivor after the first death under the survivor rules.

## Problem

Today everyone lives to `end_age`. That has two consequences:

- **"Chance the money lasts" is not literal.** It is the chance the money lasts
  to 95 for both people, not for as long as someone is alive.
- **The largest tax jump in a couple's plan is missing.** After the first
  death, pension splitting ends, one OAS stops, the CPP survivor benefit is
  capped and the survivor files as a single person. None of this is modelled.

The CPP/OAS optimizer has the same blind spot. It assumes everyone lives to
`end_age`, which flatters starting late.

## Decisions (made in conversation)

| # | Question | Decision |
|---|---|---|
| 1 | What the headline means | **Random lifespans.** The money must last only while someone is alive. `end_age` stays as the "plan to" age for the average / bad-luck views and charts. |
| 2 | Life table | **Statistics Canada, Alberta, by sex, plus mortality improvement** at the long-term rates of the CPP actuarial report (cohort rates). |
| 3 | Survivor spending | **New plan input `spending.survivor_share`**, default 0.70, with a GUI slider. |
| 4 | Optimizer | **Score by expected after-tax legacy across lifespans** (steady median return × shared death draws). |

## Goal

1. Success, legacy and lifetime tax reflect realistic lifespans and the
   survivor years.
2. The average-future charts and year-by-year table show a survivor period.
3. Every comparison (what-ifs, affordability, optimizer, GUI preview) still
   shares its random draws, now including death ages.
4. Every new statutory or official value is read from its official page and
   stored with its year and URL, never from memory.

## Non-goals

- Health or lifestyle adjustments to the life table.
- Selling the home at the first death (separate from `downsize_age`).
- Correlated deaths between spouses (lifespans are drawn independently).
- Probate fees, estate administration costs or beneficiary designations
  beyond the spousal rollover.

## Approach

A per-path "alive" mask inside the existing engine. Death ages are a
(person × future) array, drawn once beside the return draws and passed to
`engine.simulate`. Each year's step treats a dead person as having no income,
accounts, contributions or tax, and switches that future to survivor rules.
Everything stays vectorized.

Rejected:

- **A separate single-person engine after the first death.** It can't be
  vectorized, because the first death falls in a different year in each future.
- **Simulating to `end_age` and cutting each future at its deaths.** It is
  wrong: the survivor years would keep the couple's spending, splitting and both
  OAS cheques.

## Part 1 — Data and inputs

### `mortality.py` (new, pure)

- `QX`: the Statistics Canada life table for Alberta (table 13-10-0114-01), the
  death probability by single age 0–110 for females and males. Stored as
  value, data year and source URL, the way `rules.py` stores a `Rule`.
- `IMPROVEMENT`: the CPP actuarial report's long-term yearly decline in death
  rates, by age band and sex as published. Value, year and URL.
- `qx(sex, age, year)`: the table rate × (1 − improvement)^(year − table year),
  capped at 1. `sex=None` uses the average of the female and male rates.
- `draw_death_ages(people, start_year, paths, seed) -> (P, N) int`. Each person's
  lifespan is drawn independently, conditional on being alive at their current
  age, walking the cohort rates year by year: dying during the year at age `a`
  gives death age `a + 1` (see "Alive" in Part 2). Capped at `MAX_AGE = 110`. It uses
  its own generator stream (`np.random.default_rng([seed, 1])`), so the death
  draws never consume the return stream.
- `median_death_age(person, start_year) -> int`: the age at which the cohort
  survival from the person's current age first falls to 50% or below.

If an official page doesn't give a clean number, implementation stops and asks
the owner rather than guessing.

### Plan inputs (`inputs.py`)

- `Person.sex: str | None = None`, one of `"female"`, `"male"` or omitted.
  Omitted uses the average table, and the report notes "set sex for more
  accurate lifespans". Existing `plan.json` files keep working.
- `Spending.survivor_share: float = 0.70`, validated to 0.40–1.00. The bounds go
  in `inputs.limits` so the GUI slider and `validate` share them.
- `end_age` keeps its label "Plan to age". It now drives only the deterministic
  views, the charts and `earliest_retirement`'s upper bound.
- `TEMPLATE` gains `sex` per person and `survivor_share`.

### Shared draws

`returns.seed` seeds both the returns and (through its own stream) the death
ages. Every comparison draws both once and reuses them for every candidate.

### Official values to fetch

- The Statistics Canada Alberta life table (latest three-year period), qx by
  age and sex.
- The CPP actuarial report's ultimate mortality improvement rates.
- The CPP survivor's pension: the rate at 65+, the under-65 formula (flat rate
  plus a share), and the combined-benefit cap with the survivor's own retirement
  pension. These go in `rules.CPP["survivor"]`.
- The CPP death benefit (one-time). It goes in `rules.CPP["death_benefit"]`.

## Part 2 — The engine

### Horizon

`simulate(plan, returns, deaths)` takes `deaths` as a (P, N) array of death
ages. `steps(plan)` runs until the youngest person reaches `MAX_AGE`, so a run
gets about 25% longer. The deterministic views pass fixed deaths and run on the
same horizon, so every `Projection` has the same shape. Charts and tables cut
it at `end_age`.

### Alive

A person is alive in year t when their age that year is below their death
age. A death age is the age reached on the January 1 after the last year lived:
the last year is a full year of income, tax and spending, and the estate is
valued on that January 1. "Lives to `end_age`" therefore means exactly what it
means today, where the projection stops on the January 1 the youngest turns
`end_age`.

### At the first death (the next January 1, in that future only)

- **Spousal rollover, untaxed:**
  - RRSP/RRIF goes to the survivor's RRSP/RRIF;
  - LIRA/LIF goes to the survivor's locked-in account;
  - TFSA goes to the survivor's TFSA as successor holder, needing no room;
  - non-registered money goes to the survivor at its cost base.
- The deceased's unused TFSA room is lost.
- The deceased's CPP, OAS, salary and contributions stop.
- **CPP survivor benefit**, from the official rules: a share of the deceased's
  CPP (their actual pension if it had started, otherwise their estimated pension
  at 65), with the under-65 formula when the survivor is under 65. It is capped
  so the survivor's own CPP plus the survivor benefit stays within the official
  maximum. It is the survivor's ordinary income.
- **The death benefit** is the estate's income in the year of death. It is
  counted as cash available to the household and taxed as the deceased's
  ordinary income that year.
- **Pension splitting is forced to share 0** in that future, so the search never
  moves income to a dead spouse.
- **Single-filer tax:** the survivor goes through the existing
  `household_tax`, with the dead person's tax parts at zero.

### Spending

- A couple with one person alive spends `need × survivor_share`.
- Care costs stay at the full amount.
- With both dead the need is 0, so nothing can be short.
- Life stages, downsizing and the bad-market trigger still key on people[0]'s
  age, even if people[0] has died. This is documented.
- "Working" and "retired" both require being alive.

### At the second death

The estate is valued in the year the last person dies, in that future: all
investments plus the home, less tax at death (top rate on registered money,
the inclusion rate on gains, as today). **Legacy and lifetime tax are per
future at that point.** For the deterministic views with a survivor to
`end_age`, that is the `end_age` year, matching today.

### `Projection`

It gains:

- `death_ages` (P, N);
- `alive` (T, P, N);
- `success`: no short year while anyone is alive.

Balances after the second death are NaN, so no average includes estate money
that no longer exists. Properties that reduce over balances
(`investments_at_retirement`, `bad_luck_index`'s money left) use the value at
each path's last alive year.

## Part 3 — Views, tools, report, tests

### Deterministic futures

- **Couple:** the person with the earlier `median_death_age` dies at that age,
  and the survivor lives to `end_age`. Ties go to the older person, then
  people[1].
- **Single:** dies at `end_age`.
- **Bad luck:** uses the same deaths as the average future. The path is still
  picked from the simulated futures by `bad_luck_index` and replayed alone with
  its returns.

`engine.run` builds these with `average_deaths(plan)`.

### Planning tools

- `scenarios.evaluate`, `optimize.affordability` and the GUI previews draw
  deaths once, with the same seed, and pass them to every candidate.
- `optimize.best_benefit_ages` scores a candidate by **mean legacy over
  `LIFESPAN_DRAWS = 300` death draws at a steady median return**. A candidate is
  timed first. If it costs more than about 0.5 s, the grid becomes 2-year steps
  plus a one-year refinement around the best pair, to keep about 10 s total. The
  success comparison at today's vs the best ages stays.

### Report, GUI, CLI

- **Headline:** "Chance the money lasts as long as either of you lives" ("as
  long as you live" for one person). The CLI prints the same wording.
- **New "Lifespans" tile:**
  - each person's median death age;
  - the chance at least one person reaches 95;
  - the median years the survivor lives alone.
- **Money-left chart:** the band covers only futures where someone is alive,
  drawn to `end_age`, with a dashed marker for the average future's first death.
- **Year-by-year table:** a † after a deceased person's age.
- **Assumptions:** sex per person, survivor share, and the life table and
  improvement rule with their sources. The rules table lists the new CPP rules.
- **GUI:**
  - a sex selector per person and a survivor-share slider;
  - optimizer text updated, with the "assumes everyone lives to N" note replaced
    by the number of death draws used.
- **`summary.json`:** same keys, with `success` under the new definition, plus
  `lifespans` (median death ages).

### Tests (offline, invented households)

- **Mortality:**
  - improvement math checked by hand at one age;
  - draws are seeded, conditional on the current age, and capped at 110;
  - the sample median matches `median_death_age` within one year.
- **Regression anchor:** with each person's death age set to their age on the
  January 1 that today's model stops (both die in the same year, so the survivor
  share is unused), the new engine reproduces today's numbers exactly over that
  span: income, tax, balances, legacy. The mask changes nothing until someone
  dies.
- **First death:**
  - total invested is conserved across the rollover January;
  - the split share is 0 afterwards;
  - one OAS;
  - spending × survivor share;
  - the CPP survivor cap holds;
  - the death benefit lands in the year of death.
- **Success:** no shortfall after the second death; paths are scored only while
  someone is alive.
- **Optimizer:** a small grid with `workers=1`, plus pool equivalence.
- **Report / GUI / CLI:** labels, the Lifespans tile, the †, and `sex` /
  `survivor_share` round-trip through `plan.json`.

### Docs

- The retirement `CLAUDE.md`: the module map (`mortality.py`) and the new
  conventions.
- The retirement `README.md`: items 2 and 3 move from "Future improvements" into
  the feature list.
- `.claude/skills/retirement-planning/SKILL.md`.
