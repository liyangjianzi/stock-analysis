"""Exporter interface for the signal matrix + screener detail."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


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
