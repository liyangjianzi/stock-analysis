"""The tax-refund check: the model's expected refund vs refunds found in bank CSVs.
Invented numbers only."""
from __future__ import annotations

import pytest

from stockanalysis.retirement import refund, tax
from stockanalysis.retirement.inputs import (Person, PlanInputs, Returns, Spending, Withdrawal)


def plan_with(**kw) -> PlanInputs:
    p = Person(id="A", name="A", age=40, retire_age=50, salary=100_000.0,
               contributions={"rrsp": 10_000.0, "pension": 5_500.0}, pension_match=1.75, **kw)
    return PlanInputs(province="AB", start_year=2026, end_age=60, people=(p,),
                      spending=Spending(base=50_000.0), home=None, returns=Returns(),
                      withdrawal=Withdrawal())


def test_expected_refund_is_the_tax_on_the_rrsp_contribution():
    rows = refund.expected_refund(plan_with())
    own = 5_500.0 / 2.75
    withheld = float(tax.income_tax(ordinary=100_000.0 - own, age=40))
    owed = float(tax.income_tax(ordinary=100_000.0 - own - 10_000.0, age=40))
    assert rows == [("A", pytest.approx(withheld - owed))]


def test_actual_refunds_reads_only_refund_deposits_in_the_last_12_months(tmp_path):
    (tmp_path / "a.csv").write_text(
        '"2025-03-20","TAX REFUND       RIT","","1000.00","5000"\n'      # older than 12 months
        '"2026-01-05","EMPLOYER   PAY","","2500.00","7500"\n'
        '"2026-03-23","TAX REFUND       RIT","","1234.56","8734.56"\n'
        '"2026-03-23","TAX REFUND       RIT","","765.44","9500"\n'
        '"2026-04-01","CITY  TAX","300","","9200"\n')
    total, rows = refund.actual_refunds(tmp_path)
    assert total == pytest.approx(2_000.0)
    assert [r[0] for r in rows] == ["2026-03-23", "2026-03-23"]


def test_no_bank_files_means_no_refunds(tmp_path):
    assert refund.actual_refunds(tmp_path) == (None, [])
    assert refund.actual_refunds(tmp_path / "missing") == (None, [])


def test_expected_refund_uses_the_capped_rrsp_contribution():
    rows = refund.expected_refund(plan_with(rrsp_room=4_000.0))
    own = 5_500.0 / 2.75
    withheld = float(tax.income_tax(ordinary=100_000.0 - own, age=40))
    owed = float(tax.income_tax(ordinary=100_000.0 - own - 4_000.0, age=40))
    assert rows == [("A", pytest.approx(withheld - owed))]
