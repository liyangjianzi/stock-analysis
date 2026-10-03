"""Year-by-year Canadian retirement projection over many return paths.

State is kept per person and per path, in today's dollars:
- ``rrsp``: an RRSP, which becomes a RRIF in the year the person reaches
  ``rrif_start_age``;
- ``pension``: locked-in money, a LIRA and then an Alberta LIF from
  ``lif_start_age``;
- ``tfsa``;
- ``nonreg``, with its cost base.

Each step is one calendar year:
1. January 1: TFSA room (the annual limit plus last year's withdrawals); the LIF
   start, with Alberta's one-time unlocking moved to the RRSP; downsizing.
   The RESP (``plan.education``): January's contribution and grant, then the
   year's school costs, less any Canada Student Grant (tested on last year's
   family income), paid from it first. After the last child's school its
   contributions come back, unused grants are repaid, and growth goes to the
   RRSP (up to the limit) or is taxed with the extra 20%.
2. The household's after-tax spending need: base, dated changes, stage share,
   the bad-market cut and care costs, plus RESP contributions and any school
   cost the RESP can't cover. While anyone still works, earned income covers the
   need and the contributions, and the uncovered school cost comes from savings.
3. Guaranteed income: CPP, OAS, and the RRIF / LIF minimums.
4. Top-up withdrawals by strategy, iterated with the couple's tax (the best
   pension-splitting share included) until the two agree within TOL dollars.
   A retired person's non-registered payouts (``plan.nonreg_income``) are part of
   that taxable income.
5. Payouts are reinvested (raising the cost base); a worker's payouts are taxed
   on top of their salary and that tax is sold out of the account. Surplus saved
   (TFSA room, then non-registered); shortfall recorded.
6. Growth, then the year's contributions from whoever still works.
7. Lifespans (``deaths``): a dead person earns, draws and pays nothing; each January
   their accounts roll to the living partner untaxed (plus the CPP death benefit
   once); the survivor gets the CPP survivor's pension, spends ``survivor_share`` of
   the budget, and files alone (no splitting). The estate is valued the January
   after the last death.

Pure: no I/O. Every statutory number comes from :mod:`rules`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import education, mortality, rules, tax
from .inputs import PlanInputs


# -- returns ----------------------------------------------------------------------

def lognormal_params(mean: float, sd: float) -> tuple[float, float]:
    """(mu, s) of a lognormal gross return with arithmetic ``mean`` and ``sd``."""
    s = float(np.sqrt(np.log(1 + sd ** 2 / (1 + mean) ** 2)))
    return float(np.log(1 + mean) - s ** 2 / 2), s


def draw_returns(mean: float, sd: float, paths: int, years: int, seed: int) -> np.ndarray:
    """(years, paths) real yearly returns, seeded."""
    mu, s = lognormal_params(mean, sd)
    rng = np.random.default_rng(seed)
    return (np.exp(rng.normal(mu, s, (paths, years))) - 1).T


def median_return(mean: float, sd: float) -> float:
    """The typical (median, geometric) yearly return: what a steady 'average future' earns."""
    return float(np.exp(lognormal_params(mean, sd)[0]) - 1)


# -- government benefits ----------------------------------------------------------

def cpp_at_65(person) -> float:
    """Yearly CPP at 65 in today's dollars. Uses the My Service Canada statement
    figure when given; otherwise estimates from contributory years (with the
    general dropout) times an earnings ratio."""
    if person.cpp_at_65 is not None:
        return float(person.cpp_at_65)
    d = rules.CPP["dropout"].value
    counted = d["contributory_years"] - min(d["max_years"], d["share"] * d["contributory_years"])
    years = person.cpp_years + max(0, person.retire_age - person.age)
    return (12 * rules.CPP["max_monthly_at_65"].value * min(1.0, years / counted)
            * person.cpp_earnings_ratio)


def cpp_factor(start_age: int) -> float:
    """Early (-0.6%/month) or late (+0.7%/month) start adjustment."""
    a = rules.CPP["adjustment"].value
    months = (start_age - 65) * 12
    return 1 + (a["late_per_month"] if months > 0 else a["early_per_month"]) * months


def oas_yearly(person, age: int) -> float:
    """OAS paid in the year the person reaches ``age``: 0 before the start age,
    years/40 residence share (none under 10 years), +0.6% per month deferred,
    and the higher 75+ rate."""
    if age < person.oas_start_age:
        return 0.0
    residence = rules.OAS["residence"].value
    years = person.years_in_canada_at_65
    share = 0.0 if years < residence["min_years"] else min(1.0, years / residence["full_years"])
    monthly = rules.OAS["monthly"].value["75+" if age >= 75 else "65-74"]
    deferral = rules.OAS["deferral"].value
    months = (person.oas_start_age - deferral["min_age"]) * 12
    return 12 * monthly * share * (1 + deferral["per_month"] * months)


SOURCES = ("earned", "cpp", "oas", "minimums", "registered", "tfsa", "nonreg", "shortfall")
ACCOUNTS = ("rrsp", "pension", "tfsa", "nonreg")
MAX_ITER = 40         # tax <-> withdrawal fixed-point rounds per split share
SPLIT_ROUNDS = 3      # re-optimise the pension split at most this often per year
TOL = 1.0             # dollars
SHORTFALL_EPS = 1.0   # a gap under a dollar is rounding, not a short year


def steps(plan: PlanInputs) -> int:
    """The "plan to" horizon: from now until the youngest person reaches ``end_age``.
    The average and bad-luck futures and the charts use it. Household events still
    key on people[0]'s age."""
    return plan.end_age - min(p.age for p in plan.people)


def life_steps(plan: PlanInputs) -> int:
    """The lifespan horizon: until the youngest could reach mortality.OMEGA."""
    return mortality.OMEGA - min(p.age for p in plan.people)


def fixed_deaths(plan: PlanInputs, T: int, N: int) -> np.ndarray:
    """(P, N) death ages under which everyone lives through all T years."""
    return np.repeat(np.array([[p.age + T] for p in plan.people], dtype=int), N, axis=1)


def stage_share(spending, age: int) -> float:
    """Go-go (1.0), slow-go or no-go share of the budget at the first person's ``age``."""
    if age >= spending.no_go_age:
        return spending.no_go_share
    if age >= spending.slow_go_age:
        return spending.slow_go_share
    return 1.0


@dataclass
class Projection:
    years: list
    ages: list
    income: dict
    tax: np.ndarray
    need: np.ndarray
    saved: np.ndarray
    tfsa_room: np.ndarray
    investments: np.ndarray
    balances: dict
    home_value: np.ndarray
    legacy: np.ndarray
    death_tax: np.ndarray
    retire_step: int
    max_residual: float
    education: np.ndarray | None = None   # (T, N) household money for school: contributions + uncovered costs
    student_grant: np.ndarray | None = None  # (T, N) Canada Student Grants received
    resp: np.ndarray | None = None        # (T + 1, N) RESP balance on January 1, after that year's flows
    school: object | None = None          # the education.Schedule behind them
    death_ages: np.ndarray | None = None        # (P, N) death ages (see mortality)
    alive: np.ndarray | None = None             # (T, P, N) alive in each year
    end_step: np.ndarray | None = None          # (N,) years the household lives; the estate's January 1
    final_investments: np.ndarray | None = None  # (N,) investments on that January 1, before tax at death

    @property
    def paths(self) -> int:
        return self.tax.shape[1]

    @property
    def shortfall_years(self) -> np.ndarray:
        return (self.income["shortfall"] > SHORTFALL_EPS).sum(axis=0)

    @property
    def success(self) -> float:
        return float((self.shortfall_years == 0).mean())

    @property
    def first_shortfall_step(self) -> np.ndarray:
        short = self.income["shortfall"] > SHORTFALL_EPS
        return np.where(short.any(axis=0), short.argmax(axis=0), len(self.years))

    @property
    def lifetime_tax(self) -> np.ndarray:
        return self.tax.sum(axis=0) + self.death_tax

    @property
    def investments_at_retirement(self) -> np.ndarray:
        return self.investments[min(self.retire_step, len(self.years))]


def allocate(withdrawal, avail: dict, required: np.ndarray, base_taxable: np.ndarray):
    """Split the cash still needed across accounts.

    ``avail`` maps rrsp / lif / nonreg / tfsa to (P, N) accessible balances.
    ``required`` is (N,); a negative value is a surplus. Returns
    ``(draw, unfunded, surplus)``, where draw values are (P, N). Within each
    account type the draw is pro-rata to the people's balances.
    """
    draw = {k: np.zeros_like(v) for k, v in avail.items()}
    left = {k: v.copy() for k, v in avail.items()}
    need = np.maximum(required, 0.0)
    surplus = np.maximum(-required, 0.0)

    def take(kinds):
        nonlocal need
        for k in kinds:
            total = left[k].sum(axis=0)
            amount = np.minimum(need, total)
            share = np.divide(left[k], total, out=np.zeros_like(left[k]), where=total > 0)
            draw[k] += share * amount
            left[k] -= share * amount
            need = need - amount

    if withdrawal.strategy == "steady_income":
        room = np.maximum(withdrawal.steady_income_target - base_taxable, 0.0)
        for k in ("rrsp", "lif"):
            forced = np.minimum(left[k], room)
            draw[k] += forced
            left[k] -= forced
            room -= forced
        forced_total = draw["rrsp"].sum(axis=0) + draw["lif"].sum(axis=0)
        covered = np.minimum(need, forced_total)
        surplus = surplus + forced_total - covered
        need = need - covered
        take(("nonreg", "tfsa", "rrsp", "lif"))
    elif withdrawal.strategy == "proportional":
        total = sum(left[k].sum(axis=0) for k in left)
        amount = np.minimum(need, total)
        for k in left:
            draw[k] += np.divide(left[k], total, out=np.zeros_like(left[k]), where=total > 0) * amount
        need = need - amount
    else:
        take(("rrsp", "lif", "nonreg", "tfsa"))
    return draw, need, surplus


def _parts(people, ages, is_rrif, cpp, oas, min_rrif, min_lif, draw, gain_ratio, payouts) -> list:
    """Each person's taxable pieces. RRIF/LIF payments are eligible pension income at
    65+; plain RRSP withdrawals and anything before 65 are ordinary income.
    ``payouts`` is (eligible dividends, foreign dividends + interest), each (P, N)."""
    eligible, other = payouts
    pension_age = rules.PENSION_SPLIT.value["min_age"]
    parts = []
    for i in range(len(people)):
        reg = min_rrif[i] + draw["rrsp"][i]
        lif = min_lif[i] + draw["lif"][i]
        cpp_i = np.zeros_like(reg) + cpp[i]
        if ages[i] >= pension_age:
            pension = (reg if is_rrif[i] else np.zeros_like(reg)) + lif
            ordinary = cpp_i + (np.zeros_like(reg) if is_rrif[i] else reg)
        else:
            pension = np.zeros_like(reg)
            ordinary = cpp_i + reg + lif
        parts.append({"ordinary": ordinary + other[i], "pension": pension, "dividends": eligible[i],
                      "gains": draw["nonreg"][i] * gain_ratio[i],
                      "oas": np.zeros_like(reg) + oas[i], "age": ages[i]})
    return parts


def _pro_rata(amount, part, whole):
    """``amount * part / whole``, 0 where ``whole`` is 0 (e.g. the cost base of a sale)."""
    product = amount * part
    return np.divide(product, whole, out=np.zeros(np.shape(product)), where=whole > 0)


def _resp_year(school, t, cost_t, resp_bal, resp_in, resp_grant) -> tuple:
    """One January in the RESP, in place. ``cost_t`` is the year's school cost after
    student grants. Returns (cost it can't cover, leftover), where leftover is None
    or, after the last school year, (contributions back, growth) as (N,) arrays;
    unused grants are repaid. Withdrawals take growth and grants first (paid to the
    student, taxed in their hands: about nothing), then contributions."""
    resp_bal += school.contribution[t] + school.grant[t]
    resp_in += school.contribution[t]
    resp_grant += school.grant[t]
    paid = np.minimum(cost_t, resp_bal)
    earnings = np.maximum(resp_bal - resp_in, 0.0)
    from_earnings = np.minimum(paid, earnings)
    resp_grant -= _pro_rata(resp_grant, from_earnings, earnings)
    resp_in -= paid - from_earnings
    resp_bal -= paid
    leftover = None
    if t == school.end_step:
        back = np.clip(resp_in, 0.0, resp_bal)                       # contributions: tax-free
        repaid = np.clip(resp_grant, 0.0, resp_bal - back)           # unused grants go back
        leftover = (back, resp_bal - back - repaid)
        resp_bal[:], resp_in[:], resp_grant[:] = 0.0, 0.0, 0.0
    return cost_t - paid, leftover


def roll_over(bal, cost, room, restore, lif_gain, alive) -> np.ndarray:
    """Spousal rollover, untaxed, in place: a dead person's accounts, cost base and
    last LIF return move to the living partner, and their TFSA room is lost. Runs
    every January, so money paid to a dead person later is swept too. Returns
    (P, N) bool: who was rolled into a partner this January."""
    moved = np.zeros(alive.shape, dtype=bool)
    for i, j in ((0, 1), (1, 0)):
        go = ~alive[i] & alive[j]
        if not go.any():
            continue
        for k in ACCOUNTS:
            bal[k][j] += np.where(go, bal[k][i], 0.0)
            bal[k][i] = np.where(go, 0.0, bal[k][i])
        for arr in (cost, lif_gain):
            arr[j] += np.where(go, arr[i], 0.0)
            arr[i] = np.where(go, 0.0, arr[i])
        room[i] = np.where(go, 0.0, room[i])
        restore[i] = np.where(go, 0.0, restore[i])
        moved[i] |= go
    return moved


def survivor_cpp(people, ages, alive, own) -> np.ndarray:
    """(P, N) yearly CPP survivor's pension for a living person whose partner has
    died: 60% of the partner's CPP at 65 from 65, or the flat rate plus 37.5% before,
    capped so it plus their own CPP stays within the combined maximum."""
    s = rules.CPP["survivor"].value
    out = np.zeros(alive.shape)
    for i, j in ((0, 1), (1, 0)):                       # j survives i
        base = cpp_at_65(people[i])
        if base <= 0:                                   # never paid in: no survivor's pension
            continue
        amount = (s["share_65"] * base if ages[j] >= 65
                  else 12 * s["flat_monthly"] + s["share_under_65"] * base)
        out[j] = np.where(~alive[i] & alive[j],
                          np.clip(12 * s["combined_max_monthly"] - own[j], 0.0, amount), 0.0)
    return out


def simulate(plan: PlanInputs, returns: np.ndarray, deaths: np.ndarray | None = None) -> Projection:
    """Project ``plan`` over ``returns``: (T, N) real yearly returns. ``deaths`` is a
    (P, N) array of death ages (see mortality); None lets everyone live all T years."""
    people, prov, spend, home = plan.people, plan.province, plan.spending, plan.home
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2 or returns.shape[0] < 1:
        raise ValueError(f"returns must have shape (years, paths); got {returns.shape}")
    T, N, P = returns.shape[0], returns.shape[1], len(people)
    deaths = fixed_deaths(plan, T, N) if deaths is None else np.asarray(deaths, dtype=int)
    if deaths.shape != (P, N):
        raise ValueError(f"deaths must have shape ({P}, {N}); got {deaths.shape}")
    age0 = np.array([[p.age] for p in people])
    end_step = np.clip((deaths - age0).max(axis=0), 0, T)     # years someone is alive
    owner = {p.id: i for i, p in enumerate(people)}

    bal = {k: np.zeros((P, N)) for k in ACCOUNTS}
    cost = np.zeros((P, N))
    for acct in plan.accounts:
        i = owner[acct.owner]
        bal[acct.type][i] += acct.balance
        if acct.type == "nonreg":
            cost[i] += acct.balance if acct.cost is None else acct.cost
    room = np.repeat(np.array([[p.tfsa_room] for p in people], dtype=float), N, axis=1)
    restore = np.zeros((P, N))
    lif_age = [p.lif_start_age if p.lif_start_age is not None
               else max(rules.LIF[prov]["min_age"].value, p.retire_age) for p in people]
    # A LIF already running at the start (retired, past its start age) was set up in
    # an earlier year: its minimum applies now and the one-time unlock is long done.
    lif_step: list = [-1 if p.retire_age <= p.age and p.age > a else None
                      for p, a in zip(people, lif_age)]
    lif_gain = np.zeros((P, N))               # last year's LIF return (Alberta's "A")
    cpp_year = [cpp_at_65(p) * cpp_factor(p.cpp_start_age) for p in people]
    limit = rules.TFSA["annual_limit"].value
    split_age = rules.PENSION_SPLIT.value["min_age"]
    payout = plan.nonreg_income
    y_eligible, y_other = payout.eligible_dividends, payout.foreign_dividends + payout.interest
    taxable_yield = float(tax.total_income(ordinary=y_other, dividends=y_eligible))   # per $ held

    income = {s: np.zeros((T, N)) for s in SOURCES}
    tax_paid, need_rec, saved_rec, room_rec = (np.zeros((T, N)) for _ in range(4))
    invest = np.zeros((T + 1, N))
    gains_rec = np.zeros((T + 1, N))            # unrealized non-reg gains each January 1
    alive_rec = np.zeros((T, P, N), dtype=bool)
    balances = {k: np.zeros((T + 1, N)) for k in ACCOUNTS}
    home_value = np.zeros(T + 1)
    retire_ref, retire_step, max_resid, downsized = None, T, 0.0, False
    school = education.schedule(plan, T)
    edu_out = resp_rec = csg_rec = None
    # Last year's family income (line 15000), for the student grant. A missing salary
    # counts as too high here (np.inf) but as none for tax. Before the plan starts it
    # is known only from workers' salaries; a retired household's is unknown.
    salaries = [p.salary if p.salary is not None else np.inf for p in people]
    workers = [s for s, p in zip(salaries, people) if p.age < p.retire_age]
    last_income = np.full(N, sum(workers) if workers else np.inf)
    if school is not None:
        edu_out, resp_rec, csg_rec = np.zeros((T, N)), np.zeros((T + 1, N)), np.zeros((T, N))
        family = P + len(plan.education.kids)
        resp_bal = np.full(N, school.balance)
        resp_in = np.full(N, min(school.contributed, school.balance))       # contributions left
        resp_grant = np.full(N, min(school.grants, school.balance - resp_in[0]))
    aip = rules.RESP["aip"].value
    prev_tax, prev_share = np.zeros(N), np.zeros(N)
    benefit_paid = np.zeros((P, N), dtype=bool)
    death_benefit = rules.CPP["death_benefit"].value

    for t in range(T):
        year = plan.start_year + t
        ages = [p.age + t for p in people]
        alive = (age0 + t) < deaths                                      # (P, N)
        alive_rec[t] = alive
        planned = [ages[i] < p.retire_age for i, p in enumerate(people)]  # the plan's calendar
        working = np.array(planned)[:, None] & alive                     # (P, N) actually earning
        anyone_working = any(planned)
        earning = working.any(axis=0)                                    # (N,)
        household = alive.any(axis=0)                                    # (N,)

        # 1. January 1: TFSA room, LIF start (+ unlocking), downsizing.
        room += limit + restore
        restore[:] = 0.0
        if P == 2:
            moved = roll_over(bal, cost, room, restore, lif_gain, alive)
            first = moved & ~benefit_paid
            benefit_paid |= moved
            for i, j in ((0, 1), (1, 0)):              # the CPP death benefit, once, untaxed
                if cpp_at_65(people[i]) > 0:
                    paid = np.where(first[i], death_benefit, 0.0)
                    bal["nonreg"][j] += paid
                    cost[j] += paid
        rrsp_jan1 = bal["rrsp"].copy()   # before any LIF unlock: that money wasn't here on Jan 1
        for i, p in enumerate(people):
            if lif_step[i] is None and not planned[i] and ages[i] >= lif_age[i]:
                unlock = p.unlock_share * bal["pension"][i]
                bal["pension"][i] -= unlock
                bal["rrsp"][i] += unlock
                lif_step[i] = t
        if home is not None and home.downsize_age is not None and ages[0] == home.downsize_age:
            released = home.value * (1 - home.selling_cost) - home.new_value - home.moving_cost
            split = np.where(household, alive / np.maximum(alive.sum(axis=0), 1), 1.0 / P)
            bal["nonreg"] += released * split
            cost += released * split
            downsized = True
        if home is not None:
            home_value[t] = home.new_value if downsized else home.value
        uncovered, resp_tax, aip_income = np.zeros(N), np.zeros(N), np.zeros(N)
        if school is not None:
            cost_t = np.full(N, school.cost[t])
            if plan.education.student_grant and school.students[t]:
                csg_rec[t] = np.minimum(school.students[t] * education.student_grant(last_income, family),
                                        cost_t)
                cost_t = cost_t - csg_rec[t]
            uncovered, leftover = _resp_year(school, t, cost_t, resp_bal, resp_in, resp_grant)
            if leftover is not None:
                # Leftover growth goes to people[0]'s RRSP up to the limit; the rest is
                # their income (plus the flat extra tax): on top of salary while they
                # work, otherwise in the household tax below.
                back, growth = leftover
                to_rrsp = (np.minimum(growth, aip["rrsp_transfer_max"])
                           if plan.education.aip_to_rrsp else 0.0)
                taxable = growth - to_rrsp
                resp_tax = aip["extra_tax"] * taxable
                if planned[0]:
                    resp_tax = resp_tax + tax.extra_tax(people[0].salary or 0.0, ordinary=taxable,
                                                        age=ages[0], province=prov)
                else:
                    aip_income = taxable
                resp_tax = np.minimum(resp_tax, taxable)
                bal["rrsp"][0] += to_rrsp
                bal["nonreg"][0] += back + taxable - resp_tax
                cost[0] += back + taxable - resp_tax
            resp_rec[t] = resp_bal
            edu_out[t] = school.contribution[t] + uncovered
        room_rec[t] = room.sum(axis=0)
        for k in ACCOUNTS:
            balances[k][t] = bal[k].sum(axis=0)
        invest[t] = sum(balances[k][t] for k in ACCOUNTS)
        gains_rec[t] = np.maximum(bal["nonreg"] - cost, 0.0).sum(axis=0)
        if not anyone_working and retire_ref is None:
            retire_ref, retire_step = invest[t].copy(), t
        pension_jan1 = bal["pension"].copy()

        # 2. The household's after-tax spending need.
        level = spend.base + sum(c.amount for c in spend.changes if c.year <= year)
        if downsized:
            level -= (home.property_tax + home.insurance) * (1 - home.new_value / home.value)
        need = np.full(N, max(level, 0.0) * stage_share(spend, ages[0]))
        if not anyone_working:
            need = np.where(invest[t] < spend.bad_market_trigger * retire_ref,
                            need * (1 - spend.bad_market_cut), need)
        if P == 2:
            need = np.where(alive.sum(axis=0) == 1, need * spend.survivor_share, need)
        if ages[0] >= spend.no_go_age:
            need = need + spend.care
        if school is not None:
            need = need + school.contribution[t]       # RESP contributions are paid like spending
        # School costs the RESP can't cover come from savings even while anyone works.
        need = need * household
        uncovered = uncovered * household
        need_rec[t] = need + uncovered
        income["earned"][t] = np.where(earning, need, 0.0)
        cash_need = np.where(earning, uncovered, need + uncovered)

        # 3. Guaranteed income: CPP, OAS, RRIF and LIF minimums.
        cpp = np.array([[cpp_year[i] if ages[i] >= p.cpp_start_age else 0.0]
                        for i, p in enumerate(people)]) * alive          # (P, N)
        oas = np.array([[oas_yearly(p, ages[i])] for i, p in enumerate(people)]) * alive
        if P == 2:
            cpp = cpp + survivor_cpp(people, ages, alive, cpp)
        is_rrif = [ages[i] >= p.rrif_start_age for i, p in enumerate(people)]
        min_rrif, min_lif, lif_room = (np.zeros((P, N)) for _ in range(3))
        for i, p in enumerate(people):
            if ages[i] > p.rrif_start_age:        # the minimum starts the year after conversion
                min_rrif[i] = rules.rrif_min_factor(ages[i] - 1) * rrsp_jan1[i]
            if lif_step[i] is not None:
                if t > lif_step[i]:
                    min_lif[i] = rules.rrif_min_factor(ages[i] - 1) * pension_jan1[i]
                cap = np.maximum(lif_gain[i], rules.lif_max_pct(prov, ages[i] - 1) * pension_jan1[i])
                lif_room[i] = np.maximum(cap - min_lif[i], 0.0)
        min_rrif, min_lif, lif_room = min_rrif * alive, min_lif * alive, lif_room * alive
        min_rrif = np.minimum(min_rrif, bal["rrsp"])
        min_lif = np.minimum(min_lif, bal["pension"])
        bal["rrsp"] -= min_rrif
        bal["pension"] -= min_lif
        lif_room = np.minimum(lif_room, bal["pension"])
        guaranteed = (cpp + oas + min_rrif + min_lif).sum(axis=0)
        base_taxable = cpp + oas + min_rrif + min_lif

        # 4. Top-up withdrawals, iterated with the household's tax.
        # Earned income covers a worker: never draw on their RRSP/LIF before they retire.
        retired = (~working & alive).astype(float)
        base_taxable = base_taxable + taxable_yield * bal["nonreg"] * retired
        avail = {"rrsp": bal["rrsp"] * retired, "lif": lif_room * retired,
                 "nonreg": bal["nonreg"], "tfsa": bal["tfsa"]}
        gain_ratio = np.clip(np.divide(bal["nonreg"] - cost, bal["nonreg"], out=np.zeros((P, N)),
                                       where=bal["nonreg"] > 0), 0.0, 1.0)
        both = alive.all(axis=0)
        share, tax_est = np.where(both, prev_share, 0.0), prev_tax.copy()
        for _round in range(SPLIT_ROUNDS):
            for _ in range(MAX_ITER):
                draw, unfunded, surplus = allocate(plan.withdrawal, avail,
                                                   cash_need + tax_est - guaranteed, base_taxable)
                kept = (bal["nonreg"] - draw["nonreg"]) * retired       # a retiree's payouts
                other = y_other * kept
                other[0] = other[0] + aip_income                        # leftover RESP growth
                parts = _parts(people, ages, is_rrif, cpp, oas, min_rrif, min_lif, draw, gain_ratio,
                               (y_eligible * kept, other))
                new_tax = tax.household_tax(parts, share, prov)
                resid = float(np.abs(new_tax - tax_est).max())
                tax_est = new_tax
                if resid < TOL:
                    break
            if P < 2 or max(ages) < split_age:
                break
            best = np.where(both, tax.best_split(parts, prov), 0.0)   # never split to the dead
            if np.array_equal(best, share):
                break
            share = best
        max_resid = max(max_resid, resid)
        prev_share, prev_tax = share, tax_est

        # 5. Apply the draws; save any surplus (TFSA room first).
        bal["rrsp"] -= draw["rrsp"]
        bal["pension"] -= draw["lif"]
        bal["tfsa"] -= draw["tfsa"]
        restore += draw["tfsa"]
        cost -= _pro_rata(cost, draw["nonreg"], bal["nonreg"])
        bal["nonreg"] -= draw["nonreg"]
        payouts = (y_eligible + y_other) * np.maximum(bal["nonreg"], 0.0)
        drag, worker_income = np.zeros(N), np.zeros(N)
        for i, p in enumerate(people):
            w = working[i]
            if not w.any():
                continue
            held = np.maximum(bal["nonreg"][i], 0.0)
            worker_income += np.where(w, salaries[i] + taxable_yield * held, 0.0)
            if not payouts[i].any():
                continue
            due = np.where(w, np.minimum(tax.extra_tax(p.salary or 0.0, ordinary=y_other * held,
                                                       dividends=y_eligible * held, age=ages[i],
                                                       province=prov),
                                         held), 0.0)    # sold out of the account to pay it
            cost[i] -= _pro_rata(cost[i], due, held)
            bal["nonreg"][i] -= due
            drag += due
        cost += payouts                                 # reinvested payouts were taxed already
        last_income = worker_income + sum(
            tax.total_income(ordinary=part["ordinary"], pension=part["pension"], gains=part["gains"],
                             oas=part["oas"], dividends=part["dividends"]) for part in parts)
        per_person = surplus * alive / np.maximum(alive.sum(axis=0), 1)   # to whoever is alive
        into_tfsa = np.minimum(per_person, room)
        room -= into_tfsa
        bal["tfsa"] += into_tfsa
        bal["nonreg"] += per_person - into_tfsa
        cost += per_person - into_tfsa
        for k in ACCOUNTS:
            np.maximum(bal[k], 0.0, out=bal[k])

        income["cpp"][t] = cpp.sum(axis=0)
        income["oas"][t] = oas.sum(axis=0)
        income["minimums"][t] = (min_rrif + min_lif).sum(axis=0)
        income["registered"][t] = (draw["rrsp"] + draw["lif"]).sum(axis=0)
        income["tfsa"][t] = draw["tfsa"].sum(axis=0)
        # The drag and any leftover-RESP tax are paid out of investments too.
        income["nonreg"][t] = draw["nonreg"].sum(axis=0) + drag + resp_tax
        income["shortfall"][t] = unfunded
        tax_paid[t], saved_rec[t] = tax_est + drag + resp_tax, surplus

        # 6. Growth, then contributions from whoever still works.
        r = returns[t]
        started = np.array([s is not None for s in lif_step])[:, None]
        lif_gain = np.where(started, bal["pension"] * r, 0.0)
        for k in ACCOUNTS:
            bal[k] *= 1 + r
        if school is not None:
            resp_bal *= 1 + r
        for i, p in enumerate(people):
            w = working[i]
            if not w.any():
                continue
            others = [working[j] for j in range(P) if j != i]
            partner = np.logical_and.reduce(others) if others else np.ones(N, dtype=bool)
            alt = p.contributions_when_partner_retired

            def amount(kind, w=w, partner=partner, alt=alt, p=p):
                full = p.contributions.get(kind, 0.0)
                when_alone = full if alt is None else alt.get(kind, 0.0)
                return np.where(w, np.where(partner, full, when_alone), 0.0)

            bal["pension"][i] += amount("pension")
            bal["rrsp"][i] += amount("rrsp")
            to_tfsa = np.minimum(amount("tfsa"), room[i])
            room[i] -= to_tfsa
            bal["tfsa"][i] += to_tfsa
            extra = amount("tfsa") - to_tfsa + amount("nonreg")
            bal["nonreg"][i] += extra
            cost[i] += extra

    for k in ACCOUNTS:
        balances[k][T] = bal[k].sum(axis=0)
    invest[T] = sum(balances[k][T] for k in ACCOUNTS)
    gains_rec[T] = np.maximum(bal["nonreg"] - cost, 0.0).sum(axis=0)
    if school is not None:
        resp_rec[T] = resp_bal
    if home is not None:
        home_value[T] = home.new_value if downsized else home.value
    # The estate: the January 1 after the last death, in each future.
    cols = np.arange(N)
    top = rules.top_marginal_rate(prov)
    registered = balances["rrsp"][end_step, cols] + balances["pension"][end_step, cols]
    death_tax = top * registered + top * rules.CAPITAL_GAINS_INCLUSION.value * gains_rec[end_step, cols]
    final = invest[end_step, cols].copy()
    later = np.arange(T + 1)[:, None] > end_step[None, :]       # nobody left: no estate to track
    invest[later] = np.nan
    for k in ACCOUNTS:
        balances[k][later] = np.nan
    return Projection(
        years=[plan.start_year + t for t in range(T)],
        ages=[tuple(p.age + t for p in people) for t in range(T)],
        income=income, tax=tax_paid, need=need_rec, saved=saved_rec, tfsa_room=room_rec,
        investments=invest, balances=balances, home_value=home_value,
        legacy=final + home_value[end_step] - death_tax, death_tax=death_tax,
        retire_step=retire_step, max_residual=max_resid,
        education=edu_out, student_grant=csg_rec, resp=resp_rec, school=school,
        death_ages=deaths, alive=alive_rec, end_step=end_step, final_investments=final)


@dataclass
class PlanResult:
    inputs: PlanInputs
    simulated: Projection
    average: Projection
    bad_luck: Projection
    average_return: float
    bad_luck_path: int


def bad_luck_index(proj: Projection, pct: float = 0.10) -> int:
    """The path at the ``pct`` point, worst first: ranked by the year the money runs
    out (earliest = worst), then by money left at the end."""
    order = np.lexsort((proj.final_investments, proj.first_shortfall_step))
    return int(order[int(np.floor(pct * (len(order) - 1)))])


def average_deaths(plan: PlanInputs) -> np.ndarray:
    """(P, 1) deaths for the average and bad-luck futures. In a couple, the person
    with fewer median years left (ties: the older, then people[1]) dies at their
    median death age and the survivor lives to ``end_age``; one person lives to it."""
    T = steps(plan)
    d = fixed_deaths(plan, T, 1)
    if len(plan.people) == 2:
        left = [(mortality.median_death_age(p, plan.start_year) - p.age, -p.age, -i)
                for i, p in enumerate(plan.people)]
        i = min(range(2), key=lambda k: left[k])
        d[i, 0] = min(d[i, 0], plan.people[i].age + left[i][0])
    return d


def draw_futures(plan: PlanInputs, paths: int, seed: int) -> tuple:
    """(returns over life_steps, death ages): one set of futures every comparison shares."""
    r = plan.returns
    return (draw_returns(r.mean, r.sd, paths, life_steps(plan), seed),
            mortality.draw_death_ages(plan.people, plan.start_year, paths, seed))


def run(plan: PlanInputs, *, paths: int | None = None, seed: int | None = None) -> PlanResult:
    """The plan over ``paths`` simulated futures with drawn lifespans, plus the average
    future (a steady median return) and the bad-luck future (the 10th-percentile path's
    returns, replayed alone), both with ``average_deaths`` over ``steps(plan)``."""
    r = plan.returns
    R, D = draw_futures(plan, r.paths if paths is None else paths, r.seed if seed is None else seed)
    simulated = simulate(plan, R, D)
    T, g, fixed = steps(plan), median_return(r.mean, r.sd), average_deaths(plan)
    average = simulate(plan, np.full((T, 1), g), fixed)
    k = bad_luck_index(simulated)
    return PlanResult(plan, simulated, average, simulate(plan, R[:T, [k]], fixed), g, k)
