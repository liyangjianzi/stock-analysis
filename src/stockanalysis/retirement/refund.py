"""The tax-refund check: what the model expects back each spring vs what the bank shows.

Payroll withholds tax as if there were no RRSP contribution; the return gives that
back. The engine already charges the *true* tax, so the refund is counted there;
this module only checks it. ``expected_refund`` is pure; ``actual_refunds`` reads
bank CSVs (private files under the gitignored ``retirement/bank/``).
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import numpy as np

from . import engine, tax
from .inputs import PlanInputs

REFUND_MARK = "TAX REFUND"      # how the bank labels a CRA refund deposit


def expected_refund(plan: PlanInputs) -> list:
    """(name, refund) for each person working in the first year with a salary: tax
    withheld without the RRSP and child care deductions (own pension deducted at
    source, the ESPP discount included) minus the tax truly owed. The RRSP part is
    capped by ``rrsp_room`` when it is set."""
    working = np.array([[p.age < p.retire_age] for p in plan.people])
    alive = np.ones_like(working)
    contrib = engine.planned_contributions(plan.people, working)
    kids = [k.age for k in plan.education.kids] if plan.education is not None else []
    care = engine.childcare_claims(plan.people, plan.education, kids, working, alive)
    rows = []
    for i, p in enumerate(plan.people):
        if not working[i, 0] or p.salary is None:
            continue
        paid, value = engine.espp_purchase(p)
        base = p.salary + (value - paid) - float(engine.own_pension(p, contrib["pension"][i, 0]))
        withheld = float(tax.income_tax(ordinary=base, age=p.age, province=plan.province))
        rrsp = min(contrib["rrsp"][i, 0], np.inf if p.rrsp_room is None else p.rrsp_room)
        owed = float(tax.income_tax(ordinary=max(base - rrsp - care[i, 0], 0.0), age=p.age,
                                    province=plan.province))
        rows.append((p.name, withheld - owed))
    return rows


def _rows(path: Path):
    files = sorted(path.glob("*.csv")) if path.is_dir() else [path] if path.is_file() else []
    for f in files:
        with f.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                if len(row) < 4:
                    continue
                try:
                    yield dt.date.fromisoformat(row[0].strip()), row[1], row[3]
                except ValueError:
                    continue                          # a header or a malformed line


def actual_refunds(path) -> tuple:
    """(total, [(date, amount)]) of refund deposits in the 12 months up to the latest
    transaction in the bank CSVs at ``path`` (a file or a folder); (None, []) if none."""
    rows = list(_rows(Path(path)))
    if not rows:
        return None, []
    latest = max(d for d, _, _ in rows)
    start = latest - dt.timedelta(days=365)
    found = []
    for d, desc, credit in rows:
        if REFUND_MARK in desc.upper() and d > start and credit.strip():
            found.append((d.isoformat(), float(credit)))
    if not found:
        return None, []
    return sum(a for _, a in found), sorted(found)
