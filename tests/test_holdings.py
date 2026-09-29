"""Tests for holdings.py — parsing the holdings sheet, fully offline.

Every position here is invented. The repo is public: never paste real holdings
into a test. The rows only mirror the sheet's *shape* (the Details tab's A:M
header, thousands separators, cash rows, a totals row).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis import holdings
from stockanalysis.holdings import parse_holdings, parse_number, to_yahoo

HEADER = ["Column 1", "Ticker", "Shares", "Current Price", "Purchase Price",
          "Total Value ", "Total Value CAD", "Total Cost", "Gain/Loss",
          "Gain/Loss CAD", "Currency", "Gain/Loss (%)", "Weight"]


def _rows():
    return [
        HEADER,
        ["Acct A USD", "KO", "10", "100.00", "50.00", "1,000", "1,400", "500",
         "500", "700", "USD", "100.00", "10.00%"],
        ["Acct A USD", "Cash_interest_USD", "50", "10", "10.00", "500", "700", "500",
         "0", "0", "USD", "0.00", "5.00%"],
        ["Acct B CAD", "XIC", "100", "70.00", "40.00", "7,000", "7,000", "4,000",
         "3,000", "3,000", "CAD", "75.00", "50.00%"],
        ["Acct B CAD", "Cash_CAD", "", "", "", "2,000", "2,000", "0", "0", "0", "CAD", "", "14.29%"],
        ["Acct B CAD", "TSE:RY", "5", "200.0", "80.00", "1,000", "1,000", "400",
         "600", "600", "CAD", "", "7.14%"],
        ["Acct C", "POLICY", "", "", "", "1,500", "1,500", "1,500", "0", "0", "CAD", "", "10.71%"],
        ["", "", "", "", "", "13,000", "13,600", "", "", "", "", "", "1.00"],   # totals row
    ]


# --- numbers --------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("12,345", 12345.0), ("2.50%", 2.5), ("-75", -75.0), ("$98,765.43", 98765.43),
    ("50.12", 50.12), (" 7 ", 7.0),
])
def test_parse_number(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "   ", "#DIV/0!", "n/a", None])
def test_unparseable_numbers_are_nan(text):
    assert np.isnan(parse_number(text))


# --- ticker mapping ---------------------------------------------------------------

@pytest.mark.parametrize("ticker, currency, expected", [
    ("KO", "USD", "KO"),
    ("BF.B", "USD", "BF-B"),              # US share class
    ("XIC", "CAD", "XIC.TO"),             # a CAD row is a TSX listing
    ("XUS.U", "USD", "XUS-U.TO"),         # USD-traded units on the TSX
    ("TSE:RY", "CAD", "RY.TO"),           # Google Finance exchange prefix
    ("NASDAQ:CSCO", "USD", "CSCO"),
])
def test_to_yahoo(ticker, currency, expected):
    assert to_yahoo(ticker, currency) == expected


def test_overrides_win_over_the_rules():
    assert to_yahoo("XIC", "CAD", {"XIC": "XIU.TO"}) == "XIU.TO"


# --- parse_holdings -------------------------------------------------------------------

def test_parse_holdings_reads_every_position_and_skips_the_totals_row():
    df = parse_holdings(_rows())
    assert list(df["ticker"]) == ["KO", "Cash_interest_USD", "XIC", "Cash_CAD",
                                  "TSE:RY", "POLICY"]
    assert df.loc[df["ticker"] == "XIC", "value_cad"].item() == pytest.approx(7000.0)
    assert df.loc[df["ticker"] == "KO", "shares"].item() == pytest.approx(10.0)


def test_cash_rows_are_cash_even_when_they_carry_units():
    df = parse_holdings(_rows()).set_index("ticker")
    assert df.loc["Cash_interest_USD", "kind"] == "cash"
    assert df.loc["Cash_CAD", "kind"] == "cash"
    assert pd.isna(df.loc["Cash_CAD", "yahoo"])        # pandas 3 stores None as NaN


def test_a_row_without_shares_is_an_unpriced_holding():
    """POLICY-style rows (a value, no shares) aren't market positions."""
    row = parse_holdings(_rows()).set_index("ticker").loc["POLICY"]
    assert row["kind"] == "other"
    assert pd.isna(row["yahoo"])
    assert row["value_cad"] == pytest.approx(1500.0)


def test_stock_rows_are_mapped_to_yahoo():
    df = parse_holdings(_rows()).set_index("ticker")
    assert df.loc["XIC", "yahoo"] == "XIC.TO"
    assert df.loc["TSE:RY", "yahoo"] == "RY.TO"
    assert (df.loc[["KO", "XIC", "TSE:RY"], "kind"] == "stock").all()


def test_parse_holdings_finds_the_header_below_a_preamble():
    assert len(parse_holdings([["notes"], [], *_rows()])) == 6


@pytest.mark.parametrize("rows", [[], [["no", "header", "here"]]], ids=["empty", "no-header"])
def test_parse_holdings_without_a_header_is_empty(rows):
    assert parse_holdings(rows).empty


# --- sources ------------------------------------------------------------------------

def test_load_holdings_csv_parses_an_exported_sheet(tmp_path):
    import csv
    path = tmp_path / "holdings.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(_rows())
    assert len(holdings.load_holdings_csv(path)) == 6


def test_ticker_map_file_is_optional(tmp_path):
    assert holdings.load_ticker_map(tmp_path / "missing.csv") == {}
    path = tmp_path / "map.csv"
    path.write_text("sheet_ticker,yahoo\nFOO,FOO.V\n", encoding="utf-8")
    assert holdings.load_ticker_map(path) == {"FOO": "FOO.V"}


# --- History and contributions (workbook tabs) --------------------------------------

import datetime as dt

import pandas as pd


def test_parse_history_reads_the_daily_totals():
    frame = pd.DataFrame([[None, "Total CAD"],
                          [dt.datetime(2025, 7, 30, 22, 53), 1000.0],
                          [dt.datetime(2025, 7, 31, 14, 23), 990.0],
                          [dt.datetime(2025, 8, 1, 14, 23), "not a number"]])
    s = holdings.parse_history(frame)
    assert list(s.index) == [pd.Timestamp("2025-07-30"), pd.Timestamp("2025-07-31")]
    assert s.iloc[-1] == pytest.approx(990.0)


def test_contribution_dates_typed_month_first_are_unswapped():
    """The sheet's locale is day-first but dates are typed month-first: a typed
    "12/08/25" (8 Dec) is stored as 12 Aug, and "01/27/25" stays text."""
    frame = pd.DataFrame([
        ["Date", "Amount", "CAD Contribution", "Account", "Currency", "1.4"],
        [dt.datetime(2025, 8, 12), 100, 100, "Acct", "CAD", None],   # typed 12/08/25
        ["01/27/25", 200, 200, "Acct", "CAD", None],
        [dt.datetime(2026, 7, 8), 50, None, "Acct", "USD", 70.0],    # typed 08/07/26; CAD in F
        [None, None, 999, None, None, None],                          # totals row
    ])
    s = holdings.parse_contributions(frame)
    assert list(s.index) == [pd.Timestamp("2025-01-27"), pd.Timestamp("2025-12-08"),
                             pd.Timestamp("2026-08-07")]
    assert list(s) == [200.0, 100.0, 70.0]


def test_parse_holdings_treats_empty_spreadsheet_cells_as_blank():
    rows = [HEADER, ["Acct", "KO", 10, 100.0, 50.0, 1000, 1400, 500, 500, 700, "USD", 1.0, 0.1],
            [float("nan")] * 13]
    assert list(parse_holdings(rows)["ticker"]) == ["KO"]


def test_a_ticker_named_cash_is_a_stock_not_cash():
    """The sheet's cash lines are Cash_CAD / CASH_USD / Cash_interest_*; a real
    ticker that merely starts with "cash" (e.g. a TSX savings ETF) is not one."""
    rows = [HEADER, ["Acct", "CASH", "10", "50.0", "50.0", "500", "500", "", "", "", "CAD", "", ""]]
    row = parse_holdings(rows).iloc[0]
    assert row["kind"] == "stock" and row["yahoo"] == "CASH.TO"


@pytest.mark.parametrize("symbol, expected", [
    ("KO", "USD"), ("RY.TO", "CAD"), ("XUS-U.TO", "USD"), ("ABC.V", "CAD"), ("BF-B", "USD"),
])
def test_listing_currency(symbol, expected):
    assert holdings.listing_currency(symbol) == expected


def test_contributions_out_of_order_are_flagged(caplog):
    """The day/month unswap is only right while the tab's rows stay in date
    order — if the sheet's locale is ever fixed, that breaks, so say so."""
    frame = pd.DataFrame([
        ["Date", "Amount", "CAD Contribution", "Account", "Currency", "1.4"],
        [dt.datetime(2025, 3, 4), 100, 100, "Acct", "CAD", None],   # read as 2025-04-03
        [dt.datetime(2025, 3, 1), 100, 100, "Acct", "CAD", None],   # read as 2025-01-03: earlier
    ])
    with caplog.at_level("WARNING"):
        holdings.parse_contributions(frame)
    assert "out of order" in caplog.text


def test_load_resolves_the_path_and_dispatches_on_the_suffix(tmp_path, monkeypatch):
    import csv as _csv
    path = tmp_path / "holdings.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        _csv.writer(f).writerows(_rows())
    monkeypatch.setenv("HOLDINGS_FILE", str(path))
    book = holdings.load()
    assert len(book["holdings"]) == 6
    assert book["history"] is None and book["contributions"] is None     # a CSV has neither
    assert book["path"] == path and book["saved_at"] is not None


def test_load_without_a_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="holdings"):
        holdings.load(tmp_path / "missing.csv")
