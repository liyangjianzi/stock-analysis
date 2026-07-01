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


def test_write_report_creates_timestamped_html(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _closed(state, "MSFT")
    out_base = tmp_path / "out"

    path = report.write_report(state, out_base=str(out_base))

    p = Path(path)
    assert p.name == "report.html"
    assert p.exists() and p.stat().st_size > 0
    assert p.parent.parent == out_base          # out_base/<timestamp>/report.html
    assert "MSFT" in p.read_text()


def test_write_report_empty_store_still_writes(tmp_path):
    path = report.write_report(tmp_path / "state2", out_base=str(tmp_path / "out2"))
    assert Path(path).exists()
    assert "No theses yet" in Path(path).read_text()


def test_build_html_report_renders_populated_mae_mfe(tmp_path):
    _closed(tmp_path, "MSFT", entry=100.0, exit_=130.0)
    theses = store.query(tmp_path)
    # Simulate a postmortem having persisted MAE/MFE onto the outcome.
    theses[0]["outcome"]["mae_pct"] = -8.5
    theses[0]["outcome"]["mfe_pct"] = 42.0
    out = report.build_html_report(theses, review.summary_stats(tmp_path),
                                   generated_at="t")
    assert "-8.5%" in out and "42.0%" in out


def test_build_html_report_unknown_status_uses_default_badge(tmp_path):
    _idea(tmp_path, "AAPL")
    theses = store.query(tmp_path)
    theses[0]["status"] = "WEIRD"          # not in _STATUS_COLORS
    out = report.build_html_report(theses, review.summary_stats(tmp_path),
                                   generated_at="t")
    assert "#9e9e9e" in out                 # default-gray fallback
    assert "WEIRD" in out
