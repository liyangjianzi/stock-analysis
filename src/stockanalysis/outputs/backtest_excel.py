"""Excel writer for backtest results — Summary, Event Study, Planned Trades.

Reuses the base styling from :mod:`stockanalysis.outputs.excel` so the two
workbooks look consistent. Not an :class:`Exporter` subclass: the Exporter
contract is signal-matrix shaped, whereas a backtest produces different tables.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .excel import _style_base

log = logging.getLogger(__name__)


def write_backtest_workbook(results, path) -> str:
    """Write a styled two-sheet backtest workbook and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    summary_df = pd.DataFrame([{"mode": results.mode, **(results.portfolio_summary or {})}])

    rows = []
    for bucket, hstats in results.event_stats.items():
        for horizon, d in hstats.items():
            rows.append({"Bucket": bucket, "Horizon": horizon, **d})
    event_df = pd.DataFrame(rows)

    # exits="plan": one row per walked trade, plus its aggregate as a summary row.
    trades_df = pd.DataFrame([{
        "Ticker": t.ticker, "Entry Date": t.entry_date, "Entry": t.entry,
        "Stop": t.stop, "Target": t.target, "Exit Date": t.exit_date,
        "Exit": t.exit_price, "Exit Reason": t.exit_reason,
        "R": t.r_multiple, "Bars Held": t.bars_held,
    } for t in results.trades])
    if results.trade_stats:
        summary_df = pd.concat(
            [summary_df, pd.DataFrame([results.trade_stats])], axis=1)

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        summary_df.to_excel(xl, sheet_name="Backtest Summary", index=False)
        if not event_df.empty:
            event_df.to_excel(xl, sheet_name="Event Study", index=False)
        if not trades_df.empty:
            trades_df.to_excel(xl, sheet_name="Planned Trades", index=False)
        for ws in xl.sheets.values():
            _style_base(ws)

    log.info("Wrote backtest workbook to '%s'.", path)
    return str(path)
