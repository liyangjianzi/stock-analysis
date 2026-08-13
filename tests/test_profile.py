"""Tests for profile.save_report — the thin I/O wrapper (no network).

``build_profile`` itself needs a live yfinance fetch, so only the persistence
half is unit-tested here; the report string is supplied directly.
"""
from __future__ import annotations

from pathlib import Path

from stockanalysis.profile import save_report

# The real report is drawn with box characters, an em dash and a check mark —
# exactly the bytes that break under a non-UTF-8 default locale.
REPORT = "══════\n  ACME · Moat ✓  ·  P/E —\n══════\n"


def test_save_report_writes_utf8_verbatim(tmp_path):
    path = save_report({"report": REPORT}, tmp_path / "ACME_profile.txt")

    assert Path(path).read_text(encoding="utf-8") == REPORT
    assert Path(path).read_bytes().decode("utf-8") == REPORT   # not the platform default


def test_save_report_creates_parent_dirs(tmp_path):
    """Mirrors charts.save_html: the caller never has to mkdir first."""
    path = save_report({"report": REPORT}, tmp_path / "run" / "nested" / "ACME_profile.txt")

    assert Path(path).exists()


def test_save_report_returns_the_path_as_a_string(tmp_path):
    target = tmp_path / "ACME_profile.txt"
    path = save_report({"report": REPORT}, target)

    assert isinstance(path, str)
    assert Path(path) == target


def test_save_report_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "ACME_profile.txt"
    save_report({"report": "stale"}, target)
    save_report({"report": REPORT}, target)

    assert target.read_text(encoding="utf-8") == REPORT
