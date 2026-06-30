"""Exporter interface for the signal matrix + screener detail."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

# Worksheet names shared by every exporter (and the thesis ingest path that reads
# them back) — keep them here so a rename can't desync producer and consumer.
SIGNAL_MATRIX_SHEET = "Signal Matrix"
FUNDAMENTALS_SHEET = "Fundamentals"


class Exporter(ABC):
    """Write the pipeline results to some destination.

    Implementations receive the signal matrix and (optionally) the screener
    detail, and persist them however they like (Excel workbook, Google Sheet, …).
    """

    @abstractmethod
    def export(self, signal_matrix: pd.DataFrame,
               screened_df: pd.DataFrame | None = None, **meta) -> str:
        """Persist the results and return a human-readable destination string."""
        raise NotImplementedError
