"""The ``stock-analysis retire`` subcommand: a thin CLI over the retirement API.

Kept next to the logic it drives (like ``thesis/cli.py``). The main CLI wires it
in with :func:`add_parser` and :func:`dispatch`.

Balances come from ``--holdings`` if given, then plan.json's ``balances``, then
the default holdings file (``holdings.load()``).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .. import config, holdings
from . import engine, inputs, report, scenarios


def add_parser(sub) -> None:
    p = sub.add_parser("retire", help="Canadian retirement plan: chance of success, legacy, "
                                      "yearly income by source.")
    p.add_argument("--inputs", default=None, help="plan.json (default: retirement/plan.json).")
    p.add_argument("--holdings", default=None,
                   help="Holdings file for live balances (overrides plan.json 'balances').")
    p.add_argument("--out", default=None, help="Output root (default: retirement/output/).")
    p.add_argument("--paths", type=int, default=None,
                   help="Simulated futures (default: plan.json returns.paths).")
    p.add_argument("--scenario-paths", type=int, default=2000,
                   help="Futures per what-if suggestion (default 2000).")
    p.add_argument("--seed", type=int, default=None, help="Random seed (default: plan.json).")
    p.add_argument("--init", action="store_true", help="Write a starter plan.json and exit.")


def _with_balances(plan, holdings_path):
    """Pick the balances and say where they came from."""
    if holdings_path is None and plan.accounts:
        return plan, "plan.json balances"
    try:
        book = holdings.load(holdings_path)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"{e}; add 'balances' to plan.json or pass --holdings") from e
    source = f"{Path(book['path']).name}, saved {book['saved_at']:%Y-%m-%d %H:%M}"
    return inputs.with_holdings(plan, book["holdings"]), source


def dispatch(args) -> int:
    path = Path(args.inputs) if args.inputs else config.DEFAULT_RETIREMENT_INPUTS
    if args.init:
        inputs.write_template(path)
        print(f"Wrote a starter plan with example values: {path}")
        return 0
    plan, source = _with_balances(inputs.load_inputs(path), args.holdings)
    seed = plan.returns.seed if args.seed is None else args.seed
    result = engine.run(plan, paths=args.paths, seed=seed)
    baseline, ranked = scenarios.rank(plan, paths=args.scenario_paths, seed=seed)
    root = Path(args.out) if args.out else config.DEFAULT_RETIREMENT_OUT
    previous = report.latest_summary(root)
    now = dt.datetime.now()
    run_dir = root / now.strftime("%Y-%m-%d_%H%M%S")
    html_doc = report.build_report(result, baseline, ranked, generated_at=now.strftime("%Y-%m-%d %H:%M"),
                                   holdings_source=source, previous=previous)
    out = report.save_report(html_doc, run_dir / "retirement_report.html")
    report.write_summary(result, run_dir / "summary.json")
    avg = result.average
    print(f"Chance the money lasts to {plan.end_age}: {result.simulated.success:.0%}")
    print(f"Legacy (average future): C${avg.legacy[0]:,.0f}")
    print(f"Lifetime taxes (average future): C${avg.lifetime_tax[0]:,.0f}")
    print(f"Report: {out}")
    return 0
