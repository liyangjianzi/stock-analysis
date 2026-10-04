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
from . import engine, inputs, optimize, refund, report, scenarios


def add_parser(sub) -> None:
    p = sub.add_parser("retire", help="Canadian retirement plan: chance of success, legacy, "
                                      "yearly income by source.")
    p.add_argument("--inputs", default=None, help="plan.json (default: retirement/plan.json).")
    p.add_argument("--holdings", default=None,
                   help="Holdings file for live balances (overrides plan.json 'balances').")
    p.add_argument("--out", default=None, help="Output root (default: retirement/output/).")
    p.add_argument("--paths", type=int, default=None,
                   help="Simulated futures (default: plan.json returns.paths).")
    p.add_argument("--scenario-paths", type=int, default=None,
                   help="Futures per what-if suggestion (default: the same as --paths, so the "
                        "suggestions' current-plan row matches the gauge).")
    p.add_argument("--seed", type=int, default=None, help="Random seed (default: plan.json).")
    p.add_argument("--bank", default=None,
                   help="Bank CSV file or folder for the tax-refund check (default: retirement/bank/).")
    p.add_argument("--init", action="store_true", help="Write a starter plan.json and exit.")
    p.add_argument("--optimize", action="store_true",
                   help="Print the highest safe spending, the earliest safe retirement and the "
                        "best CPP/OAS start ages, then exit.")
    p.add_argument("--target", type=float, default=90.0,
                   help="With --optimize: the chance the money lasts to aim for, in percent "
                        "(default 90).")
    p.add_argument("--gui", action="store_true",
                   help="Open a local web page to edit plan.json and generate the report.")
    p.add_argument("--port", type=int, default=8765, help="Port for --gui (default 8765).")
    p.add_argument("--no-browser", action="store_true", help="With --gui: don't open the browser.")


def _load_book(holdings_path):
    try:
        return holdings.load(holdings_path)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"{e}; add 'balances' to plan.json or pass --holdings") from e


def _with_balances(plan, holdings_path, load=_load_book):
    """Pick the balances and say where they came from. ``load`` reads the holdings
    file only when it's needed (the GUI passes a cached one)."""
    if holdings_path is None and plan.accounts:
        return plan, "plan.json balances"
    book = load(holdings_path)
    source = f"{Path(book['path']).name}, saved {book['saved_at']:%Y-%m-%d %H:%M}"
    return inputs.with_holdings(plan, book["holdings"]), source


def generate(plan, source, *, paths=None, scenario_paths=None, seed=None, out_root=None,
             bank=None):
    """Run the plan and its what-ifs, write the report + summary; return (report path, result).
    ``bank``: bank CSVs (file or folder) for the tax-refund check; None skips it."""
    seed = plan.returns.seed if seed is None else seed
    result = engine.run(plan, paths=paths, seed=seed)
    baseline, ranked = scenarios.rank(plan, seed=seed,
                                      paths=scenario_paths or result.simulated.paths)
    root = Path(out_root) if out_root else config.DEFAULT_RETIREMENT_OUT
    previous = report.latest_summary(root)
    now = dt.datetime.now()
    run_dir = root / now.strftime("%Y-%m-%d_%H%M%S")
    html_doc = report.build_report(result, baseline, ranked, generated_at=now.strftime("%Y-%m-%d %H:%M"),
                                   holdings_source=source, previous=previous,
                                   refunds=refund.actual_refunds(bank) if bank else None)
    out = report.save_report(html_doc, run_dir / "retirement_report.html")
    report.write_summary(result, run_dir / "summary.json")
    return Path(out), result


def dispatch(args) -> int:
    path = Path(args.inputs) if args.inputs else config.DEFAULT_RETIREMENT_INPUTS
    if args.init:
        inputs.write_template(path)
        print(f"Wrote a starter plan with example values: {path}")
        return 0
    if args.gui:
        from . import gui
        gui.serve(path, port=args.port, open_browser=not args.no_browser,
                  holdings_path=args.holdings, out_root=args.out,
                  report_paths=args.paths, scenario_paths=args.scenario_paths, bank=args.bank)
        return 0
    plan, source = _with_balances(inputs.load_inputs(path), args.holdings)
    if args.optimize:
        return _print_optimize(plan, args.target / 100, paths=args.paths or 1_000, seed=args.seed)
    out, result = generate(plan, source, paths=args.paths, scenario_paths=args.scenario_paths,
                           bank=args.bank or config.DEFAULT_RETIREMENT_BANK,
                           seed=args.seed, out_root=args.out)
    avg = result.average
    print(f"{report.lasts_label(plan)}: {result.simulated.success:.0%}")
    print(f"Legacy (average future): C${avg.legacy[0]:,.0f}")
    print(f"Lifetime taxes (average future): C${avg.lifetime_tax[0]:,.0f}")
    print(f"Report: {out}")
    return 0


def _print_optimize(plan, target: float, *, paths: int, seed) -> int:
    a = optimize.affordability(plan, target=target, paths=paths, seed=seed)
    print(f"Chance the money lasts now: {a.success:.0%} (target {target:.0%}, {paths:,} futures)")
    if a.max_spending is None:
        print("Highest spending: none reaches the target, even with no spending")
    else:
        print(f"Highest spending: C${a.max_spending:,.0f}/yr ({a.max_spending_success:.0%}); "
              f"now C${a.spending:,.0f}")
    if a.retire_shift is None:
        print("Earliest retirement: none reaches the target")
    else:
        when = ", ".join(f"{name} at {age}" for name, age in a.retire_ages)
        shift = ("as planned" if a.retire_shift == 0 else
                 f"{abs(a.retire_shift)} year{'s' if abs(a.retire_shift) != 1 else ''} "
                 f"{'earlier' if a.retire_shift < 0 else 'later'}")
        print(f"Earliest retirement: {when} ({shift}; {a.retire_success:.0%})")
    b = optimize.best_benefit_ages(plan, paths=paths, seed=seed)
    for c in b.people:
        print(f"{c.name}: CPP {c.current[0]} -> {c.best[0]}, OAS {c.current[1]} -> {c.best[1]}")
    print(f"Expected legacy (over lifespans): C${b.legacy:,.0f} -> C${b.best_legacy:,.0f}; "
          f"chance the money lasts {b.success:.0%} -> {b.best_success:.0%}")
    d = optimize.rrsp_drawdown(plan, paths=paths, seed=seed)
    now = d.current
    print(f"RRSP draw ({now.label}): legacy C${now.legacy:,.0f}, lifetime tax C${now.lifetime_tax:,.0f}, "
          f"{now.success:.0%}")
    for goal, name in (("legacy", "most legacy"), ("tax", "least lifetime tax"), ("success", "safest")):
        r = d.best(goal)
        print(f"RRSP draw for {name}: up to C${r.target:,.0f} each -> legacy C${r.legacy:,.0f}, "
              f"lifetime tax C${r.lifetime_tax:,.0f}, {r.success:.0%}")
    return 0
