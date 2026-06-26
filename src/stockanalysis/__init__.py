"""StockAnalysis — North American equity analysis toolkit.

Library-first: the pipeline and its building blocks are importable with no
notebook/IO side effects, so a CLI or server can drive them directly.

Quick start::

    from stockanalysis import run
    results = run(export_target="excel", save_charts=True)
    print(results.signal_matrix)
"""
from __future__ import annotations

from . import charts, config, indicators, ingest, overview, profile, screener, signals
from .pipeline import Results, run, run_output_dir

__all__ = [
    "run", "Results", "run_output_dir",
    "config", "ingest", "screener", "indicators", "signals",
    "overview", "profile", "charts",
]

__version__ = "0.1.0"
