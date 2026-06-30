"""Registration adapters: turn this package's own outputs into IDEA theses.

Only the two sources a StockAnalysis user actually has are supported — the
native ``signal_matrix`` (the Buy/Hold/Watch frame from
:func:`stockanalysis.signals.generate_signals`) and free-form **manual** entry.
Both go through :func:`stockanalysis.thesis.store.register`, so they inherit its
fingerprint idempotency: re-ingesting the same run is a no-op.

The 7 foreign screener adapters (vcp / pead / canslim / earnings / edge /
kanchi) of the original skill are intentionally dropped — this project doesn't
emit those formats.
"""
from __future__ import annotations

import pandas as pd

from . import store

_SIGNAL_SKILL = "signal-matrix"
_ACTION_COL = "Final Action Signal"


def _pyval(v):
    """Coerce a pandas/numpy scalar to a JSON-serializable native Python value."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):          # numpy scalar -> python int/float/bool
        return v.item()
    return v


def from_manual(state_dir, data: dict, *, salt: str | None = None) -> str:
    """Register one manually-formed thesis (an idea you came up with yourself).

    Requires ``ticker``, ``thesis_type`` and ``thesis_statement``. Optional
    planning fields map to the thesis: ``target_price`` -> ``entry.target_price``,
    ``stop_price`` -> ``exit.stop_loss``, ``target_profit`` -> ``exit.take_profit``,
    ``entry_date`` -> ``created_at`` (backdates the idea). Everything supplied is
    also preserved verbatim under ``origin.raw_provenance`` (e.g. a planned
    ``shares`` size). Returns the thesis id.
    """
    thesis_data = {
        "ticker": data.get("ticker"),
        "thesis_type": data.get("thesis_type"),
        "thesis_statement": data.get("thesis_statement"),
        "created_at": data.get("entry_date"),
        "entry": {"target_price": data.get("target_price")},
        "exit": {"stop_loss": data.get("stop_price"),
                 "take_profit": data.get("target_profit")},
        "origin": {"skill": "manual", "output_file": "-",
                   "raw_provenance": {k: v for k, v in data.items()}},
    }
    if data.get("review_interval_days") is not None:
        thesis_data["monitoring"] = {"review_interval_days": int(data["review_interval_days"])}
    return store.register(state_dir, thesis_data, salt=salt)


def from_signal_matrix(state_dir, signal_matrix: pd.DataFrame, *,
                       actions: tuple[str, ...] = ("Buy",),
                       default_type: str = "long_term_value",
                       run_file: str = "-", as_of: str | None = None,
                       salt: str | None = None) -> list[str]:
    """Register IDEA theses from the rows of a ``signal_matrix`` frame.

    Only rows whose ``Final Action Signal`` is in ``actions`` are taken (Buys by
    default). Each row's full contents are carried into ``origin.raw_provenance``
    and summarized into the thesis statement. Idempotent via fingerprint, so
    re-running over the same matrix returns the same ids without duplicating.
    Returns the list of (new or pre-existing) thesis ids.
    """
    if signal_matrix is None or signal_matrix.empty or _ACTION_COL not in signal_matrix.columns:
        return []

    ids: list[str] = []
    for _, row in signal_matrix.iterrows():
        if row[_ACTION_COL] not in actions:
            continue
        provenance = {col: _pyval(row[col]) for col in signal_matrix.columns}
        ticker = provenance.get("Ticker")
        statement = (
            f"{provenance.get(_ACTION_COL)} signal: composite "
            f"{provenance.get('Composite')}, {provenance.get('Technical Posture')} posture, "
            f"fundamental {provenance.get('Fundamental Score')}/6.")
        thesis_data = {
            "ticker": ticker,
            "thesis_type": default_type,
            "thesis_statement": statement,
            "created_at": as_of,
            "origin": {
                "skill": _SIGNAL_SKILL, "output_file": run_file,
                "screening_grade": provenance.get(_ACTION_COL),
                "screening_score": provenance.get("Composite"),
                "raw_provenance": provenance,
            },
        }
        ids.append(store.register(state_dir, thesis_data, salt=salt))
    return ids
