"""Tests for thesis.sources: the signal_matrix + manual registration adapters."""
from __future__ import annotations

import pytest

from stockanalysis.signals import generate_signals
from stockanalysis.thesis import sources, store


@pytest.fixture
def signal_matrix(make_screened):
    """A real generate_signals frame: AAPL->Buy, KO->Hold, XYZ->Watch (tech 0)."""
    screened = make_screened({"AAPL": 6, "KO": 5, "XYZ": 2})
    return generate_signals(screened, tech_data={})


# --- from_manual ---------------------------------------------------------------

def test_from_manual_registers_and_maps_targets(tmp_path):
    tid = sources.from_manual(tmp_path, {
        "ticker": "ko", "thesis_type": "dividend_income",
        "thesis_statement": "Dividend king on a pullback.",
        "target_price": 60.0, "stop_price": 54.0, "shares": 7.86,
        "entry_date": "2026-01-15",
    }, salt="fixed")
    th = store.get(tmp_path, tid)
    assert th["ticker"] == "KO" and th["thesis_type"] == "dividend_income"
    assert th["status"] == "IDEA"
    assert th["entry"]["target_price"] == 60.0
    assert th["exit"]["stop_loss"] == 54.0
    assert th["created_at"].startswith("2026-01-15")
    assert th["origin"]["skill"] == "manual"
    assert th["origin"]["raw_provenance"]["shares"] == 7.86      # planned size kept as provenance


def test_from_manual_requires_core_fields(tmp_path):
    with pytest.raises(ValueError):
        sources.from_manual(tmp_path, {"ticker": "KO"})         # no statement / type


# --- from_signal_matrix --------------------------------------------------------

def test_from_signal_matrix_buys_only_by_default(tmp_path, signal_matrix):
    ids = sources.from_signal_matrix(tmp_path, signal_matrix, salt="fixed")
    theses = [store.get(tmp_path, i) for i in ids]
    assert {t["ticker"] for t in theses} == {"AAPL"}            # only the Buy row
    aapl = theses[0]
    assert aapl["status"] == "IDEA"
    assert aapl["thesis_type"] == "long_term_value"            # default
    assert aapl["origin"]["skill"] == "signal-matrix"
    # Row carried into provenance as native python types (JSON-serializable).
    rp = aapl["origin"]["raw_provenance"]
    assert rp["Final Action Signal"] == "Buy"
    assert isinstance(rp["Fundamental Score"], int)
    assert isinstance(rp["Composite"], float)


def test_from_signal_matrix_action_filter(tmp_path, signal_matrix):
    ids = sources.from_signal_matrix(tmp_path, signal_matrix, actions=("Buy", "Hold"),
                                     default_type="dividend_income", salt="fixed")
    theses = [store.get(tmp_path, i) for i in ids]
    assert {t["ticker"] for t in theses} == {"AAPL", "KO"}
    assert all(t["thesis_type"] == "dividend_income" for t in theses)


def test_from_signal_matrix_is_idempotent(tmp_path, signal_matrix):
    first = sources.from_signal_matrix(tmp_path, signal_matrix, salt="fixed")
    second = sources.from_signal_matrix(tmp_path, signal_matrix, salt="fixed")
    assert first == second                                      # same ids returned
    assert len(list(tmp_path.glob("th_*.json"))) == len(first)  # no duplicates written


def test_from_signal_matrix_empty(tmp_path):
    import pandas as pd
    assert sources.from_signal_matrix(tmp_path, pd.DataFrame()) == []
