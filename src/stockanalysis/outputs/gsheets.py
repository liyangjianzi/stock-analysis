"""Google Sheets exporter (gspread + google-auth).

Requires the optional ``gsheets`` extra: ``pip install -e ".[gsheets]"``.

Authentication uses a Google service-account JSON key. Point
``$GOOGLE_APPLICATION_CREDENTIALS`` at the key file and share the target sheet
with the service-account email. Identify the sheet via ``spreadsheet`` (id or
name) or the ``$GSHEET_ID`` / ``$GSHEET_NAME`` environment variables.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from .base import Exporter

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GSheetsExporter(Exporter):
    """Write the signal matrix + screener detail to a Google Spreadsheet."""

    def __init__(self, spreadsheet: str | None = None, credentials: str | None = None):
        # Resolve config from args or environment.
        self.spreadsheet = spreadsheet or os.getenv("GSHEET_ID") or os.getenv("GSHEET_NAME")
        self.credentials = credentials or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    def _open(self):
        """Authorize and open the target spreadsheet (lazy-imports gspread)."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as e:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Google Sheets export needs the 'gsheets' extra: "
                'pip install -e ".[gsheets]"'
            ) from e

        if not self.credentials:
            raise RuntimeError(
                "No credentials: set $GOOGLE_APPLICATION_CREDENTIALS to a "
                "service-account JSON key file."
            )
        if not self.spreadsheet:
            raise RuntimeError(
                "No target spreadsheet: pass spreadsheet=<id|name> or set "
                "$GSHEET_ID / $GSHEET_NAME."
            )

        creds = Credentials.from_service_account_file(self.credentials, scopes=_SCOPES)
        client = gspread.authorize(creds)
        try:  # treat the identifier as a key first, then fall back to a name
            return client.open_by_key(self.spreadsheet)
        except Exception:
            return client.open(self.spreadsheet)

    @staticmethod
    def _to_values(df: pd.DataFrame) -> list[list]:
        """Header row + rows, with NaN -> '' so gspread serialises cleanly."""
        clean = df.replace({np.nan: ""})
        return [clean.columns.tolist()] + clean.astype(object).values.tolist()

    def _write_sheet(self, sh, title: str, df: pd.DataFrame) -> None:
        try:
            ws = sh.worksheet(title)
            ws.clear()
        except Exception:
            rows = max(len(df) + 10, 20)
            cols = max(len(df.columns) + 2, 10)
            ws = sh.add_worksheet(title=title, rows=rows, cols=cols)
        ws.update(self._to_values(df), value_input_option="USER_ENTERED")

    def export(self, signal_matrix: pd.DataFrame,
               screened_df: pd.DataFrame | None = None, **meta) -> str:
        if signal_matrix is None or signal_matrix.empty:
            log.warning("Nothing to export — signal matrix is empty.")
            return ""
        sh = self._open()
        self._write_sheet(sh, "Signal Matrix", signal_matrix)
        if screened_df is not None and not screened_df.empty:
            self._write_sheet(sh, "Fundamentals", screened_df.reset_index())
        log.info("Exported results to Google Sheet '%s'.", sh.title)
        return sh.url
