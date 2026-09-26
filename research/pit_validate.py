"""Sanity-check the point-in-time metrics against today's live yfinance values.

Prices the latest snapshot for every watchlist name that is also in the S&P 500
universe and prints it beside the ``Fundamentals`` sheet of a live run. The two
will not match exactly -- XBRL tags are not Yahoo's definitions (see
pit_fundamentals) -- but a metric that is off by an order of magnitude, or a
score that disagrees on most names, means the rebuild is broken.

Dividend yield is compared against ``trailingAnnualDividendYield`` fetched fresh,
because the sheet's ``Div_Yield`` carries the live percent/fraction bug.

    python pit_validate.py [path/to/signal_matrix.xlsx]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from pit_fundamentals import load, metrics_at
from stockanalysis.screener import screen_fundamentals

HERE = Path(__file__).resolve().parent
COLS = ["PE", "EPS_Growth", "Rev_Growth", "Debt_Equity", "Div_Yield", "FCF"]


def main(sheet: Path):
    import yfinance as yf
    live = pd.read_excel(sheet, sheet_name="Fundamentals").set_index("Ticker")
    _, snaps, prices = load()
    names = [t for t in live.index if t in snaps]
    asof = max(p.index.max() for p in prices.values()) + pd.Timedelta(days=1)
    pit = metrics_at(pd.DataFrame({"Ticker": names, "date": asof}), snaps, prices).set_index("Ticker")
    pit_scored = screen_fundamentals(pit[COLS])

    trailing = {t: yf.Ticker(t).info.get("trailingAnnualDividendYield", np.nan) for t in names}
    live = live.assign(Div_Yield=pd.Series(trailing))
    live_scored = screen_fundamentals(live[COLS])

    rows = []
    for t in names:
        r = {"Ticker": t}
        for c in COLS:
            r[f"{c}_live"], r[f"{c}_pit"] = live.at[t, c], pit.at[t, c]
        r["score_live"] = live_scored.at[t, "Fundamental_Score"]
        r["score_pit"] = pit_scored.at[t, "Fundamental_Score"]
        rows.append(r)
    out = pd.DataFrame(rows).set_index("Ticker")
    pd.set_option("display.width", 250)
    for c in COLS:
        print(out[[f"{c}_live", f"{c}_pit"]].round(4).to_string(), "\n")
    passes = [f"Pass_{k}" for k in ("PE", "EPS", "Rev", "DE", "Div", "FCF")]
    agree = (live_scored.loc[names, passes] == pit_scored.loc[names, passes]).mean()
    print("per-test agreement (live vs PIT):\n" + agree.round(2).to_string())
    print("\nscores:\n" + out[["score_live", "score_pit"]].to_string())
    print(f"\nscore exact-match {np.mean(out.score_live == out.score_pit):.0%}, "
          f"within 1 {np.mean(abs(out.score_live - out.score_pit) <= 1):.0%}, "
          f">=4 agreement {np.mean((out.score_live >= 4) == (out.score_pit >= 4)):.0%}")


if __name__ == "__main__":
    runs = sorted((HERE.parent / "output").glob("20*/signal_matrix.xlsx"))
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else runs[-1])
