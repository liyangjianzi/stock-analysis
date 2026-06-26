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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-analysis", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging.")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
