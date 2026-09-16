"""Command-line entry point: run the pipeline end-to-end.

    stock-analysis run --target excel --out output/
    stock-analysis run --target gsheets --spreadsheet <id|name>
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config, pipeline, signals


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
    p.add_argument("--no-report", action="store_true",
                   help="Skip writing the combined HTML report (screener + "
                        "signal matrix + top-N dashboards/profiles + market overview).")
    p.add_argument("--fund-min", type=int, default=signals.DEFAULT_FUND_MIN,
                   metavar="N",
                   help="Minimum fundamental score (of 6) for a name to be ownable; "
                        "below it the action is Watch (default: %(default)s).")
    p.add_argument("--account", type=float, default=config.DEFAULT_ACCOUNT_SIZE,
                   metavar="USD",
                   help="Account equity the position sizing is measured against "
                        "(default: %(default)s, a notional figure).")
    p.add_argument("--risk-pct", type=float, default=config.DEFAULT_RISK_PCT * 100,
                   metavar="PCT",
                   help="Percent of the account risked per trade, as a number "
                        "(1.0 = 1%%) (default: %(default)s).")
    p.add_argument("--max-weight", type=float, default=config.DEFAULT_MAX_WEIGHT * 100,
                   metavar="PCT",
                   help="Cap on one position's notional, as a percent of the "
                        "account (default: %(default)s).")
    p.add_argument("--top", type=int, default=5, metavar="N",
                   help="Limit the report's technical-dashboard and fundamental-"
                        "profile sections to the N strongest names (the screener/"
                        "signal-matrix tables always show every screened ticker) "
                        "(default: %(default)s).")


def _add_backtest_parser(sub) -> None:
    p = sub.add_parser("backtest", help="Backtest the signal engine over history.")
    p.add_argument("--scope", choices=["technical", "composite"], default="technical",
                   help="technical = price-only (no lookahead); composite = full "
                        "Buy/Hold/Watch with TODAY's fundamentals (lookahead-biased).")
    p.add_argument("--universe", default=None, metavar="CSV",
                   help="Run over a cached universe CSV instead of the watchlist "
                        "(reads data/cache/prices.db, no network).")
    p.add_argument("--exits", choices=["horizon", "plan"], default="horizon",
                   help="horizon = fixed-horizon forward returns (what the signal "
                        "led to); plan = walk each entry to its trade-plan stop or "
                        "target and report R-multiples (what you'd have traded). "
                        "plan always enters on the technical gate (default: %(default)s).")
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


def _add_universe_parser(sub) -> None:
    p = sub.add_parser("universe", help="Write an index constituent list to CSV.")
    p.add_argument("--index", choices=["sp500"], default="sp500",
                   help="Which index to pull (default: %(default)s).")
    p.add_argument("--out", default="data/universe_sp500.csv",
                   help="Destination CSV (default: %(default)s).")


def _add_cache_parser(sub) -> None:
    p = sub.add_parser("cache", help="Fetch/refresh the research price cache.")
    p.add_argument("--universe", default="data/universe_sp500.csv",
                   help="Ticker CSV to cache (default: %(default)s).")
    p.add_argument("--period", default="10y",
                   help="History to fetch for tickers not yet cached "
                        "(default: %(default)s).")
    p.add_argument("--refresh-period", default="1mo", metavar="PERIOD",
                   help="Shorter window refetched for already-cached tickers; the "
                        "upsert is idempotent on (ticker, date), so the overlap "
                        "just restates bars (default: %(default)s).")
    p.add_argument("--full", action="store_true",
                   help="Refetch --period for every ticker, ignoring what is "
                        "already cached (use after a data correction).")
    p.add_argument("--db", default=None,
                   help="Cache database path (default: data/cache/prices.db).")
    p.add_argument("--chunk", type=int, default=50,
                   help="Tickers per bulk request (default: %(default)s).")
    p.add_argument("--status", action="store_true",
                   help="Print cache coverage and exit without fetching.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-analysis", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging.")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_backtest_parser(sub)
    _add_universe_parser(sub)
    _add_cache_parser(sub)
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
                save_report=not args.no_report,
                top_n=args.top,
                out_dir=args.out,
                fund_min=args.fund_min,
                account_size=args.account,
                risk_pct=args.risk_pct / 100.0,      # CLI takes percent, API takes a fraction
                max_weight=args.max_weight / 100.0,
            )
        except Exception as e:
            print(f"Pipeline failed: {e}", file=sys.stderr)
            return 1

        n = len(results.signal_matrix)
        print(f"\nDone. Signal matrix: {n} stocks.")
        if results.export_destination:
            print(f"Exported to: {results.export_destination}")
        if results.report_path:
            print(f"Report: {results.report_path}")
        if n:
            buys = results.signal_matrix[
                results.signal_matrix["Final Action Signal"] == "Buy"
            ]["Ticker"].tolist()
            print(f"Buys: {buys or 'none'}")
        return 0

    if args.command == "universe":
        from . import ingest
        rows = ingest.fetch_sp500_universe()
        if not rows:
            print("Universe fetch failed; leaving any existing CSV alone.",
                  file=sys.stderr)
            return 1
        import csv as _csv
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["ticker", "company", "exchange", "sector"])
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} tickers to {out}")
        print("NOTE: current constituents only — delisted names are absent, so a "
              "backtest over this list still carries survivorship bias.")
        return 0

    if args.command == "cache":
        from . import cache as price_cache, ingest
        conn = price_cache.connect(args.db)
        if args.status:
            cov = price_cache.coverage(conn)
            print(cov.to_string(index=False) if not cov.empty else "Cache is empty.")
            if not cov.empty:
                print(f"\n{len(cov)} tickers, {int(cov['Bars'].sum()):,} bars.")
            return 0
        try:
            wl = config.load_watchlist_csv(args.universe)
        except FileNotFoundError as e:
            print(f"{e}\nRun: stock-analysis universe --out {args.universe}",
                  file=sys.stderr)
            return 1
        # Incremental by default: only names with no cached bars need full
        # history. Everything else gets a short top-up window, which the
        # (ticker, date) upsert folds in without duplicating. A nightly refresh
        # is therefore seconds, not a re-download of the whole universe.
        known = set(price_cache.cached_tickers(conn))
        wanted = list(wl)
        fresh = [t for t in wanted if t not in known] if not args.full else wanted
        topup = [t for t in wanted if t in known] if not args.full else []

        bars: dict = {}
        if fresh:
            print(f"Fetching {len(fresh)} new ticker(s) over {args.period}...")
            bars.update(ingest.fetch_bulk_prices(fresh, period=args.period,
                                                 chunk=args.chunk))
        if topup:
            print(f"Topping up {len(topup)} cached ticker(s) over "
                  f"{args.refresh_period}...")
            bars.update(ingest.fetch_bulk_prices(topup, period=args.refresh_period,
                                                 chunk=args.chunk))

        written = sum(price_cache.upsert_bars(conn, tk, df) for tk, df in bars.items())
        print(f"Cached {len(bars)}/{len(wanted)} tickers, {written:,} bars -> "
              f"{args.db or config.DEFAULT_CACHE_DB}")
        return 0

    if args.command == "backtest":
        from . import backtest as bt

        horizons = tuple(args.horizon) if args.horizon else ("1m", "3m", "6m")
        if args.scope == "composite":
            print("⚠ COMPOSITE SCOPE: fundamentals are frozen at TODAY's values, so "
                  "past composites are LOOKAHEAD-BIASED. Treat results as a sanity "
                  "check, not proof of edge.")
        prices = None
        if args.universe:
            from . import cache as price_cache
            wl = config.load_watchlist_csv(args.universe)
            with price_cache.connect() as conn:
                prices = price_cache.load_universe(conn, list(wl))
            if not prices:
                print(f"No cached bars for {args.universe}. Run: "
                      f"stock-analysis cache --universe {args.universe}", file=sys.stderr)
                return 1
            print(f"Loaded {len(prices)} cached tickers (no network).")
        try:
            results = bt.run_backtest(
                exits=args.exits, prices=prices,
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
        ts = results.trade_stats or {}
        if ts.get("n"):
            mix = "  ".join(f"{k}:{v}" for k, v in sorted(ts["exit_mix"].items()))
            print(f"  Planned trades: {ts['n']}   win rate {ts['win_rate']:.1%}   "
                  f"expectancy {ts['expectancy_r']:+.2f}R")
            print(f"    avg win {ts['avg_win_r']:+.2f}R   avg loss {ts['avg_loss_r']:+.2f}R"
                  f"   total {ts['total_r']:+.1f}R   avg hold {ts['avg_bars_held']:.0f} bars")
            print(f"    exits: {mix}")
        elif results.config.get("exits") == "plan":
            print("  Planned trades: none (no gate entries with a usable plan).")
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
