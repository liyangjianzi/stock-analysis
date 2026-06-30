"""Tests for thesis.review: MAE/MFE, postmortem rendering, summary stats.

The price source is injected as a fake adapter, so these run fully offline.
"""
from __future__ import annotations

import pytest

from stockanalysis.thesis import review, store


class FakeAdapter:
    """Stand-in for YFinancePriceAdapter: returns a fixed close series."""
    source = "fake_eod"

    def __init__(self, closes):
        self._closes = closes

    def get_daily_closes(self, ticker, from_date, to_date):
        return [{"date": from_date, "close": c} for c in self._closes]


def _closed(state_dir, ticker="AAPL", entry=100.0, exit_=130.0, shares=10.0,
            thesis_type="long_term_value"):
    tid = store.register(state_dir, {
        "ticker": ticker, "thesis_type": thesis_type,
        "thesis_statement": f"{ticker} idea.", "created_at": "2026-06-01",
        "origin": {"skill": "manual", "output_file": "-"}}, salt=ticker)
    store.transition(state_dir, tid, "ENTRY_READY", reason="validated", event_date="2026-06-01")
    store.open_position(state_dir, tid, actual_price=entry, actual_date="2026-06-02",
                        shares=shares)
    store.close(state_dir, tid, exit_reason="target_hit", actual_price=exit_,
                actual_date="2026-06-20")
    return tid


# --- MAE / MFE -----------------------------------------------------------------

def test_compute_mae_mfe_from_injected_prices(tmp_path):
    tid = _closed(tmp_path, entry=100.0)
    th = store.get(tmp_path, tid)
    mm = review.compute_mae_mfe(th, FakeAdapter([95.0, 130.0, 110.0]))
    assert mm["mae_pct"] == pytest.approx(-5.0)     # (95-100)/100*100
    assert mm["mfe_pct"] == pytest.approx(30.0)     # (130-100)/100*100
    assert mm["mae_mfe_source"] == "fake_eod"


def test_compute_mae_mfe_without_prices_is_none(tmp_path):
    tid = _closed(tmp_path)
    mm = review.compute_mae_mfe(store.get(tmp_path, tid), FakeAdapter([]))
    assert mm == {"mae_pct": None, "mfe_pct": None, "mae_mfe_source": None}


# --- postmortem ----------------------------------------------------------------

def test_generate_postmortem_writes_report_and_fills_outcome(tmp_path):
    tid = _closed(tmp_path, entry=100.0, exit_=130.0)
    path = review.generate_postmortem(tmp_path, tid, price_adapter=FakeAdapter([90.0, 140.0]))
    text = open(path).read()
    assert tid in text and "Postmortem" in text
    assert "P&L" in text
    th = store.get(tmp_path, tid)                   # outcome persisted back
    assert th["outcome"]["mae_pct"] == pytest.approx(-10.0)
    assert th["outcome"]["mfe_pct"] == pytest.approx(40.0)


def test_postmortem_rejects_open_thesis(tmp_path):
    tid = store.register(tmp_path, {
        "ticker": "AAPL", "thesis_type": "long_term_value", "thesis_statement": "x",
        "origin": {"skill": "manual", "output_file": "-"}}, salt="fixed")
    with pytest.raises(ValueError):
        review.generate_postmortem(tmp_path, tid)   # still IDEA


# --- summary -------------------------------------------------------------------

def test_summary_stats_aggregates_wins_and_by_type(tmp_path):
    _closed(tmp_path, ticker="AAPL", entry=100.0, exit_=130.0)         # +30%  win
    _closed(tmp_path, ticker="MSFT", entry=100.0, exit_=90.0)          # -10%  loss
    _closed(tmp_path, ticker="KO", entry=100.0, exit_=120.0,
            thesis_type="dividend_income")                            # +20%  win
    s = review.summary_stats(tmp_path)
    assert s["count"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-4)        # win_rate rounded to 4 dp
    assert s["avg_pnl_pct"] == pytest.approx((30 - 10 + 20) / 3, abs=1e-4)
    assert s["by_type"]["dividend_income"] == {"count": 1, "win_rate": 1.0, "avg_pnl_pct": 20.0}


def test_summary_stats_empty(tmp_path):
    assert review.summary_stats(tmp_path) == {
        "count": 0, "win_rate": None, "avg_pnl_pct": None, "by_type": {}}
