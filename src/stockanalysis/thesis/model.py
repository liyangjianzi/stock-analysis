"""Thesis value-object: canonical shape, identifiers, and invariant validation.

This module is pure logic — no I/O, no network. It defines what a *thesis* is
(the dict shape persisted by :mod:`stockanalysis.thesis.store`), how its id and
idempotency fingerprint are derived, and the lifecycle invariants every save
must satisfy. Validation is plain Python (no ``jsonschema`` dependency).

Datetimes are RFC 3339 strings with a timezone offset (e.g.
``2026-06-30T14:30:00+00:00``); bare ``YYYY-MM-DD`` inputs are widened to
midnight UTC by :func:`normalize_datetime`.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

# Thesis types, flavored for a long-term / buy-and-hold investor (the original
# skill's short-term setups — earnings_drift, pivot_breakout — are dropped).
TYPE_ABBR: dict[str, str] = {
    "dividend_income": "div",
    "growth_momentum": "grw",
    "long_term_value": "val",
    "special_situation": "spc",
}
THESIS_TYPES = frozenset(TYPE_ABBR)

# Forward-only lifecycle. Index in STATUS_ORDER gates legal transitions; the two
# terminal states admit no further transitions.
STATUS_ORDER = ["IDEA", "ENTRY_READY", "ACTIVE", "PARTIALLY_CLOSED", "CLOSED", "INVALIDATED"]
TERMINAL_STATUSES = frozenset({"CLOSED", "INVALIDATED"})

EXIT_REASONS = frozenset({"stop_hit", "target_hit", "time_stop", "invalidated", "manual"})

# Fields that are set once at registration and must never be mutated via update().
PROTECTED_FIELDS = frozenset(
    {"thesis_id", "created_at", "status", "status_history", "ticker", "thesis_type"})

_EPS = 1e-9


# --- datetime helpers ----------------------------------------------------------

def now_iso() -> str:
    """Current UTC time as a second-precision RFC 3339 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_datetime(value: str | None) -> str | None:
    """Coerce a date / datetime string to a tz-aware RFC 3339 string.

    ``None`` passes through. A bare ``YYYY-MM-DD`` widens to midnight UTC; a
    trailing ``Z`` is accepted. Anything unparseable raises ``ValueError``.
    """
    if value is None:
        return None
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s = s + "T00:00:00+00:00"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def date_only(value: str | None) -> str | None:
    """The ``YYYY-MM-DD`` calendar-date prefix of an ISO datetime string."""
    return None if value is None else str(value)[:10]


def add_days(date_str: str, days: int) -> str:
    """Advance a ``YYYY-MM-DD`` date by ``days`` and return ``YYYY-MM-DD``."""
    d = datetime.fromisoformat(date_str[:10]).date()
    return (d + timedelta(days=days)).isoformat()


# --- identifiers ---------------------------------------------------------------

def make_thesis_id(ticker: str, thesis_type: str, date_str: str, salt: str | None = None) -> str:
    """Deterministic id ``th_<ticker>_<abbr>_<YYYYMMDD>_<hash4>``.

    ``salt`` defaults to a random UUID so distinct ideas never collide; tests
    pass a fixed salt for reproducibility. The ticker slug is lower-cased with
    non-alphanumerics stripped (so ``SU.TO`` -> ``suto``).
    """
    if thesis_type not in TYPE_ABBR:
        raise ValueError(f"unknown thesis_type {thesis_type!r}")
    slug = re.sub(r"[^a-z0-9]", "", ticker.lower())
    ymd = date_only(date_str).replace("-", "")
    salt = salt if salt is not None else uuid.uuid4().hex
    digest = hashlib.sha256(f"{ticker}_{thesis_type}_{date_str}_{salt}".encode()).hexdigest()[:4]
    return f"th_{slug}_{TYPE_ABBR[thesis_type]}_{ymd}_{digest}"


def compute_fingerprint(*, ticker: str, thesis_type: str, thesis_statement: str,
                        source_date: str | None, skill: str, raw_provenance: dict) -> str:
    """16-hex idempotency fingerprint of a thesis' identifying content.

    Order-independent over ``raw_provenance`` (keys are sorted), so re-ingesting
    the same screener/signal output yields the same fingerprint -> no duplicate.
    """
    content = "|".join([
        ticker, thesis_type, thesis_statement, str(source_date), skill,
        json.dumps(raw_provenance or {}, sort_keys=True),
    ])
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# --- canonical shape -----------------------------------------------------------

def _section(provided, defaults: dict) -> dict:
    """Merge a user-provided section dict over the canonical defaults."""
    out = dict(defaults)
    if isinstance(provided, dict):
        out.update({k: v for k, v in provided.items() if k in defaults})
    return out


def build_thesis(data: dict, *, now: str | None = None, salt: str | None = None) -> dict:
    """Build a fully-formed IDEA thesis dict from sparse input.

    Required keys: ``ticker``, ``thesis_type``, ``thesis_statement``, and
    ``origin`` (with ``skill`` + ``output_file``). Optional sections (``entry``,
    ``exit``, ``position``, ``monitoring``) and classification fields are merged
    over canonical defaults. ``created_at`` may be supplied to backdate the
    thesis to its real entry/observation date; otherwise ``now`` (or the wall
    clock) is used. Raises ``ValueError`` on bad/missing required input.
    """
    ticker = str(data.get("ticker", "")).strip().upper()
    thesis_type = data.get("thesis_type")
    statement = str(data.get("thesis_statement", "")).strip()
    origin_in = data.get("origin") or {}

    if not ticker:
        raise ValueError("thesis requires a non-empty ticker")
    if thesis_type not in THESIS_TYPES:
        raise ValueError(f"thesis_type must be one of {sorted(THESIS_TYPES)}, got {thesis_type!r}")
    if not statement:
        raise ValueError("thesis requires a non-empty thesis_statement")
    if not origin_in.get("skill") or not origin_in.get("output_file"):
        raise ValueError("origin requires both 'skill' and 'output_file'")

    created_at = normalize_datetime(data.get("created_at") or now or now_iso())

    monitoring = _section(data.get("monitoring"), {
        "review_interval_days": 30, "next_review_date": None,
        "last_review_date": None, "review_status": "OK"})
    monitoring["next_review_date"] = add_days(
        date_only(created_at), int(monitoring["review_interval_days"]))

    raw_provenance = dict(origin_in.get("raw_provenance") or {})
    fingerprint = compute_fingerprint(
        ticker=ticker, thesis_type=thesis_type, thesis_statement=statement,
        source_date=date_only(created_at), skill=origin_in["skill"],
        raw_provenance=raw_provenance)

    thesis = {
        "thesis_id": make_thesis_id(ticker, thesis_type, date_only(created_at), salt=salt),
        "ticker": ticker,
        "created_at": created_at,
        "updated_at": created_at,
        "thesis_type": thesis_type,
        "thesis_statement": statement,
        "status": "IDEA",
        "status_history": [{"status": "IDEA", "at": created_at, "reason": "registered"}],
        "origin": {
            "skill": origin_in["skill"],
            "output_file": origin_in["output_file"],
            "screening_grade": origin_in.get("screening_grade"),
            "screening_score": origin_in.get("screening_score"),
            "fingerprint": fingerprint,
            "raw_provenance": raw_provenance,
        },
        "entry": _section(data.get("entry"), {
            "target_price": None, "conditions": [],
            "actual_price": None, "actual_date": None}),
        "exit": _section(data.get("exit"), {
            "stop_loss": None, "take_profit": None, "time_stop_days": None,
            "actual_price": None, "actual_date": None, "exit_reason": None}),
        "position": _section(data.get("position"), {
            "shares": None, "shares_remaining": None, "position_value": None,
            "risk_dollars": None, "sizing_method": None}),
        "monitoring": monitoring,
        "outcome": {
            "pnl_dollars": None, "pnl_pct": None, "holding_days": None,
            "mae_pct": None, "mfe_pct": None, "mae_mfe_source": None,
            "lessons_learned": None},
    }
    # Optional free-form classification fields, only when supplied.
    for key in ("setup_type", "catalyst", "evidence", "kill_criteria", "confidence"):
        if data.get(key) is not None:
            thesis[key] = data[key]

    validate_thesis(thesis)
    return thesis


# --- invariant validation ------------------------------------------------------

def validate_thesis(thesis: dict) -> None:
    """Raise ``ValueError`` if ``thesis`` violates a structural or lifecycle rule.

    Enforces: known type/status, non-empty statement/ticker, a non-empty and
    time-monotonic status_history, the per-status entry/exit/shares invariants,
    and exit-after-entry ordering.
    """
    if thesis.get("thesis_type") not in THESIS_TYPES:
        raise ValueError(f"invalid thesis_type {thesis.get('thesis_type')!r}")
    if not str(thesis.get("ticker", "")).strip():
        raise ValueError("empty ticker")
    if not str(thesis.get("thesis_statement", "")).strip():
        raise ValueError("empty thesis_statement")

    status = thesis.get("status")
    if status not in STATUS_ORDER:
        raise ValueError(f"invalid status {status!r}")

    history = thesis.get("status_history") or []
    if not history:
        raise ValueError("status_history must not be empty")
    times = [h["at"] for h in history]
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("status_history timestamps must be non-decreasing")

    entry = thesis.get("entry") or {}
    exit_ = thesis.get("exit") or {}
    pos = thesis.get("position") or {}
    shares = pos.get("shares")
    remaining = pos.get("shares_remaining")

    def _has_entry() -> bool:
        return entry.get("actual_price") is not None and entry.get("actual_date") is not None

    if status == "ACTIVE":
        if not _has_entry():
            raise ValueError("ACTIVE requires entry.actual_price and entry.actual_date")
        if shares is None or remaining is None or abs(remaining - shares) > _EPS:
            raise ValueError("ACTIVE requires shares_remaining == shares")
    elif status == "PARTIALLY_CLOSED":
        if not _has_entry():
            raise ValueError("PARTIALLY_CLOSED requires entry data")
        if shares is None or remaining is None or not (_EPS < remaining < shares - _EPS):
            raise ValueError("PARTIALLY_CLOSED requires 0 < shares_remaining < shares")
    elif status == "CLOSED":
        if (exit_.get("actual_price") is None or exit_.get("actual_date") is None
                or not exit_.get("exit_reason")):
            raise ValueError("CLOSED requires exit price, date and reason")
        if remaining is not None and abs(remaining) > _EPS:
            raise ValueError("CLOSED requires shares_remaining == 0")
    elif status == "INVALIDATED":
        if not exit_.get("exit_reason"):
            raise ValueError("INVALIDATED requires an exit_reason")

    if entry.get("actual_date") and exit_.get("actual_date"):
        if exit_["actual_date"] < entry["actual_date"]:
            raise ValueError("exit date must not precede entry date")
