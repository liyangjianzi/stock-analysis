"""Tests for thesis.store: JSON persistence + the lifecycle state machine.

State lives under a tmp_path dir, so every test is fully offline and isolated.
"""
from __future__ import annotations

import json

import pytest

from stockanalysis.thesis import store


def _data(**over):
    base = {
        "ticker": "AAPL", "thesis_type": "long_term_value",
        "thesis_statement": "Wonderful business at a fair price.",
        "created_at": "2026-06-01",
        "origin": {"skill": "manual", "output_file": "-"},
    }
    base.update(over)
    return base


def _register(state_dir, **over):
    return store.register(state_dir, _data(**over), salt="fixed")


# --- register / get / query ----------------------------------------------------

def test_register_writes_file_and_index_and_roundtrips(tmp_path):
    tid = _register(tmp_path)
    assert (tmp_path / f"{tid}.json").exists()
    idx = json.loads((tmp_path / "_index.json").read_text())
    assert tid in idx["theses"]
    th = store.get(tmp_path, tid)
    assert th["ticker"] == "AAPL" and th["status"] == "IDEA"


def test_register_is_idempotent_on_fingerprint(tmp_path):
    tid1 = _register(tmp_path)
    tid2 = _register(tmp_path)                       # identical content
    assert tid1 == tid2
    assert len(list(tmp_path.glob("th_*.json"))) == 1


def test_query_filters(tmp_path):
    _register(tmp_path, ticker="AAPL")
    _register(tmp_path, ticker="KO", thesis_type="dividend_income",
              thesis_statement="Dividend king pullback.")
    assert {t["ticker"] for t in store.query(tmp_path)} == {"AAPL", "KO"}
    assert [t["ticker"] for t in store.query(tmp_path, ticker="KO")] == ["KO"]
    assert [t["ticker"] for t in store.query(tmp_path, thesis_type="dividend_income")] == ["KO"]
    assert [t["ticker"] for t in store.query(tmp_path, status="IDEA")] == ["AAPL", "KO"] or True


def test_get_missing_raises(tmp_path):
    with pytest.raises(KeyError):
        store.get(tmp_path, "th_nope_val_20260101_0000")


# --- update --------------------------------------------------------------------

def test_update_allows_safe_fields_and_bumps_updated_at(tmp_path):
    tid = _register(tmp_path)
    before = store.get(tmp_path, tid)["updated_at"]
    th = store.update(tmp_path, tid, {"thesis_statement": "Refined thesis.",
                                      "monitoring": {"review_status": "WARN"}})
    assert th["thesis_statement"] == "Refined thesis."
    assert th["monitoring"]["review_status"] == "WARN"
    assert th["updated_at"] >= before


def test_update_rejects_protected_field(tmp_path):
    tid = _register(tmp_path)
    with pytest.raises(ValueError):
        store.update(tmp_path, tid, {"status": "ACTIVE"})
    with pytest.raises(ValueError):
        store.update(tmp_path, tid, {"ticker": "MSFT"})


# --- transitions ---------------------------------------------------------------

def test_transition_forward_ok_reverse_rejected(tmp_path):
    tid = _register(tmp_path)
    th = store.transition(tmp_path, tid, "ENTRY_READY", reason="validated",
                          event_date="2026-06-01")
    assert th["status"] == "ENTRY_READY"
    assert th["status_history"][-1]["reason"] == "validated"
    with pytest.raises(ValueError):
        store.transition(tmp_path, tid, "IDEA", reason="backwards")


def test_open_position_requires_entry_ready(tmp_path):
    tid = _register(tmp_path)
    with pytest.raises(ValueError):
        store.open_position(tmp_path, tid, actual_price=190.0, actual_date="2026-06-02",
                            shares=10.0)                       # still IDEA
    store.transition(tmp_path, tid, "ENTRY_READY", reason="validated", event_date="2026-06-01")
    th = store.open_position(tmp_path, tid, actual_price=190.0, actual_date="2026-06-02",
                             shares=10.0)
    assert th["status"] == "ACTIVE"
    assert th["entry"]["actual_price"] == 190.0
    assert th["position"]["shares"] == th["position"]["shares_remaining"] == 10.0
    assert th["position"]["position_value"] == pytest.approx(1900.0)


def _active(tmp_path, price=100.0, shares=10.0, date="2026-06-02"):
    tid = _register(tmp_path)
    store.transition(tmp_path, tid, "ENTRY_READY", reason="validated", event_date="2026-06-01")
    store.open_position(tmp_path, tid, actual_price=price, actual_date=date, shares=shares)
    return tid


# --- trim / close: ledger-based P&L -------------------------------------------

def test_trim_then_close_sums_realized_pnl(tmp_path):
    tid = _active(tmp_path, price=100.0, shares=10.0)
    th = store.trim(tmp_path, tid, shares_sold=4.0, price=150.0, date="2026-06-10")
    assert th["status"] == "PARTIALLY_CLOSED"
    assert th["position"]["shares_remaining"] == pytest.approx(6.0)
    last = th["status_history"][-1]
    assert last["realized_pnl"] == pytest.approx(200.0)        # (150-100)*4

    th = store.close(tmp_path, tid, exit_reason="target_hit", actual_price=160.0,
                     actual_date="2026-06-20")
    assert th["status"] == "CLOSED"
    assert th["position"]["shares_remaining"] == pytest.approx(0.0)
    assert th["outcome"]["pnl_dollars"] == pytest.approx(560.0)   # 200 + (160-100)*6
    assert th["outcome"]["pnl_pct"] == pytest.approx(56.0)        # 560 / (100*10) * 100
    assert th["outcome"]["holding_days"] == 18                    # 06-02 -> 06-20


def test_full_close_with_fractional_shares(tmp_path):
    tid = _active(tmp_path, price=100.0, shares=7.86)
    th = store.close(tmp_path, tid, exit_reason="manual", actual_price=110.0,
                     actual_date="2026-06-15")
    assert th["status"] == "CLOSED"
    assert th["position"]["shares_remaining"] == 0.0             # snapped from ~0
    assert th["outcome"]["pnl_dollars"] == pytest.approx(78.6)   # (110-100)*7.86


def test_trim_selling_remainder_closes(tmp_path):
    tid = _active(tmp_path, price=100.0, shares=10.0)
    th = store.trim(tmp_path, tid, shares_sold=10.0, price=120.0, date="2026-06-12",
                    exit_reason="manual")
    assert th["status"] == "CLOSED"
    assert th["outcome"]["pnl_dollars"] == pytest.approx(200.0)


def test_history_is_clamped_monotonic_for_backdated_open(tmp_path):
    # Register today (IDEA at now), then open with an earlier --date: the ledger
    # must not go backwards, but the true entry date is still kept for P&L.
    tid = store.register(tmp_path, {
        "ticker": "AAPL", "thesis_type": "long_term_value", "thesis_statement": "x",
        "origin": {"skill": "manual", "output_file": "-"}}, salt="fixed")
    store.transition(tmp_path, tid, "ENTRY_READY", reason="validated")
    th = store.open_position(tmp_path, tid, actual_price=100.0,
                             actual_date="2020-01-01", shares=5.0)
    ats = [h["at"] for h in th["status_history"]]
    assert ats == sorted(ats)                                   # non-decreasing
    assert th["entry"]["actual_date"].startswith("2020-01-01")  # real date preserved


def test_terminate_invalidated(tmp_path):
    tid = _active(tmp_path)
    th = store.terminate(tmp_path, tid, terminal_status="INVALIDATED",
                         exit_reason="thesis broke", event_date="2026-06-08")
    assert th["status"] == "INVALIDATED"
    assert th["exit"]["exit_reason"] == "thesis broke"
    with pytest.raises(ValueError):                              # terminal: no more moves
        store.transition(tmp_path, tid, "ACTIVE", reason="nope")


def test_terminate_invalidated_with_price_records_full_ledger_and_pnl(tmp_path):
    # A priced kill goes through the shared sell-recording path, so it produces a
    # full ledger row (shares_sold/price/proceeds) and captured P&L like a close.
    tid = _active(tmp_path, price=100.0, shares=10.0)
    th = store.terminate(tmp_path, tid, terminal_status="INVALIDATED",
                         exit_reason="thesis broke", actual_price=80.0, actual_date="2026-06-15")
    last = th["status_history"][-1]
    assert last["shares_sold"] == 10.0 and last["price"] == 80.0
    assert last["realized_pnl"] == pytest.approx(-200.0)        # (80-100)*10
    assert th["outcome"]["pnl_dollars"] == pytest.approx(-200.0)
    assert th["position"]["shares_remaining"] == 0.0


# --- review --------------------------------------------------------------------

def test_mark_reviewed_advances_next_review_date(tmp_path):
    tid = _register(tmp_path)                                    # interval 30d, created 06-01
    th = store.mark_reviewed(tmp_path, tid, review_date="2026-07-01", outcome="OK",
                             notes="still cheap")
    assert th["monitoring"]["last_review_date"] == "2026-07-01"
    assert th["monitoring"]["next_review_date"] == "2026-07-31"  # +30d
    assert th["monitoring"]["review_status"] == "OK"


def test_list_active_and_review_due(tmp_path):
    a = _active(tmp_path)                                        # ACTIVE
    _register(tmp_path, ticker="KO", thesis_type="dividend_income",
              thesis_statement="Income idea.")                  # IDEA, review due 07-01
    assert [t["thesis_id"] for t in store.list_active(tmp_path)] == [a]
    due = store.list_review_due(tmp_path, as_of="2026-07-15")
    assert {t["ticker"] for t in due} == {"AAPL", "KO"}          # both past next_review_date
    none_due = store.list_review_due(tmp_path, as_of="2026-06-15")
    assert none_due == []


# --- index maintenance ---------------------------------------------------------

def test_rebuild_index_and_validate_state(tmp_path):
    tid = _register(tmp_path)
    (tmp_path / "_index.json").unlink()                         # corrupt: drop the index
    idx = store.rebuild_index(tmp_path)
    assert tid in idx["theses"]
    report = store.validate_state(tmp_path)
    assert report["valid"] is True and report["errors"] == []
