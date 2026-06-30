"""Thesis persistence + lifecycle: JSON-per-thesis with an atomic write path.

Each thesis is one ``<state_dir>/<thesis_id>.json`` file; a lightweight
``_index.json`` mirrors the queryable fields so listing/filtering never has to
open every file. All writes go through :func:`_atomic_write_json`
(``tempfile`` + ``os.replace``) so a crash mid-write can't leave a torn file.

The lifecycle functions (``transition``/``open_position``/``trim``/``close``/
``terminate``/``mark_reviewed``) are the only sanctioned way to change a
thesis' status: each enforces the forward-only state machine in
:mod:`stockanalysis.thesis.model`, appends to ``status_history``, re-validates,
and persists atomically. Realized P&L is **ledger-based** — every trim/close
appends an immutable history entry carrying ``realized_pnl``, and the outcome
sums them, so partial exits never mutate the entry price.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from . import model

_INDEX_NAME = "_index.json"
# Queryable fields mirrored into _index.json: the first group is top-level on the
# thesis, the second lives under ``monitoring``.
_TOP_INDEX_FIELDS = ("ticker", "status", "thesis_type")
_MON_INDEX_FIELDS = ("next_review_date", "review_status")
_EPS = model._EPS


# --- low-level IO --------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _thesis_path(state_dir, thesis_id: str) -> Path:
    return Path(state_dir) / f"{thesis_id}.json"


def _save(state_dir, thesis: dict) -> dict:
    """Validate, stamp ``updated_at``, write the thesis, refresh the index."""
    model.validate_thesis(thesis)
    thesis["updated_at"] = model.now_iso()
    _atomic_write_json(_thesis_path(state_dir, thesis["thesis_id"]), thesis)
    _update_index(state_dir, thesis)
    return thesis


def _load_index(state_dir) -> dict:
    p = Path(state_dir) / _INDEX_NAME
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {"version": 1, "theses": {}}


def _index_entry(thesis: dict) -> dict:
    mon = thesis.get("monitoring", {})
    entry = {f: thesis.get(f) for f in _TOP_INDEX_FIELDS}
    entry.update({f: mon.get(f) for f in _MON_INDEX_FIELDS})
    entry["created_at"] = model.date_only(thesis["created_at"])
    entry["fingerprint"] = thesis["origin"]["fingerprint"]
    return entry


def _update_index(state_dir, thesis: dict) -> None:
    idx = _load_index(state_dir)
    idx["theses"][thesis["thesis_id"]] = _index_entry(thesis)
    _atomic_write_json(Path(state_dir) / _INDEX_NAME, idx)


# --- CRUD ----------------------------------------------------------------------

def register(state_dir, thesis_data: dict, *, salt: str | None = None) -> str:
    """Create an IDEA thesis from ``thesis_data`` and return its id.

    Idempotent: if a stored thesis already shares the new one's origin
    fingerprint, no file is written and the existing id is returned.
    """
    candidate = model.build_thesis(thesis_data, salt=salt)
    fingerprint = candidate["origin"]["fingerprint"]
    for tid, entry in _load_index(state_dir).get("theses", {}).items():
        if entry.get("fingerprint") == fingerprint:
            return tid
    _save(state_dir, candidate)
    return candidate["thesis_id"]


def get(state_dir, thesis_id: str) -> dict:
    """Load a thesis by id. Raises ``KeyError`` if absent."""
    p = _thesis_path(state_dir, thesis_id)
    if not p.exists():
        raise KeyError(f"no such thesis: {thesis_id}")
    return json.loads(p.read_text())


def query(state_dir, *, ticker=None, status=None, thesis_type=None,
          date_from=None, date_to=None) -> list[dict]:
    """Return full theses matching the given filters, sorted by creation time."""
    out = []
    for p in sorted(Path(state_dir).glob("th_*.json")):
        th = json.loads(p.read_text())
        if ticker and th["ticker"] != ticker.upper():
            continue
        if status and th["status"] != status:
            continue
        if thesis_type and th["thesis_type"] != thesis_type:
            continue
        created = model.date_only(th["created_at"])
        if date_from and created < date_from:
            continue
        if date_to and created > date_to:
            continue
        out.append(th)
    return sorted(out, key=lambda t: t["created_at"])


def update(state_dir, thesis_id: str, fields: dict) -> dict:
    """Patch non-protected fields (shallow-merging dict sections), re-validate, save."""
    bad = set(fields) & model.PROTECTED_FIELDS
    if bad:
        raise ValueError(f"cannot update protected field(s): {sorted(bad)}")
    th = get(state_dir, thesis_id)
    for key, val in fields.items():
        if isinstance(val, dict) and isinstance(th.get(key), dict):
            th[key].update(val)
        else:
            th[key] = val
    return _save(state_dir, th)


# --- lifecycle helpers ---------------------------------------------------------

def _append_history(thesis: dict, status: str, reason: str, at: str | None, **extra) -> None:
    at = at or model.now_iso()
    # Clamp so the ledger never goes backwards: a step can't be recorded before
    # the one before it (e.g. a same-day open at midnight after a wall-clock
    # transition). The true entry/exit *dates* still live on entry/exit, so P&L
    # and holding-days are unaffected by this ordering guard.
    history = thesis["status_history"]
    if history and at < history[-1]["at"]:
        at = history[-1]["at"]
    entry = {"status": status, "at": at, "reason": reason}
    entry.update({k: v for k, v in extra.items() if v is not None})
    history.append(entry)
    thesis["status"] = status


def _require_status(thesis: dict, allowed: set[str]) -> None:
    if thesis["status"] not in allowed:
        raise ValueError(
            f"thesis {thesis['thesis_id']} is {thesis['status']}; expected one of {sorted(allowed)}")


def _snap(x: float) -> float:
    x = round(x, 8)
    return 0.0 if abs(x) < _EPS else x


def _sum_realized(thesis: dict) -> float:
    return _snap(sum(h.get("realized_pnl", 0.0) for h in thesis["status_history"]))


def _finalize_outcome(thesis: dict, exit_price, exit_date, exit_reason) -> None:
    """Stamp exit fields + computed outcome on a now-closed/invalidated thesis."""
    exit_date = model.normalize_datetime(exit_date)
    thesis["exit"].update(actual_price=exit_price, actual_date=exit_date, exit_reason=exit_reason)
    pnl = _sum_realized(thesis)
    entry_price = thesis["entry"]["actual_price"]
    shares = thesis["position"]["shares"]
    pnl_pct = (round(pnl / (entry_price * shares) * 100, 2)
               if entry_price and shares else None)
    holding = None
    if thesis["entry"].get("actual_date") and exit_date:
        e = model.date_only(thesis["entry"]["actual_date"])
        x = model.date_only(exit_date)
        holding = (date.fromisoformat(x) - date.fromisoformat(e)).days
    thesis["outcome"].update(pnl_dollars=pnl, pnl_pct=pnl_pct, holding_days=holding)


def _record_sale(thesis: dict, *, status: str, reason: str, shares_sold: float,
                 price: float, at: str | None) -> float:
    """Record selling ``shares_sold`` at ``price``: compute realized P&L, decrement
    ``shares_remaining``, and append the ledger history entry under ``status``.

    The single place a sell-leg is written, so ``trim`` / ``close`` / a priced
    ``terminate`` all produce an identically-shaped ledger row. Returns the new
    ``shares_remaining``.
    """
    entry_price = thesis["entry"]["actual_price"]
    realized = _snap((price - entry_price) * shares_sold)
    new_remaining = _snap(thesis["position"]["shares_remaining"] - shares_sold)
    thesis["position"]["shares_remaining"] = new_remaining
    _append_history(thesis, status, reason, at, shares_sold=shares_sold, price=price,
                    proceeds=_snap(price * shares_sold), realized_pnl=realized)
    return new_remaining


# --- lifecycle transitions -----------------------------------------------------

def transition(state_dir, thesis_id: str, new_status: str, reason: str,
               event_date: str | None = None) -> dict:
    """Generic forward status move (mainly IDEA -> ENTRY_READY).

    Rejects reverse moves and any move out of a terminal state. For position
    mechanics use :func:`open_position` / :func:`trim` / :func:`close`.
    """
    th = get(state_dir, thesis_id)
    if new_status not in model.STATUS_ORDER:
        raise ValueError(f"unknown status {new_status!r}")
    if th["status"] in model.TERMINAL_STATUSES:
        raise ValueError(f"{thesis_id} is terminal ({th['status']}); no further transitions")
    if model.STATUS_ORDER.index(new_status) <= model.STATUS_ORDER.index(th["status"]):
        raise ValueError(f"cannot move {th['status']} -> {new_status} (forward-only)")
    _append_history(th, new_status, reason, model.normalize_datetime(event_date))
    return _save(state_dir, th)


def open_position(state_dir, thesis_id: str, actual_price: float, actual_date: str,
                  reason: str = "position opened", shares: float | None = None,
                  event_date: str | None = None) -> dict:
    """ENTRY_READY -> ACTIVE: record the actual fill and open the position."""
    th = get(state_dir, thesis_id)
    _require_status(th, {"ENTRY_READY"})
    actual_date = model.normalize_datetime(actual_date)
    th["entry"].update(actual_price=actual_price, actual_date=actual_date)
    if shares is not None:
        th["position"].update(
            shares=shares, shares_remaining=shares, position_value=_snap(actual_price * shares))
    _append_history(th, "ACTIVE", reason, model.normalize_datetime(event_date) or actual_date)
    return _save(state_dir, th)


def trim(state_dir, thesis_id: str, shares_sold: float, price: float, date: str,
         reason: str = "position trimmed", exit_reason: str | None = None,
         event_date: str | None = None) -> dict:
    """Partial close: sell ``shares_sold`` and append a realized-P&L ledger entry.

    Moves ACTIVE -> PARTIALLY_CLOSED, or -> CLOSED when the remainder hits zero.
    """
    th = get(state_dir, thesis_id)
    _require_status(th, {"ACTIVE", "PARTIALLY_CLOSED"})
    remaining = th["position"]["shares_remaining"]
    if shares_sold <= 0 or shares_sold > remaining + _EPS:
        raise ValueError(f"shares_sold {shares_sold} outside (0, {remaining}]")

    closing = _snap(remaining - shares_sold) <= 0
    at = model.normalize_datetime(event_date) or model.normalize_datetime(date)
    _record_sale(th, status="CLOSED" if closing else "PARTIALLY_CLOSED",
                 reason=f"closed: {exit_reason or 'manual'}" if closing else reason,
                 shares_sold=shares_sold, price=price, at=at)
    if closing:
        _finalize_outcome(th, price, date, exit_reason or "manual")
    return _save(state_dir, th)


def close(state_dir, thesis_id: str, exit_reason: str, actual_price: float,
          actual_date: str, event_date: str | None = None) -> dict:
    """Sell the entire remaining position at ``actual_price`` -> CLOSED."""
    th = get(state_dir, thesis_id)
    _require_status(th, {"ACTIVE", "PARTIALLY_CLOSED"})
    at = model.normalize_datetime(event_date) or model.normalize_datetime(actual_date)
    _record_sale(th, status="CLOSED", reason=f"closed: {exit_reason}",
                 shares_sold=th["position"]["shares_remaining"], price=actual_price, at=at)
    _finalize_outcome(th, actual_price, actual_date, exit_reason)
    return _save(state_dir, th)


def terminate(state_dir, thesis_id: str, terminal_status: str, exit_reason: str,
              actual_price: float | None = None, actual_date: str | None = None,
              event_date: str | None = None) -> dict:
    """Move a thesis to CLOSED or INVALIDATED.

    INVALIDATED is reachable from any non-terminal state (kill criteria hit).
    If a price+date are supplied and a position is open, the remaining shares
    are marked sold so P&L is captured; otherwise the thesis is simply killed.
    """
    if terminal_status not in model.TERMINAL_STATUSES:
        raise ValueError(f"terminal_status must be one of {sorted(model.TERMINAL_STATUSES)}")
    th = get(state_dir, thesis_id)
    if th["status"] in model.TERMINAL_STATUSES:
        raise ValueError(f"{thesis_id} is already terminal ({th['status']})")

    at = model.normalize_datetime(event_date) or model.normalize_datetime(actual_date)
    remaining = th["position"].get("shares_remaining")
    reason = f"terminated: {exit_reason}"
    if actual_price is not None and remaining:
        _record_sale(th, status=terminal_status, reason=reason,
                     shares_sold=remaining, price=actual_price, at=at)
    else:
        _append_history(th, terminal_status, reason, at)
    if terminal_status == "CLOSED":
        _finalize_outcome(th, actual_price, actual_date, exit_reason)
    else:  # INVALIDATED — kill criteria hit; P&L only captured if a price was given
        th["exit"]["exit_reason"] = exit_reason
        if actual_price is not None:
            _finalize_outcome(th, actual_price, actual_date, exit_reason)
    return _save(state_dir, th)


def mark_reviewed(state_dir, thesis_id: str, *, review_date: str, outcome: str = "OK",
                  notes: str | None = None) -> dict:
    """Record a review: set ``last_review_date`` and advance ``next_review_date``."""
    th = get(state_dir, thesis_id)
    review_date = model.date_only(model.normalize_datetime(review_date))
    interval = int(th["monitoring"].get("review_interval_days", 30))
    th["monitoring"].update(
        last_review_date=review_date,
        next_review_date=model.add_days(review_date, interval),
        review_status=outcome)
    if notes:
        th["monitoring"].setdefault("notes", []).append({"at": review_date, "note": notes})
    return _save(state_dir, th)


# --- listing / maintenance -----------------------------------------------------

def list_active(state_dir) -> list[dict]:
    """All theses currently holding an open position (ACTIVE or PARTIALLY_CLOSED)."""
    return [t for t in query(state_dir) if t["status"] in {"ACTIVE", "PARTIALLY_CLOSED"}]


def list_review_due(state_dir, as_of: str) -> list[dict]:
    """Non-terminal theses whose ``next_review_date`` is on/before ``as_of``."""
    as_of = model.date_only(model.normalize_datetime(as_of))
    out = []
    for t in query(state_dir):
        if t["status"] in model.TERMINAL_STATUSES:
            continue
        nrd = t["monitoring"].get("next_review_date")
        if nrd and nrd <= as_of:
            out.append(t)
    return out


def rebuild_index(state_dir) -> dict:
    """Reconstruct ``_index.json`` from the thesis files on disk."""
    idx = {"version": 1, "theses": {}}
    for p in sorted(Path(state_dir).glob("th_*.json")):
        th = json.loads(p.read_text())
        idx["theses"][th["thesis_id"]] = _index_entry(th)
    _atomic_write_json(Path(state_dir) / _INDEX_NAME, idx)
    return idx


def validate_state(state_dir) -> dict:
    """Check every thesis file validates and the index agrees with disk."""
    errors: list[str] = []
    on_disk = set()
    for p in sorted(Path(state_dir).glob("th_*.json")):
        try:
            th = json.loads(p.read_text())
            model.validate_thesis(th)
            on_disk.add(th["thesis_id"])
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"{p.name}: {e}")
    indexed = set(_load_index(state_dir).get("theses", {}))
    for missing in on_disk - indexed:
        errors.append(f"{missing}: on disk but not indexed")
    for stale in indexed - on_disk:
        errors.append(f"{stale}: indexed but file missing")
    return {"valid": not errors, "errors": errors}
