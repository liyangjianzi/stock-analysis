"""Tests for thesis.model: id/fingerprint, canonical build, invariant validation.

Pure logic — no I/O, no network. All datetimes are RFC 3339 with timezone.
"""
from __future__ import annotations

import re

import pytest

from stockanalysis.thesis import model


# --- ids & fingerprints --------------------------------------------------------

def test_thesis_id_format_and_determinism():
    tid = model.make_thesis_id("AAPL", "long_term_value", "2026-06-30", salt="fixed")
    assert re.match(r"^th_aapl_val_20260630_[a-f0-9]{4}$", tid)
    # Same inputs + same salt -> same id (deterministic for tests / idempotency).
    assert model.make_thesis_id("AAPL", "long_term_value", "2026-06-30", salt="fixed") == tid


def test_thesis_id_strips_dotted_ticker():
    tid = model.make_thesis_id("SU.TO", "dividend_income", "2026-06-30", salt="x")
    assert tid.startswith("th_suto_div_20260630_")


def test_fingerprint_is_stable_and_order_independent():
    fp1 = model.compute_fingerprint(
        ticker="AAPL", thesis_type="long_term_value", thesis_statement="cheap compounder",
        source_date="2026-06-30", skill="signal-matrix", raw_provenance={"a": 1, "b": 2})
    fp2 = model.compute_fingerprint(
        ticker="AAPL", thesis_type="long_term_value", thesis_statement="cheap compounder",
        source_date="2026-06-30", skill="signal-matrix", raw_provenance={"b": 2, "a": 1})
    assert fp1 == fp2 and len(fp1) == 16
    fp3 = model.compute_fingerprint(
        ticker="MSFT", thesis_type="long_term_value", thesis_statement="cheap compounder",
        source_date="2026-06-30", skill="signal-matrix", raw_provenance={"a": 1, "b": 2})
    assert fp3 != fp1


# --- datetime helpers ----------------------------------------------------------

def test_normalize_datetime_widens_bare_date():
    assert model.normalize_datetime("2026-06-30") == "2026-06-30T00:00:00+00:00"


def test_normalize_datetime_passes_through_full_timestamp():
    assert model.normalize_datetime("2026-06-30T14:30:00+00:00") == "2026-06-30T14:30:00+00:00"
    assert model.normalize_datetime("2026-06-30T14:30:00Z") == "2026-06-30T14:30:00+00:00"


def test_add_days():
    assert model.add_days("2026-06-30", 30) == "2026-07-30"


# --- build_thesis (canonical shape) -------------------------------------------

def test_build_thesis_fills_canonical_defaults():
    th = model.build_thesis({
        "ticker": "AAPL", "thesis_type": "long_term_value",
        "thesis_statement": "Wonderful business at a fair price.",
        "origin": {"skill": "manual", "output_file": "-"},
    }, now="2026-06-30T00:00:00+00:00", salt="fixed")

    assert th["status"] == "IDEA"
    assert th["status_history"] == [
        {"status": "IDEA", "at": "2026-06-30T00:00:00+00:00", "reason": "registered"}]
    assert th["thesis_id"].startswith("th_aapl_val_20260630_")
    assert th["created_at"] == th["updated_at"] == "2026-06-30T00:00:00+00:00"
    assert th["origin"]["fingerprint"] and len(th["origin"]["fingerprint"]) == 16
    # Default monitoring: 30-day review interval, next date is created + 30d.
    assert th["monitoring"]["review_interval_days"] == 30
    assert th["monitoring"]["next_review_date"] == "2026-07-30"
    assert th["monitoring"]["review_status"] == "OK"
    # Empty entry/exit/position/outcome scaffolding present.
    assert th["entry"]["actual_price"] is None
    assert th["exit"]["exit_reason"] is None
    assert th["position"]["shares"] is None
    assert th["outcome"]["pnl_dollars"] is None


def test_build_thesis_backdates_created_at_and_carries_targets():
    th = model.build_thesis({
        "ticker": "ko", "thesis_type": "dividend_income",
        "thesis_statement": "Dividend king on a pullback.",
        "created_at": "2026-01-15",
        "entry": {"target_price": 60.0},
        "exit": {"stop_loss": 54.0},
        "monitoring": {"review_interval_days": 90},
        "origin": {"skill": "signal-matrix", "output_file": "run.xlsx"},
    }, salt="fixed")
    assert th["ticker"] == "KO"                       # normalized uppercase
    assert th["created_at"] == "2026-01-15T00:00:00+00:00"
    assert th["entry"]["target_price"] == 60.0
    assert th["exit"]["stop_loss"] == 54.0
    assert th["monitoring"]["next_review_date"] == "2026-04-15"   # +90d


def test_build_thesis_rejects_bad_type_and_missing_fields():
    with pytest.raises(ValueError):
        model.build_thesis({"ticker": "AAPL", "thesis_type": "scalp",
                            "thesis_statement": "x", "origin": {"skill": "m", "output_file": "-"}})
    with pytest.raises(ValueError):
        model.build_thesis({"ticker": "AAPL", "thesis_type": "long_term_value",
                            "thesis_statement": "", "origin": {"skill": "m", "output_file": "-"}})


# --- validate_thesis: status invariants ---------------------------------------

def _idea(**over):
    base = model.build_thesis({
        "ticker": "AAPL", "thesis_type": "long_term_value", "thesis_statement": "x",
        "origin": {"skill": "manual", "output_file": "-"}}, now="2026-06-01T00:00:00+00:00",
        salt="fixed")
    base.update(over)
    return base


def test_validate_active_requires_entry_and_matching_shares():
    th = _idea(status="ACTIVE")
    with pytest.raises(ValueError):
        model.validate_thesis(th)                      # no actual entry / shares
    th["entry"].update(actual_price=190.0, actual_date="2026-06-02T00:00:00+00:00")
    th["position"].update(shares=10.0, shares_remaining=10.0)
    model.validate_thesis(th)                          # now valid


def test_validate_partially_closed_bounds():
    th = _idea(status="PARTIALLY_CLOSED")
    th["entry"].update(actual_price=190.0, actual_date="2026-06-02T00:00:00+00:00")
    th["position"].update(shares=10.0, shares_remaining=4.0)
    model.validate_thesis(th)
    th["position"]["shares_remaining"] = 0.0           # not partial anymore
    with pytest.raises(ValueError):
        model.validate_thesis(th)


def test_validate_closed_requires_exit_and_zero_remaining():
    th = _idea(status="CLOSED")
    th["entry"].update(actual_price=190.0, actual_date="2026-06-02T00:00:00+00:00")
    th["position"].update(shares=10.0, shares_remaining=0.0)
    with pytest.raises(ValueError):
        model.validate_thesis(th)                      # missing exit data
    th["exit"].update(actual_price=230.0, actual_date="2026-06-20T00:00:00+00:00",
                      exit_reason="target_hit")
    model.validate_thesis(th)


def test_validate_rejects_exit_before_entry():
    th = _idea(status="CLOSED")
    th["entry"].update(actual_price=190.0, actual_date="2026-06-10T00:00:00+00:00")
    th["position"].update(shares=10.0, shares_remaining=0.0)
    th["exit"].update(actual_price=230.0, actual_date="2026-06-02T00:00:00+00:00",
                      exit_reason="target_hit")
    with pytest.raises(ValueError):
        model.validate_thesis(th)


def test_validate_rejects_nonmonotonic_history():
    th = _idea()
    th["status_history"] = [
        {"status": "IDEA", "at": "2026-06-02T00:00:00+00:00", "reason": "registered"},
        {"status": "ENTRY_READY", "at": "2026-06-01T00:00:00+00:00", "reason": "validated"}]
    th["status"] = "ENTRY_READY"
    with pytest.raises(ValueError):
        model.validate_thesis(th)
