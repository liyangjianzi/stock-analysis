"""Command-line entry point: run the pipeline end-to-end.

    stock-analysis run --target excel --out output/
    stock-analysis run --target gsheets --spreadsheet <id|name>
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config, pipeline


def _add_run_parser(sub) -> None:
    p = sub.add_parser("run", help="Run the full analysis pipeline.")
    p.add_argument("--target", choices=["excel", "gsheets", "none"], default="excel",
                   help="Where to export the signal matrix (default: excel).")
    p.add_argument("--out", default="output",
                   help="Output directory for charts / the Excel file (default: output/).")
    p.add_argument("--period", default=config.HISTORY_PERIOD,
                   help="yfinance history period (default: %(default)s).")
    p.add_argument("--watchlist", default=None,
                   help="Path to a watchlist CSV (ticker,sector). "
                        "Defaults to data/watchlist.csv.")
    p.add_argument("--spreadsheet", default=None,
                   help="Google Sheet id or name (gsheets target).")
    p.add_argument("--no-charts", action="store_true",
                   help="Skip writing per-ticker HTML dashboards.")


def _add_backtest_parser(sub) -> None:
    p = sub.add_parser("backtest", help="Backtest the signal engine over history.")
    p.add_argument("--scope", choices=["technical", "composite"], default="technical",
                   help="technical = price-only (no lookahead); composite = full "
                        "Buy/Hold/Watch with TODAY's fundamentals (lookahead-biased).")
    p.add_argument("--period", default="5y", help="yfinance history period (default: 5y).")
    p.add_argument("--horizon", choices=["1m", "3m", "6m"], action="append", default=None,
                   help="Forward-return horizon(s); repeatable (default: 1m 3m 6m).")
    p.add_argument("--max-hold", choices=["1m", "3m", "6m"], default="3m",
                   help="Max holding period for the portfolio sim (default: 3m).")
    p.add_argument("--max-positions", type=int, default=10,
                   help="Max concurrent positions (default: 10).")
    p.add_argument("--cost-bps", type=float, default=10.0,
                   help="Round-trip cost per side in basis points (default: 10).")
    p.add_argument("--slippage-mult", type=float, default=1.0,
                   help="Multiply costs to stress-test execution (e.g. 1.5, 2.0).")
    p.add_argument("--out", default="output/backtest", help="Output base directory.")
    p.add_argument("--no-excel", action="store_true", help="Skip the Excel workbook.")
    p.add_argument("--no-report", action="store_true", help="Skip the HTML report.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-analysis", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging.")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_backtest_parser(sub)
    from .thesis import cli as thesis_cli
    thesis_cli.add_parser(sub)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "run":
        export_target = None if args.target == "none" else args.target
        export_opts = {}
        if export_target == "gsheets" and args.spreadsheet:
            export_opts["spreadsheet"] = args.spreadsheet

        try:
            watchlist = config.load_watchlist_csv(args.watchlist) if args.watchlist else None
            results = pipeline.run(
                watchlist=watchlist,
                period=args.period,
                export_target=export_target,
                export_opts=export_opts,
                save_charts=not args.no_charts,
                out_dir=args.out,
            )
        except Exception as e:
            print(f"Pipeline failed: {e}", file=sys.stderr)
            return 1

        n = len(results.signal_matrix)
        print(f"\nDone. Signal matrix: {n} stocks.")
        if results.export_destination:
            print(f"Exported to: {results.export_destination}")
        if results.chart_paths:
            print(f"Charts: {len(results.chart_paths)} HTML file(s) in '{results.run_dir}/'.")
        if n:
            buys = results.signal_matrix[
                results.signal_matrix["Final Action Signal"] == "Buy"
            ]["Ticker"].tolist()
            print(f"Buys: {buys or 'none'}")
        return 0

    if args.command == "backtest":
        from . import backtest as bt

        horizons = tuple(args.horizon) if args.horizon else ("1m", "3m", "6m")
        if args.scope == "composite":
            print("⚠ COMPOSITE SCOPE: fundamentals are frozen at TODAY's values, so "
                  "past composites are LOOKAHEAD-BIASED. Treat results as a sanity "
                  "check, not proof of edge.")
        try:
            results = bt.run_backtest(
                period=args.period, mode=args.scope, horizons=horizons,
                max_hold=args.max_hold, max_positions=args.max_positions,
                cost_bps=args.cost_bps, slippage_mult=args.slippage_mult,
                out_dir=args.out, export_excel=not args.no_excel,
                save_report=not args.no_report,
            )
        except Exception as e:
            print(f"Backtest failed: {e}", file=sys.stderr)
            return 1

        s = results.portfolio_summary or {}
        print(f"\nBacktest ({results.mode}) done.")
        if s:
            print(f"  Trades: {s.get('n_trades')}  Win rate: {s.get('win_rate')}  "
                  f"Total return: {s.get('total_return')}  Max DD: {s.get('max_drawdown')}")
        if results.excel_path:
            print(f"  Workbook: {results.excel_path}")
        if results.report_path:
            print(f"  Report:   {results.report_path}")
        return 0

    if args.command == "thesis":
        from .thesis import cli as thesis_cli
        try:
            return thesis_cli.dispatch(args)
        except (ValueError, KeyError, FileNotFoundError) as e:
            print(f"Thesis command failed: {e}", file=sys.stderr)
            return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
