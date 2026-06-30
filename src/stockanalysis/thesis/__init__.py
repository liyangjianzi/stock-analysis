"""Thesis memory: track an investment idea across its lifecycle.

A lean, native port of the ``trader-memory-core`` skill tailored to this
package — it records *why* a position was opened, when to review it, and how it
turned out, closing the Plan -> Trade -> Record -> Review -> Improve loop the
signal engine alone can't. Storage is JSON-per-thesis (stdlib only); MAE/MFE is
computed from the same ``yfinance`` source the rest of the package already uses.

The public API is re-exported here so callers can ``from stockanalysis.thesis
import register, open_position, close, generate_postmortem`` without reaching
into the individual modules.
"""
from __future__ import annotations

from . import model, review, sources, store
from .review import generate_postmortem, summary_stats
from .sources import from_manual, from_signal_matrix
from .store import (
    close,
    get,
    list_active,
    list_review_due,
    mark_reviewed,
    open_position,
    query,
    rebuild_index,
    register,
    terminate,
    transition,
    trim,
    update,
    validate_state,
)

__all__ = [
    "model", "store", "sources", "review",
    "register", "get", "query", "update", "transition", "open_position",
    "trim", "close", "terminate", "mark_reviewed", "list_active",
    "list_review_due", "rebuild_index", "validate_state",
    "from_signal_matrix", "from_manual", "generate_postmortem", "summary_stats",
]
