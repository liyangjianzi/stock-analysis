"""Honest error bars for plan-based backtests: month-clustered expectancy, the
first/second-half split, and the edge over a random-entry null.

Pure and offline — trades are duck-typed ``(r_multiple, entry_date)`` stand-ins,
so every number below is computed by hand rather than approximated.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stockanalysis import robustness as rb


def _t(r, date):
    return SimpleNamespace(r_multiple=r, entry_date=pd.Timestamp(date))


# Three months: Jan [1, 3] (mean 2), Feb [-1] (mean -1), Mar [0, 0, 3] (mean 1).
# n=6, mu=1, w=(2,1,3)/6, sum w^2 (m-mu)^2 = 4/36 + 4/36 = 2/9,
# se = sqrt(2/9 * 3/2) = 1/sqrt(3).
TOY = [_t(1.0, "2024-01-03"), _t(3.0, "2024-01-20"),
       _t(-1.0, "2024-02-05"),
       _t(0.0, "2024-03-01"), _t(0.0, "2024-03-02"), _t(3.0, "2024-03-28")]


# --- cluster_expectancy ---------------------------------------------------------

def test_cluster_expectancy_matches_the_hand_computation():
    s = rb.cluster_expectancy(TOY)
    assert s["n"] == 6
    assert s["months"] == 3
    assert s["exp_r"] == pytest.approx(1.0)
    assert s["se"] == pytest.approx(1 / math.sqrt(3))
    assert s["ci_lo"] == pytest.approx(1.0 - 1.96 / math.sqrt(3))
    assert s["ci_hi"] == pytest.approx(1.0 + 1.96 / math.sqrt(3))
    assert s["t"] == pytest.approx(math.sqrt(3))
    assert s["p"] == pytest.approx(math.erfc(math.sqrt(3) / math.sqrt(2)))


def test_clustering_widens_the_error_bar_when_months_move_together():
    """Ten identical wins in one month and ten identical losses in the next are
    two draws, not twenty — the naive per-trade SE badly understates that."""
    trades = ([_t(1.0, "2024-01-10")] * 10 + [_t(-1.0, "2024-02-10")] * 10)
    s = rb.cluster_expectancy(trades)
    r = np.array([t.r_multiple for t in trades])
    naive = r.std(ddof=1) / math.sqrt(r.size)
    assert s["se"] == pytest.approx(1.0)
    assert s["se"] > 4 * naive


def test_a_single_month_has_no_error_bar_rather_than_a_crash():
    s = rb.cluster_expectancy([_t(1.0, "2024-01-02"), _t(-0.5, "2024-01-09")])
    assert s["n"] == 2 and s["months"] == 1
    assert s["exp_r"] == pytest.approx(0.25)
    assert all(np.isnan(s[k]) for k in ("se", "ci_lo", "ci_hi", "t", "p"))


def test_no_trades_is_empty_not_a_crash():
    s = rb.cluster_expectancy([])
    assert s["n"] == 0 and s["months"] == 0
    assert np.isnan(s["exp_r"]) and np.isnan(s["se"])


# --- compare (edge over the null) -----------------------------------------------

def _s(exp_r, se):
    return {"exp_r": exp_r, "se": se}


def test_compare_p_value_at_one_point_nine_six_sigma_is_five_percent():
    e = rb.compare(_s(0.196, 0.06), _s(0.0, 0.08))      # combined se = 0.10
    assert e["exp_r"] == pytest.approx(0.196)
    assert e["se"] == pytest.approx(0.10)
    assert e["p"] == pytest.approx(0.05, abs=1e-3)


@pytest.mark.parametrize("gate, null, verdict", [
    (_s(0.30, 0.03), _s(0.05, 0.03), "beats random entry"),
    (_s(-0.20, 0.03), _s(0.07, 0.03), "worse than random entry"),
    (_s(0.03, 0.035), _s(0.07, 0.03), "not distinguishable from random entry"),
    (_s(0.03, float("nan")), _s(0.07, 0.03), "insufficient data"),
])
def test_compare_verdict_follows_whether_the_ci_excludes_zero(gate, null, verdict):
    assert rb.compare(gate, null)["verdict"] == verdict


def test_compare_without_a_null_is_empty():
    assert rb.compare(_s(0.1, 0.02), None) == {}


# --- yearly / midpoint / evaluate ------------------------------------------------

def test_yearly_expectancy_groups_by_entry_year():
    trades = [_t(1.0, "2022-03-01"), _t(-1.0, "2022-06-01"), _t(2.0, "2023-01-05")]
    y = rb.yearly_expectancy(trades)
    assert y == {2022: {"n": 2, "exp_r": 0.0}, 2023: {"n": 1, "exp_r": 2.0}}


def test_midpoint_is_the_calendar_middle_floored_to_a_day():
    dates = pd.to_datetime(["2020-01-01", "2020-01-05", "2020-01-11"])
    assert rb.midpoint(dates) == pd.Timestamp("2020-01-06")


def test_evaluate_splits_gate_and_null_on_the_same_date():
    gate = [_t(1.0, "2020-02-03"), _t(1.0, "2020-03-03"),
            _t(-1.0, "2022-02-03"), _t(-1.0, "2022-03-03")]
    null = [_t(0.5, "2020-02-10"), _t(0.5, "2022-02-10"), _t(0.5, "2022-05-10")]
    ev = rb.evaluate(gate, null, pd.Timestamp("2021-01-01"))

    assert ev["split_at"] == pd.Timestamp("2021-01-01")
    assert ev["first"]["gate"]["n"] == 2 and ev["first"]["gate"]["exp_r"] == 1.0
    assert ev["second"]["gate"]["n"] == 2 and ev["second"]["gate"]["exp_r"] == -1.0
    assert ev["first"]["null"]["n"] == 1 and ev["second"]["null"]["n"] == 2
    assert ev["all"]["gate"]["n"] == 4 and ev["all"]["null"]["n"] == 3
    assert ev["all"]["edge"]["exp_r"] == pytest.approx(0.0 - 0.5)

    assert ev["yearly"] == {2020: {"n": 2, "gate_r": 1.0, "null_r": 0.5},
                            2022: {"n": 2, "gate_r": -1.0, "null_r": 0.5}}
    assert ev["years"] == 2
    assert ev["positive_years"] == 1
    assert ev["negative_years"] == [2022]


def test_evaluate_without_a_null_still_reports_the_gate():
    gate = [_t(1.0, "2020-02-03"), _t(-1.0, "2022-02-03")]
    ev = rb.evaluate(gate, None, pd.Timestamp("2021-01-01"))
    assert ev["all"]["gate"]["n"] == 2
    assert ev["all"]["null"] is None and ev["all"]["edge"] == {}
    assert np.isnan(ev["yearly"][2020]["null_r"])
