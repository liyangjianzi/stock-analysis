"""Static configuration: history window and the Stage-0 market-overview
universe / indices, plus the watchlist loader.

The Stage-0 constants are plain module values that can be imported directly or
overridden by callers (the CLI and the future server pass their own values into
``pipeline.run``). The watchlist itself lives in an external CSV
(``data/watchlist.csv``) and is read on demand by :func:`load_watchlist_csv` —
no I/O happens at import time.
"""
from __future__ import annotations

import csv
import functools
from pathlib import Path

# Default dashboard height in pixels (used by chart builders).
PLOT_HEIGHT = 850

# -----------------------------------------------------------------------------
# WATCHLIST — ticker -> sector, loaded from a CSV (columns: ticker,sector) so it
# can be edited without touching source. The default file sits at the project
# root; ``parents[2]`` walks config.py -> stockanalysis -> src -> project root.
# -----------------------------------------------------------------------------
DEFAULT_WATCHLIST_CSV = Path(__file__).resolve().parents[2] / "data" / "watchlist.csv"

# Thesis-memory state lives alongside the watchlist under data/ (persistent
# state, not per-run output). One JSON file per thesis + an _index.json; the
# thesis CLI / library default to this dir. See stockanalysis.thesis.
DEFAULT_THESES_DIR = Path(__file__).resolve().parents[2] / "data" / "theses"


@functools.lru_cache(maxsize=None)
def _read_watchlist_csv(path: Path) -> tuple[tuple[str, str], ...]:
    """Read (ticker, sector) pairs from ``path``. Cached per resolved path so the
    pipeline's fallback call sites don't re-read disk within a single run.
    Returns an immutable tuple so the cached value is never mutated."""
    with open(path, newline="") as f:
        rows = csv.DictReader(f)
        return tuple(
            (r["ticker"].strip(), r["sector"].strip())
            for r in rows
            if r.get("ticker", "").strip()
        )


def load_watchlist_csv(path=None) -> dict[str, str]:
    """Load the ticker -> sector watchlist from a CSV (columns: ticker,sector).

    Defaults to :data:`DEFAULT_WATCHLIST_CSV`. Raises ``FileNotFoundError`` with
    the resolved path if the file is missing (fails loudly rather than silently
    running on an empty watchlist).
    """
    p = Path(path) if path else DEFAULT_WATCHLIST_CSV
    if not p.exists():
        raise FileNotFoundError(f"Watchlist CSV not found: {p}")
    return dict(_read_watchlist_csv(p.resolve()))

# 3 years of daily data is enough for a 200-day EMA plus context.
HISTORY_PERIOD = "3y"

# -----------------------------------------------------------------------------
# Stage-0 (daily market overview) configuration.
# -----------------------------------------------------------------------------
CANDIDATE_UNIVERSE = {
    # Technology
    "META": "Technology", "AMD": "Technology", "CRM": "Technology",
    "ORCL": "Technology", "ADBE": "Technology",
    # Financials
    "GS": "Financials", "MS": "Financials", "BLK": "Financials",
    "AXP": "Financials", "V": "Financials",
    # Healthcare
    "LLY": "Healthcare", "MRK": "Healthcare", "ABT": "Healthcare",
    "TMO": "Healthcare", "DHR": "Healthcare",
    # Consumer
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "MCD": "Consumer Staples",
    "PG": "Consumer Staples",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "SU.TO": "Energy",
    # Industrials
    "CAT": "Industrials", "BA": "Industrials", "UNP": "Industrials",
    # Utilities / Real Estate / Materials
    "NEE": "Utilities", "AMT": "Real Estate", "LIN": "Materials",
}

OVERVIEW_INDICES = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "TSX": "^GSPTSE"}
VIX_TICKER = "^VIX"
OVERVIEW_LOOKBACK = 60  # trading days for the index chart
