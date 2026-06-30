"""Tests for thesis.report: the aggregated HTML journal renderer + writer.

Pure-logic + tmp-dir I/O, fully offline (no network / yfinance).
"""
from __future__ import annotations

from pathlib import Path

from stockanalysis.thesis import report, review, store


def _idea(state_dir, ticker, statement="Idea.", thesis_type="long_term_value"):
    return store.register(state_dir, {
        "ticker": ticker, "thesis_type": thesis_type,
        "thesis_statement": statement, "created_at": "2026-06-01",
        "origin": {"skill": "manual", "output_file": "-"}}, salt=ticker)


def _closed(state_dir, ticker, entry=100.0, exit_=130.0, shares=10.0):
    tid = _idea(state_dir, ticker)
    store.transition(state_dir, tid, "ENTRY_READY", reason="v", event_date="2026-06-01")
    store.open_position(state_dir, tid, actual_price=entry, actual_date="2026-06-02",
                        shares=shares)
    store.close(state_dir, tid, exit_reason="target_hit", actual_price=exit_,
                actual_date="2026-06-20")
    return tid


def test_build_html_report_includes_theses_and_summary(tmp_path):
    _idea(tmp_path, "AAPL")
    _closed(tmp_path, "MSFT", entry=100.0, exit_=130.0)
    theses = store.query(tmp_path)
    summary = review.summary_stats(tmp_path)

    out = report.build_html_report(theses, summary, generated_at="2026-06-30 12:00")

    assert "<html" in out and "</html>" in out
    assert "<table" in out
    assert "AAPL" in out and "MSFT" in out
    assert "IDEA" in out and "CLOSED" in out
    assert "300.0" in out                 # MSFT P&L $: (130-100)*10
    assert "2026-06-30 12:00" in out


def test_build_html_report_empty_store(tmp_path):
    out = report.build_html_report([], review.summary_stats(tmp_path),
                                   generated_at="2026-06-30 12:00")
    assert "No theses yet" in out
    assert "<html" in out


def test_build_html_report_escapes_values(tmp_path):
    # generated_at is a rendered, caller-supplied header field, so special
    # characters in it must be HTML-escaped (exercises the _esc path).
    out = report.build_html_report(
        [], review.summary_stats(tmp_path), generated_at="<b>2026 & co</b>")
    assert "&lt;b&gt;2026 &amp; co&lt;/b&gt;" in out
    assert "<b>2026" not in out
