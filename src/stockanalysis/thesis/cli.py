"""The ``stock-analysis thesis ...`` subcommand: a thin CLI over the thesis API.

Kept in the thesis subpackage (rather than bloating the top-level ``cli.py``)
so the command surface lives next to the logic it drives. The main CLI wires it
in with :func:`add_parser` (build the parser tree) and :func:`dispatch` (run an
action), mirroring the ``run`` / ``backtest`` pattern.

Each action maps to one library call; output is plain text (presentation stays
out of the package core). ``--state-dir`` defaults to ``config.DEFAULT_THESES_DIR``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .. import config
from ..outputs.base import SIGNAL_MATRIX_SHEET
from . import review, sources, store


def add_parser(sub) -> None:
    p = sub.add_parser("thesis", help="Track investment theses (register, review, postmortem).")
    actions = p.add_subparsers(dest="thesis_cmd", required=True)

    # --state-dir is shared by every action and may appear after the action name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", default=None,
                        help="Thesis state directory (default: data/theses/).")

    def _action(name, **kw):
        return actions.add_parser(name, parents=[common], **kw)

    ing = _action("ingest", help="Register IDEA theses from a run's signal_matrix.")
    src = ing.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-run", help="A run dir containing signal_matrix.xlsx.")
    src.add_argument("--from-latest", action="store_true",
                     help="Use the newest output/<timestamp>/ run.")
    ing.add_argument("--out", default="output", help="Base output dir for --from-latest.")
    ing.add_argument("--actions", default="Buy",
                     help="Comma list of action signals to ingest (default: Buy).")
    ing.add_argument("--type", default="long_term_value", help="Default thesis type.")

    reg = _action("register", help="Register a manual thesis idea.")
    reg.add_argument("--ticker", required=True)
    reg.add_argument("--type", required=True, dest="thesis_type")
    reg.add_argument("--statement", required=True)
    reg.add_argument("--target-price", type=float, default=None)
    reg.add_argument("--stop", type=float, default=None)
    reg.add_argument("--target-profit", type=float, default=None)
    reg.add_argument("--entry-date", default=None, help="Backdate the idea (YYYY-MM-DD).")
    reg.add_argument("--shares", type=float, default=None, help="Planned size (kept as provenance).")
    reg.add_argument("--review-days", type=int, default=None)

    lst = _action("list", help="List theses (optionally filtered).")
    lst.add_argument("--ticker", default=None)
    lst.add_argument("--status", default=None)
    lst.add_argument("--type", default=None, dest="thesis_type")

    _action("show", help="Print one thesis as JSON.").add_argument("thesis_id")

    tr = _action("transition", help="Forward status move (e.g. IDEA->ENTRY_READY).")
    tr.add_argument("thesis_id")
    tr.add_argument("new_status")
    tr.add_argument("--reason", default="manual transition")
    tr.add_argument("--event-date", default=None)

    op = _action("open", help="Open a position (ENTRY_READY->ACTIVE).")
    op.add_argument("thesis_id")
    op.add_argument("--price", type=float, required=True)
    op.add_argument("--date", required=True)
    op.add_argument("--shares", type=float, default=None)
    op.add_argument("--reason", default="position opened")

    tm = _action("trim", help="Partial close.")
    tm.add_argument("thesis_id")
    tm.add_argument("--shares", type=float, required=True)
    tm.add_argument("--price", type=float, required=True)
    tm.add_argument("--date", required=True)
    tm.add_argument("--exit-reason", default=None)

    cl = _action("close", help="Close the full remaining position.")
    cl.add_argument("thesis_id")
    cl.add_argument("--reason", required=True, help="Exit reason (e.g. target_hit, stop_hit).")
    cl.add_argument("--price", type=float, required=True)
    cl.add_argument("--date", required=True)

    iv = _action("invalidate", help="Kill a thesis (-> INVALIDATED).")
    iv.add_argument("thesis_id")
    iv.add_argument("--reason", required=True)
    iv.add_argument("--price", type=float, default=None)
    iv.add_argument("--date", default=None)

    rd = _action("review-due", help="List theses due for review.")
    rd.add_argument("--as-of", default=None, help="Date to evaluate against (default: today).")

    rv = _action("reviewed", help="Record a review and advance the next date.")
    rv.add_argument("thesis_id")
    rv.add_argument("--date", required=True)
    rv.add_argument("--outcome", default="OK", choices=["OK", "WARN", "REVIEW"])
    rv.add_argument("--notes", default=None)

    pm = _action("postmortem", help="Write a postmortem for a closed thesis.")
    pm.add_argument("thesis_id")
    pm.add_argument("--no-prices", action="store_true", help="Skip MAE/MFE (no network).")
    pm.add_argument("--journal-dir", default=None)

    _action("summary", help="Aggregate realized performance.")
    _action("doctor", help="Validate state vs index.")
    _action("reindex", help="Rebuild _index.json from thesis files.")


def _state_dir(args) -> Path:
    return Path(args.state_dir) if args.state_dir else config.DEFAULT_THESES_DIR


def _latest_run(out_base: str) -> Path:
    # Lexical sort == chronological because pipeline.run_output_dir names run dirs
    # with a zero-padded "%Y-%m-%d_%H%M%S" timestamp (pipeline.RUN_DIR_FMT).
    runs = sorted(p for p in Path(out_base).glob("*") if (p / "signal_matrix.xlsx").exists())
    if not runs:
        raise FileNotFoundError(f"no run with signal_matrix.xlsx under {out_base}/")
    return runs[-1]


def _fmt_row(t: dict) -> str:
    m = t["monitoring"]
    return (f"{t['thesis_id']:<28} {t['ticker']:<8} {t['status']:<16} "
            f"{t['thesis_type']:<16} review:{m.get('next_review_date')}")


def dispatch(args) -> int:
    state = _state_dir(args)
    cmd = args.thesis_cmd

    if cmd == "ingest":
        run_dir = Path(args.from_run) if args.from_run else _latest_run(args.out)
        xlsx = run_dir / "signal_matrix.xlsx"
        sm = pd.read_excel(xlsx, sheet_name=SIGNAL_MATRIX_SHEET)
        ids = sources.from_signal_matrix(
            state, sm, actions=tuple(a.strip() for a in args.actions.split(",")),
            default_type=args.type, run_file=str(xlsx))
        print(f"Registered {len(ids)} thesis(es) from {xlsx}")
        for tid in ids:
            print(f"  {tid}")
        return 0

    if cmd == "register":
        tid = sources.from_manual(state, {
            "ticker": args.ticker, "thesis_type": args.thesis_type,
            "thesis_statement": args.statement, "target_price": args.target_price,
            "stop_price": args.stop, "target_profit": args.target_profit,
            "entry_date": args.entry_date, "shares": args.shares,
            "review_interval_days": args.review_days})
        print(f"Registered {tid}")
        return 0

    if cmd == "list":
        rows = store.query(state, ticker=args.ticker, status=args.status,
                           thesis_type=args.thesis_type)
        if not rows:
            print("(no theses)")
        for t in rows:
            print(_fmt_row(t))
        return 0

    if cmd == "show":
        print(json.dumps(store.get(state, args.thesis_id), indent=2, sort_keys=True))
        return 0

    if cmd == "transition":
        th = store.transition(state, args.thesis_id, args.new_status, reason=args.reason,
                              event_date=args.event_date)
        print(f"{th['thesis_id']} -> {th['status']}")
        return 0

    if cmd == "open":
        th = store.open_position(state, args.thesis_id, actual_price=args.price,
                                 actual_date=args.date, shares=args.shares, reason=args.reason)
        print(f"{th['thesis_id']} -> {th['status']} ({th['position']['shares']} sh @ {args.price})")
        return 0

    if cmd == "trim":
        th = store.trim(state, args.thesis_id, shares_sold=args.shares, price=args.price,
                        date=args.date, exit_reason=args.exit_reason)
        print(f"{th['thesis_id']} -> {th['status']} "
              f"(remaining {th['position']['shares_remaining']})")
        return 0

    if cmd == "close":
        th = store.close(state, args.thesis_id, exit_reason=args.reason,
                         actual_price=args.price, actual_date=args.date)
        print(f"{th['thesis_id']} -> CLOSED  P&L ${th['outcome']['pnl_dollars']} "
              f"({th['outcome']['pnl_pct']}%)")
        return 0

    if cmd == "invalidate":
        th = store.terminate(state, args.thesis_id, terminal_status="INVALIDATED",
                             exit_reason=args.reason, actual_price=args.price,
                             actual_date=args.date)
        print(f"{th['thesis_id']} -> INVALIDATED ({args.reason})")
        return 0

    if cmd == "review-due":
        from datetime import date as _date
        as_of = args.as_of or _date.today().isoformat()
        due = store.list_review_due(state, as_of=as_of)
        if not due:
            print(f"(nothing due as of {as_of})")
        for t in due:
            print(_fmt_row(t))
        return 0

    if cmd == "reviewed":
        th = store.mark_reviewed(state, args.thesis_id, review_date=args.date,
                                 outcome=args.outcome, notes=args.notes)
        print(f"{th['thesis_id']} reviewed; next {th['monitoring']['next_review_date']}")
        return 0

    if cmd == "postmortem":
        adapter = None if args.no_prices else review.YFinancePriceAdapter()
        path = review.generate_postmortem(state, args.thesis_id, price_adapter=adapter,
                                          journal_dir=args.journal_dir)
        print(f"Postmortem written: {path}")
        return 0

    if cmd == "summary":
        s = review.summary_stats(state)
        print(json.dumps(s, indent=2, sort_keys=True))
        return 0

    if cmd == "doctor":
        report = store.validate_state(state)
        print("OK" if report["valid"] else "INVALID")
        for err in report["errors"]:
            print(f"  - {err}")
        return 0 if report["valid"] else 1

    if cmd == "reindex":
        idx = store.rebuild_index(state)
        print(f"Reindexed {len(idx['theses'])} thesis(es).")
        return 0

    return 1
