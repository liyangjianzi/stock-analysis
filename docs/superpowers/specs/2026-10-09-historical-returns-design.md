# Historical returns and a stock/bond mix: design

Date: 2026-10-09 · Area: `src/stockanalysis/retirement/` · Status: approved design, before the plan

## Goal

Returns are a lognormal draw (`returns.mean`, `returns.sd`) that knows nothing about
inflation. Since 2026-10-09 inflation is drawn as 5-year runs of 1950–2025 Canadian
CPI, centred on the last 40 years' average, but a 1970s run costs real return only
in the years inflation is above that average (`lag_returns`), and the low years
hand it back. In history, high inflation came with *years* of poor real returns
(US stocks lost roughly a third in real terms over 1973–81, bonds more). The
planner can't produce that, nor a multi-year bear market (1929–32, 2000–02) or a
crash beyond the bell curve.

The owner wants:

1. **Returns and inflation drawn together from history**, so bad combinations
   show up in the futures.
2. **A stock/bond mix that changes with age**, so a glide toward bonds lowers both
   the swings and the expected return.
3. **Their own expected returns**, not history's (the US century was the best one).

**Success:** the worst simulated futures contain real historical stretches
(1929–32, 1973–81, 2000–02, 2008), the chance of success and bad-luck legacy reflect
them, and changing the mix changes both the risk and the average.

## Decisions taken

- **History over the earlier rejection.** The 2026-10-07 spec rejected "a joint
  historical replay" without a recorded reason, and historical replay of whole
  retirements was skipped on 2026-10-04. On 2026-10-09 the owner reconsidered after
  the trade-offs (US data for a Canadian plan, `sd` losing its meaning, history as
  the worst case) and chose this design. Block draws avoid the main weakness of a
  whole-retirement replay: there are only ~70 overlapping retirements in history,
  while mixed 5-year blocks give thousands of futures.
- **Data:** US real stock and bond returns, paired with Canadian CPI from the same
  year, over **1928–2025**. The Great Depression and the 1940s (bonds losing to
  inflation for years) are in. No official free Canadian total-return series
  exists; both countries lived through the same 1970s inflation, so the years line
  up.
- **Averages are the owner's.** History supplies the shape (swings, streaks, crashes,
  the link to inflation); each series is shifted to an expected compound real return
  the owner sets. Defaults come from the FP Canada / Institute of Financial Planning
  Projection Assumption Guidelines. Same idea as the inflation centring of
  2026-10-09.
- **The mix is a list of age points on people[0]'s age**, straight lines between,
  flat outside. One point is a fixed mix.
- **The lognormal model stays as a switch** (`returns.model`), unchanged, as the
  regression anchor and a what-if.
- **One mix for the household, blended before the engine.** A mix per account
  (bonds in the RRSP, stocks in the TFSA) was rejected for now: it rewrites
  `simulate`.

## Data (`rules.py`)

Historical series, `Rule(value, year, source)`, refreshed each January with `CPI`
(add them to the refresh list in the package CLAUDE.md and the skill):

- `US_RETURNS`: `{year: (stocks, bonds)}`, nominal, 1928–2025. Stocks = S&P 500
  including dividends, bonds = US 10-year Treasury, from Aswath Damodaran's
  "Historical Returns on Stocks, Bonds and Bills" (NYU Stern, `histretSP`).
- `US_CPI`: `{year: change}`, BLS CPI-U all items, U.S. city average, change of the
  annual average (series CUUR0000SA0), 1928–2025. Used only to turn `US_RETURNS`
  real: `(1 + nominal) / (1 + US_CPI) − 1`.
- `CPI` extended back to 1928, same StatCan table 18-10-0004-01 and method (annual
  average of the monthly index). `historical_inflation()` keeps its 40-year default.
  The 2026-10-09 inflation centring already divides by the whole history's average,
  so the lognormal mode's inflation draws now cover 1928–2025 too.
- `RETURN_ASSUMPTIONS`: FP Canada's nominal equity and fixed-income returns and
  their inflation, read from the official 2026 guidelines document (not from press
  coverage; the press reports equities around 6.3–6.6% and fixed income 3.2%, with
  2.1% inflation). Equities use the guidelines' Canadian equity figure, so the
  default isn't a bet on one foreign market.

Every value is read from its source page, never from memory. Tests check lengths
(1928..`CPI.year`, the same years in all three series) and a few spot values.

## Inputs (`plan.json` → `returns`)

| Field | Meaning | Default |
|---|---|---|
| `model` | `"history"` or `"lognormal"` | `"history"` |
| `stocks` | expected compound real return of stocks | FP Canada equities, made real with FP Canada's inflation |
| `bonds` | expected compound real return of bonds | FP Canada fixed income, made real the same way |
| `mix` | `[[age, stock share], ...]` on people[0]'s age | `[[0, 0.8]]` |
| `mean`, `sd` | the lognormal model, used only when `model` is `"lognormal"` | unchanged |

The defaults go in `inputs.Returns`, read from `rules.RETURN_ASSUMPTIONS` (never
hardcoded). `None` for `stocks`/`bonds` means the default, like `returns.inflation`.

Validation (`PlanError` with the field): `model` is one of the two; `stocks` and
`bonds` between −0.05 and 0.15; `mix` has at least one point, ages strictly
increasing and within 0..`mortality.OMEGA`, shares within 0..1. Existing plan files
load unchanged and switch to the history model by default.

`inputs.stock_share(mix, age)` is the one interpolation (flat before the first
point and after the last); everything else calls it.

## The draw (`engine.py`)

`draw_history(plan, paths, years, seed) -> (stocks, bonds, inflation)`, each
(years, paths):

1. Block starts per future from the inflation stream `[seed, INFLATION_STREAM]`:
   `INFLATION_BLOCK`-year runs of calendar years 1928–2025, wrapping from the last
   year to the first, so every year is drawn equally often. One start index picks
   all three series, so a drawn year is always a whole historical year.
2. Shift each series by a constant factor so the whole history averages its target
   (compound): `(1 + x) · (1 + target) / (1 + geomean(x over 1928–2025)) − 1`.
   Targets: `stocks`, `bonds`, and `inflation_rate` (the last 40 years) for CPI.
   If `inflation_shocks` is off or `inflation` is fixed, inflation is the steady
   rate and returns are still drawn from history.

`portfolio_returns(plan, stocks, bonds) -> (years, paths)`: each year
`w · stocks + (1 − w) · bonds`, `w = stock_share(mix, people[0]'s age that year)`.
That assumes a rebalance every January.

`draw_futures` in history mode returns `(portfolio_returns(...), deaths, inflation)`,
with **no `lag_returns`**: history's real returns already contain what inflation
did, so lagging them again would count it twice. Lognormal mode is exactly today's
code. The death draws don't change in either mode.

`average_returns(plan, T) -> (T, 1)`: the steady return of the average future.
History mode: `w_t · stocks + (1 − w_t) · bonds` per year. Lognormal mode:
`median_return(mean, sd)` repeated (today's value). It replaces the three
`median_return(r.mean, r.sd)` calls in `engine.run`, `scenarios`, and `optimize`.
`PlanResult.average_return` becomes the average of that array over the plan's
years (display only).

The bad-luck future is unchanged: it ranks the drawn paths and replays one.

## What-ifs (`scenarios.py`)

Added, both history mode only:

- "Smooth returns (old model)": `model="lognormal"` with the plan's `mean`/`sd`.
  This shows how much the model itself changes the answer.
- "10 points more bonds": every mix point's share − 0.10, floored at 0.

"Steady inflation (no shocks)" keeps working: it turns off inflation swings while
keeping historical returns.

## GUI (`gui.py`, `static/planner.html`)

Investing tab:

- A model switch: "Historical (recommended)" / "Smooth (old model)".
- History: sliders for expected stock and bond real returns, with the FP Canada
  default shown beside them; an editable table of mix points (age, % stocks) with
  add/remove rows. Slider limits come from `inputs.limits` like the others.
- Smooth: today's mean/sd sliders.

The page posts back the whole original dict, so `mix` round-trips like other keys.
"Save as scenario" skips list edits, so mix changes can't be saved as a scenario;
the model and the averages can.

## Report (`report.py`)

- Assumptions table: the Returns line describes the mix ("80% stocks to age 65,
  gliding to 40% at 80") and the averages with their source. Lognormal mode keeps
  today's line.
- The inflation line says "joint runs of 1928–2025 returns and inflation" in
  history mode.
- Details tab (assumptions table): a row giving the worst 5-year real loss of the
  portfolio in the bad-luck (10th-percentile) future, so the reader can see history
  show up. `PlanResult` gains `bad_luck_returns` (the replayed path's yearly returns)
  for it.

## Docs

The README's Returns and Inflation sections and its `returns` input row; the
package CLAUDE.md (module map entries for `draw_history`, `portfolio_returns`,
`average_returns`, and a convention: history mode never lags returns, block
starts are shared, defaults come from `rules.RETURN_ASSUMPTIONS`); the skill's
input table; the yearly refresh lists.

## Tests (offline, TDD)

- `rules`: three series cover the same years 1928..`CPI.year`; spot values;
  `RETURN_ASSUMPTIONS` converts to the stated real defaults.
- `draw_history`: each block is consecutive whole years (stocks, bonds and
  inflation from the same year), wrapping, seeded, the shifted compound averages
  match the targets over many paths, a 1929–32 or 1973–74 run is reachable,
  steady inflation when shocks are off.
- `stock_share`: interpolation, flat ends, one point; validation errors name
  `returns.mix[i]`.
- `portfolio_returns`: a mix of 1 gives the stock path, 0 the bond path, a glide
  changes with people[0]'s age.
- `average_returns`: lognormal mode equals today's `median_return`; history mode
  is the per-year blend.
- `TEMPLATE` (the `--init` plan) uses the history model, so a new user gets the
  recommended one. Every existing test whose expected values rely on lognormal draws
  pins `model="lognormal"` through its plan helper, so it stays the regression
  anchor; tests that pass explicit return arrays to `simulate` don't change.
- What-ifs present in history mode and absent in lognormal mode; GUI round-trip of
  `mix` and the model switch; report lines for both modes.

Small path counts as usual (40 runs, 20 scenarios).

## Out of scope

- A mix per account or tax-aware asset location.
- Canadian or international return series, currency effects.
- Fees as an input (separate suggestion).
- A whole-retirement historical replay.
