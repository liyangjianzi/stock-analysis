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

# Personal retirement plan and its reports: gitignored (the repo is public).
DEFAULT_RETIREMENT_INPUTS = Path(__file__).resolve().parents[2] / "retirement" / "plan.json"
DEFAULT_RETIREMENT_OUT = Path(__file__).resolve().parents[2] / "retirement" / "output"
DEFAULT_RETIREMENT_BANK = Path(__file__).resolve().parents[2] / "retirement" / "bank"


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

# Broad-universe research cache (SQLite, stdlib). Bars only — no .info, which is
# today's data and therefore useless for history. Gitignored: it is a rebuildable
# artifact, not source. See stockanalysis.cache.
DEFAULT_CACHE_DB = Path(__file__).resolve().parents[2] / "data" / "cache" / "prices.db"

# 3 years of daily data is enough for a 200-day EMA plus context.
HISTORY_PERIOD = "3y"

# -----------------------------------------------------------------------------
# Risk / position sizing defaults, consumed by stockanalysis.tradeplan.
#
# The account size is a **notional** unless the caller overrides it (CLI:
# ``--account``). Every run's report states the figure it used so a share count
# is never mistaken for a real position. The decision knobs that belong to the
# signal contract itself (GATE_COMPONENTS, DEFAULT_FUND_MIN) live next to the
# component registry in signals.py; only the account-specific numbers live here.
# -----------------------------------------------------------------------------
DEFAULT_ACCOUNT_SIZE = 100_000.0   # notional equity the sizing is measured against
DEFAULT_RISK_PCT = 0.01            # fraction of the account risked per trade (1%)
DEFAULT_MAX_WEIGHT = 0.20          # cap on one position's notional (20% of account)
MIN_RR = 1.5                       # reward:risk below which a plan is flagged
ATR_STOP_MULT = 1.5                # stop distance in ATRs when no support is below
STOP_BUFFER_ATR = 0.25             # ATRs of slack placed under a structural level
# Distance floors, in ATRs, for accepting a support/resistance level as a stop or
# target. Price often sits *on* a level, and the nearest one can be a fraction of
# a percent away — too tight to survive a normal day's range as a stop, and
# meaningless as a target. Levels inside the floor are skipped for the next one
# out; if none qualifies, the ATR stop / 2R target take over.
MIN_STOP_ATR = 0.5
MIN_TARGET_ATR = 1.0
# Time stop, in bars after the signal: the backtest's time exit and the live
# plan's "Exit By" date, so the two can't describe different trades.
MAX_HOLD_BARS = 63
# Flag a plan whose next earnings report is this many sessions away or fewer.
# Not "before Exit By": a 63-bar hold spans a whole quarter, so nearly every
# plan crosses a report. Gate trades average ~8 bars (baseline, 2026-09-27), so
# 10 sessions covers the typical trade. A warning only — nothing decides on it.
EARNINGS_WARN_DAYS = 10
# Liquidity. The participation cap limits an order to a fraction of the name's
# 20-day average share volume, so a fill doesn't move the price it assumed. The
# dollar-volume floor only flags a thin name in the report (like MIN_RR, nothing
# else reads it). DVOL20 is in the listing's currency, so a .TO name is in CAD.
MAX_ADV_PARTICIPATION = 0.01       # max order size as a fraction of VOL_SMA20
MIN_DOLLAR_VOLUME = 5_000_000.0    # DVOL20 below which a plan is flagged

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

# Household portfolio risk (stockanalysis.portfolio) — display only, never an
# input to a signal. Weights are shares of the whole household in CAD, cash
# included. Concentration flags fire above these; cash is never "concentrated".
MAX_POSITION_WEIGHT = 0.10
MAX_SECTOR_WEIGHT = 0.30
RISK_LOOKBACK_BARS = 756          # ~3 years of daily returns for vol / VaR / beta
RISK_BENCHMARKS = {"S&P 500": "^GSPC", "Nasdaq-100": "^NDX"}
USDCAD_TICKER = "USDCAD=X"
HOLDINGS_STALE_DAYS = 7           # the risk report flags a holdings file older than this
# Crashes to replay the *current* portfolio through (first and last session).
STRESS_WINDOWS = {
    "Q4 2018 selloff": ("2018-09-20", "2018-12-24"),
    "COVID crash": ("2020-02-19", "2020-03-23"),
    "2022 bear market": ("2022-01-03", "2022-10-12"),
}

# Macro panel (display only — never an input to a signal). Yahoo quotes the
# Treasury yields in percent already (^TNX 5.18 = 5.18%, checked 2026-09-27), so
# changes are reported in bps and levels are never rescaled. The curve slope is
# "10Y Treasury" minus "3M T-Bill", so keep those two names.
MACRO_YIELDS = {"3M T-Bill": "^IRX", "5Y Treasury": "^FVX",
                "10Y Treasury": "^TNX", "30Y Treasury": "^TYX"}
MACRO_MARKETS = {"US Dollar (DXY)": "DX-Y.NYB", "WTI Crude": "CL=F", "Gold": "GC=F"}
# FRED series, fetched as CSV without an API key. Latest vintage, i.e. revised
# data — fine for context, never point-in-time, so never feed it to a backtest.
FRED_SERIES = {"Fed Funds": "DFF", "CPI": "CPIAUCSL",
               "Unemployment": "UNRATE", "Payrolls": "PAYEMS"}
