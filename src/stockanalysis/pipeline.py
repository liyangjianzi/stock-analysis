"""End-to-end orchestration: ingest → screen → indicators → signals (+ outputs).

``run`` is the single entry point a script, the CLI, or a future server calls.
It performs no printing/plotting side effects beyond logging; everything lands
in the returned :class:`Results`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import charts, config
from .indicators import add_indicators
from .ingest import load_watchlist
from .outputs import get_exporter
from .screener import screen_fundamentals
from .signals import generate_signals

log = logging.getLogger(__name__)

#: Folder-name format for a single run's output subdirectory (e.g. 2026-06-25_143022).
RUN_DIR_FMT = "%Y-%m-%d_%H%M%S"


def run_output_dir(base: str = "output") -> Path:
    """Return a fresh per-run subdir of ``base`` named with a timestamp.

    Compute-only: the caller creates the directory when it actually writes,
    so a run that produces nothing never litters an empty folder.
    """
    return Path(base) / datetime.now().strftime(RUN_DIR_FMT)


@dataclass
class Results:
    """Everything one pipeline run produces."""
    prices: dict = field(default_factory=dict)
    fundamentals_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    screened_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    tech: dict = field(default_factory=dict)
    signal_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    export_destination: str | None = None
    chart_paths: list[str] = field(default_factory=list)
    run_dir: str | None = None


def compute_indicators(prices: dict) -> dict:
    """Enrich every fetched ticker with indicator columns (skips failures)."""
    tech = {}
    for tk, hist in prices.items():
        try:
            tech[tk] = add_indicators(hist)
        except Exception as e:
            log.warning("%s: indicator computation failed (%s) — skipping.", tk, e)
    return tech


def run(watchlist: dict | None = None,
        period: str = config.HISTORY_PERIOD,
        export_target: str | None = None,
        export_opts: dict | None = None,
        save_charts: bool = False,
        out_dir: str = "output") -> Results:
    """Run the full pipeline and return a :class:`Results`.

    Parameters
    ----------
    watchlist     : ticker -> sector mapping (defaults to the watchlist CSV).
    period        : yfinance history period (e.g. '3y').
    export_target : 'excel' | 'gsheets' | None. When set, the signal matrix +
                    screener detail are written via that exporter.
    export_opts   : kwargs forwarded to the exporter (e.g. path, spreadsheet).
                    An explicit ``path`` bypasses the timestamped run dir.
    save_charts   : if True, write one HTML dashboard per screened ticker.
    out_dir       : base output directory; this run's artifacts land in a fresh
                    timestamped subdir ``out_dir/<YYYY-MM-DD_HHMMSS>/``.
    """
    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist
    export_opts = dict(export_opts or {})
    run_dir = run_output_dir(out_dir)

    prices, fundamentals_df = load_watchlist(watchlist, period=period)
    screened_df = screen_fundamentals(fundamentals_df)
    tech = compute_indicators(prices)
    signal_matrix = generate_signals(screened_df, tech)

    results = Results(
        prices=prices, fundamentals_df=fundamentals_df, screened_df=screened_df,
        tech=tech, signal_matrix=signal_matrix, run_dir=str(run_dir),
    )

    if export_target:
        # Default the Excel path into the run dir when not explicitly overridden.
        if export_target == "excel" and "path" not in export_opts:
            run_dir.mkdir(parents=True, exist_ok=True)
            export_opts["path"] = str(run_dir / "signal_matrix.xlsx")
        results.export_destination = get_exporter(export_target, **export_opts).export(
            signal_matrix, screened_df
        )

    if save_charts:
        for ticker in (screened_df.index if not screened_df.empty else tech.keys()):
            fig = charts.build_technical_dashboard(ticker, tech)
            if fig is not None:
                results.chart_paths.append(
                    charts.save_html(fig, run_dir / f"{ticker}.html")
                )
        log.info("Saved %d dashboard chart(s) to '%s'.", len(results.chart_paths), run_dir)

    return results
