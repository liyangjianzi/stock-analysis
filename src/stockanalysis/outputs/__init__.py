"""Pluggable result exporters.

``get_exporter("excel")`` / ``get_exporter("gsheets")`` return an
:class:`~stockanalysis.outputs.base.Exporter`. Excel works with the core deps;
Google Sheets needs the ``gsheets`` extra and credentials.
"""
from __future__ import annotations

from .base import Exporter
from .excel import ExcelExporter

__all__ = ["Exporter", "ExcelExporter", "get_exporter"]


def get_exporter(target: str, **opts) -> Exporter:
    """Factory: return an exporter for ``target`` ('excel' or 'gsheets')."""
    target = (target or "").lower()
    if target == "excel":
        return ExcelExporter(**opts)
    if target in ("gsheets", "google", "sheets"):
        from .gsheets import GSheetsExporter  # lazy: avoids importing gspread unless used
        return GSheetsExporter(**opts)
    raise ValueError(f"Unknown export target: {target!r} (use 'excel' or 'gsheets').")
