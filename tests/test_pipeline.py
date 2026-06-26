"""Tests for pipeline helpers — fully offline (no network / yfinance)."""
from __future__ import annotations

import re
from pathlib import Path

from stockanalysis.pipeline import RUN_DIR_FMT, run_output_dir

# RUN_DIR_FMT is "%Y-%m-%d_%H%M%S" → e.g. 2026-06-25_143022
_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")


def test_run_output_dir_is_timestamped_subdir_of_base():
    base = "output"
    out = run_output_dir(base)

    assert isinstance(out, Path)
    assert out.parent == Path(base)
    assert _RUN_DIR_RE.match(out.name), f"{out.name!r} does not match {RUN_DIR_FMT}"


def test_run_output_dir_preserves_arbitrary_base():
    out = run_output_dir("/tmp/some/where")
    assert out.parent == Path("/tmp/some/where")
    assert _RUN_DIR_RE.match(out.name)


def test_run_output_dir_does_not_create_the_directory():
    """Compute-only: resolving a run dir must not touch the filesystem."""
    out = run_output_dir("output")
    assert not out.exists()
