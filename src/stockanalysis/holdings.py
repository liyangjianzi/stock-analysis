"""Real portfolio holdings, read from the owner's Google Sheet.

The sheet's **Details** tab lists one row per (account, ticker): shares, price,
purchase price, value in the native currency and in CAD, and the currency.
:func:`parse_holdings` turns those rows into a tidy frame. The sheet is read from
exports (there is no Google Cloud service account, by the owner's choice):
:func:`load_holdings_csv` parses a CSV of the Details tab, and
:func:`load_workbook` an .xlsx of the whole sheet — Details plus **History** (a
daily household total in CAD, :func:`parse_history`) and **Touzi**
(contributions, :func:`parse_contributions`) for the drawdown. :func:`load`
picks the file and the loader.

**Privacy.** The repository is public. Holdings are personal financial data, so
they are only ever written to :data:`DEFAULT_SNAPSHOT` (gitignored, pinned by
``tests/test_privacy.py``) or into ``output/``, never into tracked files. Tests
use invented positions.

Following the package rule, parsing degrades rather than raises: an unreadable
number is NaN, a row without a ticker is skipped, a sheet without the header
yields an empty frame. A missing file raises, because a silent empty portfolio
would read as "no risk".
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
#: Local snapshot of the sheet (gitignored via ``data/holdings*``).
DEFAULT_SNAPSHOT = _ROOT / "data" / "holdings_snapshot.csv"
#: .xlsx export of the whole sheet, for the History / Touzi tabs (gitignored).
DEFAULT_WORKBOOK = _ROOT / "data" / "holdings_workbook.xlsx"
#: Optional sheet-ticker -> Yahoo-symbol fixes. Gitignored like the rest: every
#: entry would name a holding.
DEFAULT_TICKER_MAP = _ROOT / "data" / "holdings_ticker_map.csv"

#: Sheet header (stripped) -> frame column. The account column's header is the
#: sheet's placeholder "Column 1", so it is taken by position instead.
_COLUMNS = {
    "Ticker": "ticker", "Shares": "shares", "Current Price": "price",
    "Purchase Price": "cost", "Total Value": "value", "Total Value CAD": "value_cad",
    "Currency": "currency",
}
FRAME_COLUMNS = ["account", "ticker", "yahoo", "kind", "shares", "price", "cost",
                 "currency", "value", "value_cad"]

_NUMBER = re.compile(r"[^0-9.\-]")


def parse_number(text) -> float:
    """``"12,345"`` -> 12345.0, ``"2.50%"`` -> 2.5, ``"$98,765.43"`` -> 98765.43;
    blank, ``#DIV/0!`` and other non-numbers -> NaN."""
    if text is None or str(text).strip().startswith("#"):   # #DIV/0!, #N/A, #REF!
        return np.nan
    cleaned = _NUMBER.sub("", str(text))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else np.nan
    except ValueError:
        return np.nan


def to_yahoo(ticker: str, currency: str, overrides: dict | None = None) -> str:
    """Sheet ticker -> Yahoo symbol.

    Rules, checked against the live sheet on 2026-09-27 (every price matched):
    an ``EXCH:`` prefix is Google Finance style (``TSE:RY`` -> ``RY.TO``);
    ``X.U`` is a USD-traded TSX unit (``XUS.U`` -> ``XUS-U.TO``); any other CAD
    row is a TSX listing (``XIC`` -> ``XIC.TO``); a US share class uses a dash
    (``BF.B`` -> ``BF-B``). ``overrides`` wins over all of them.
    """
    overrides = overrides or {}
    if ticker in overrides:
        return overrides[ticker]
    exchange, _, symbol = ticker.rpartition(":")
    if exchange:
        return (symbol.replace(".", "-") + ".TO" if exchange.upper() in ("TSE", "TSX")
                else symbol.replace(".", "-"))
    if ticker.upper().endswith(".U"):
        return ticker[:-2].replace(".", "-") + "-U.TO"
    if str(currency).upper() == "CAD":
        return ticker.replace(".", "-") + ".TO"
    return ticker.replace(".", "-")


def _text(value) -> str:
    """A cell as stripped text; empty for None / NaN (an .xlsx blank)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value).strip()


def _kind(ticker: str, shares: float) -> str:
    # The sheet's cash lines are Cash_CAD / CASH_USD / Cash_interest_*; a real
    # ticker that merely starts with "cash" is not one of them.
    if ticker.casefold().startswith("cash_"):
        return "cash"
    return "stock" if np.isfinite(shares) and shares > 0 else "other"


def parse_holdings(rows, overrides: dict | None = None) -> pd.DataFrame:
    """Rows of the Details tab (header anywhere near the top) -> one row per
    position, :data:`FRAME_COLUMNS`.

    ``kind`` is ``cash`` (the sheet's ``Cash_*`` lines, even when they carry units),
    ``stock`` (a position with shares — the only kind with a ``yahoo`` symbol),
    or ``other`` (a value without shares, e.g. an insurance product: kept at the
    sheet's value, but there is no price history to measure). Rows without a
    ticker (the totals row) are skipped.
    """
    rows = [list(r) for r in (rows or [])]
    at = next((i for i, r in enumerate(rows) if len(r) > 1 and _text(r[1]) == "Ticker"), None)
    if at is None:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    header = [_text(h) for h in rows[at]]
    where = {col: header.index(name) for name, col in _COLUMNS.items() if name in header}

    def cell(row, col):
        i = where.get(col)
        return row[i] if i is not None and i < len(row) else ""

    records = []
    for row in rows[at + 1:]:
        ticker = _text(cell(row, "ticker"))
        if not ticker:
            continue
        currency = _text(cell(row, "currency")).upper()
        shares = parse_number(cell(row, "shares"))
        kind = _kind(ticker, shares)
        records.append({
            "account": _text(row[0]) if row else "",
            "ticker": ticker,
            "yahoo": to_yahoo(ticker, currency, overrides) if kind == "stock" else None,
            "kind": kind,
            "shares": shares,
            "price": parse_number(cell(row, "price")),
            "cost": parse_number(cell(row, "cost")),
            "currency": currency,
            "value": parse_number(cell(row, "value")),
            "value_cad": parse_number(cell(row, "value_cad")),
        })
    return pd.DataFrame(records, columns=FRAME_COLUMNS)


def parse_history(frame: pd.DataFrame) -> pd.Series:
    """History tab (A: timestamp, B: household total in CAD) -> a daily Series.
    Rows without a timestamp or a numeric total (the header) are skipped; a day
    recorded twice keeps its last value."""
    out = {}
    for when, total in frame.iloc[:, :2].itertuples(index=False):
        value = parse_number(total) if not isinstance(total, (int, float)) else float(total)
        if isinstance(when, (pd.Timestamp, dt.datetime)) and np.isfinite(value):
            out[pd.Timestamp(when).normalize()] = value
    return pd.Series(out, dtype=float).sort_index()


def _contribution_date(value):
    """Touzi dates are *typed* month-first but the sheet's locale is day-first:
    when both parts are <= 12 Sheets stores a date with day and month swapped
    ("12/08/25", 8 Dec, becomes 12 Aug), and otherwise keeps the text. Swapping
    back puts every row in the tab's own order (checked 2026-09-27)."""
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        ts = pd.Timestamp(value)
        return pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
    text = _text(value)
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return pd.Timestamp(dt.datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def parse_contributions(frame: pd.DataFrame) -> pd.Series:
    """Touzi tab -> new money per day, in CAD (A: date, B: amount, C: CAD
    amount, E: currency, F: CAD amount on some USD rows). Same-day rows are
    summed; the totals row (no date) is skipped."""
    out: dict = {}
    seen: list = []
    for row in frame.itertuples(index=False):
        when = _contribution_date(row[0])
        if when is None:
            continue
        seen.append(when)
        amount = next((float(v) for v in (row[2], row[5] if len(row) > 5 else None)
                       if isinstance(v, (int, float)) and np.isfinite(v)), np.nan)
        if not np.isfinite(amount) and _text(row[4]).upper() == "CAD":
            amount = parse_number(row[1])
        if np.isfinite(amount):
            out[when] = out.get(when, 0.0) + amount
    if any(b < a for a, b in zip(seen, seen[1:])):
        log.warning("Touzi dates are out of order after the day/month unswap — the "
                    "sheet's date entry may have changed; check the contributions.")
    return pd.Series(out, dtype=float).sort_index()


def load_workbook(path=DEFAULT_WORKBOOK, *, overrides: dict | None = None) -> dict:
    """An .xlsx export of the whole sheet -> ``{holdings, history, contributions}``."""
    overrides = load_ticker_map() if overrides is None else overrides
    book = pd.ExcelFile(path)
    details = pd.read_excel(book, "Details", header=None, usecols="A:M")
    return {
        "holdings": parse_holdings(details.values.tolist(), overrides),
        "history": parse_history(pd.read_excel(book, "History", header=None, usecols="A:B")),
        "contributions": parse_contributions(
            pd.read_excel(book, "Touzi", header=None, usecols="A:F")),
    }


def load_ticker_map(path=DEFAULT_TICKER_MAP) -> dict[str, str]:
    """``sheet_ticker -> yahoo`` fixes from a CSV; ``{}`` when the file is absent."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["sheet_ticker"].strip(): r["yahoo"].strip()
                for r in csv.DictReader(f) if r.get("sheet_ticker", "").strip()}


def load_holdings_csv(path=DEFAULT_SNAPSHOT, *, overrides: dict | None = None) -> pd.DataFrame:
    """Holdings from a CSV export of the Details tab."""
    overrides = load_ticker_map() if overrides is None else overrides
    with open(path, newline="", encoding="utf-8") as f:
        return parse_holdings(list(csv.reader(f)), overrides)


_CAD_SUFFIXES = (".TO", ".V", ".CN", ".NE")


def listing_currency(symbol: str) -> str:
    """A Yahoo symbol's trading currency: Canadian exchanges trade in CAD except
    their USD-traded units (``XUS-U.TO``); everything else here is USD. The
    inverse of :func:`to_yahoo`'s rules, kept beside them."""
    s = str(symbol).upper()
    if s.endswith(_CAD_SUFFIXES):
        return "USD" if s.rsplit(".", 1)[0].endswith("-U") else "CAD"
    return "USD"


def load(path=None) -> dict:
    """The holdings file and what's in it: ``{holdings, history, contributions,
    path, saved_at}`` (``history`` / ``contributions`` are None for a CSV).

    ``path`` falls back to ``$HOLDINGS_FILE``, then :data:`DEFAULT_WORKBOOK` if
    it exists, else :data:`DEFAULT_SNAPSHOT`; an .xlsx is read with
    :func:`load_workbook`, anything else as a Details CSV.
    """
    if path is None:
        path = os.getenv("HOLDINGS_FILE") or (
            DEFAULT_WORKBOOK if DEFAULT_WORKBOOK.exists() else DEFAULT_SNAPSHOT)
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No holdings file at {path}")
    if path.suffix.lower() == ".xlsx":
        book = load_workbook(path)
    else:
        book = {"holdings": load_holdings_csv(path), "history": None, "contributions": None}
    return {**book, "path": path, "saved_at": dt.datetime.fromtimestamp(path.stat().st_mtime)}
