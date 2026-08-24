"""M55 (Issue #362): prospective simulated monitoring of the frozen M52
momentum candidate.

This module is the frozen protocol and pure decision/state logic only:
session-timing decisions (has a signal date's data actually completed
yet), the prospective-boundary rule (which signal is the first one
eligible to count as forward evidence), and the append-only,
hash-chained checkpoint ledger primitives. No network I/O, no
simulation logic of its own -- the actual momentum simulation is
`src.cross_sectional_momentum_research.simulate_cross_sectional_momentum_portfolio`,
called unchanged (`top_n=TOP_N`, `vol_target_annualized=None`, the
exact M52 defaults) by `scripts/monitor_momentum_prospective.py`, which
owns all orchestration and I/O.

Frozen candidate (Issue #362): the exact M52 strategy, not either M53
arm -- point-in-time IWB universe, deterministic OpenFIGI resolution,
Alpaca SIP adjusted daily bars, same liquidity floors, 12-1 momentum,
top-15 equal-capital weight, SPY 200-session trend filter, monthly
signal on the final completed regular session, execution at the next
regular session's open, $30,000 fixed paper capital, base 5bps /
stress 10bps parallel paper ledgers. No M53 breadth-30 or
volatility-target overlay. No strategy parameter changes during
monitoring.

Prospective boundary: no paper P&L may begin before the first monthly
signal whose signal date occurs STRICTLY AFTER the frozen protocol
commit's timestamp (compared as an America/New_York calendar date, the
conservative reading -- a signal occurring on the exact same calendar
date as the freeze is never treated as prospective, since that day's
close could not yet be known at freeze time). Warm-up history may be
loaded solely to compute the frozen indicators; it is never treated as
prospective performance evidence.

Execution model: runs once and exits. Never remains open waiting for
future sessions, never polls continuously. Reruns against the same
completed data are idempotent -- no duplicate checkpoint, signal, or
transaction is ever created.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

__all__ = [
    "NY_TZ",
    "CHECKPOINT_TYPE_INITIALIZATION",
    "CHECKPOINT_TYPE_SIGNAL",
    "RUN_STATUS_NOT_DUE",
    "RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL",
    "RUN_STATUS_SIGNAL_DUE",
    "MonitorProtocolError",
    "to_ny_datetime",
    "to_ny_date",
    "latest_completed_regular_session",
    "month_end_sessions",
    "first_eligible_signal_date",
    "next_pending_signal_date",
    "checkpoint_run_decision",
    "assert_frozen_candidate_config",
    "canonical_checkpoint_json",
    "compute_checkpoint_hash",
    "validate_hash_chain",
]

NY_TZ = ZoneInfo("America/New_York")

CHECKPOINT_TYPE_INITIALIZATION = "INITIALIZATION"
CHECKPOINT_TYPE_SIGNAL = "SIGNAL"

RUN_STATUS_NOT_DUE = "NOT_DUE"
RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL = "INITIALIZED_WAITING_FOR_FIRST_SIGNAL"
RUN_STATUS_SIGNAL_DUE = "SIGNAL_DUE"


class MonitorProtocolError(RuntimeError):
    """Refused: an M55 (Issue #362) monitoring-protocol precondition
    failed -- e.g. a non-M52 configuration was about to be used, or the
    checkpoint hash chain does not validate. Never silently substituted
    or guessed."""


def to_ny_datetime(moment_utc: datetime) -> datetime:
    """`moment_utc` must be timezone-aware. Converts to
    America/New_York wall-clock time -- the timezone every session
    boundary and freeze-timestamp comparison in this module uses."""
    if moment_utc.tzinfo is None:
        raise MonitorProtocolError("moment_utc must be timezone-aware")
    return moment_utc.astimezone(NY_TZ)


def to_ny_date(moment_utc: datetime) -> date:
    return to_ny_datetime(moment_utc).date()


def latest_completed_regular_session(
    *, now_utc: datetime, master_dates: Sequence[date], session_close_time: Mapping[date, time] | None = None,
    default_close_time: time = time(16, 0),
) -> date | None:
    """The most recent regular session in `master_dates` whose close
    has definitely occurred as of `now_utc`. If today (America/New_York
    calendar date) is itself a session but has not yet reached its
    close time, today does NOT count -- the previous session in
    `master_dates` is returned instead. `session_close_time` supplies
    the exact close time for a specific date (e.g. an early-close
    session); dates absent from it fall back to `default_close_time`
    (16:00 ET) -- the conservative choice, since treating an
    early-close day as open until the standard close only makes this
    function MORE conservative (waits longer before calling a session
    "completed"), never less. `master_dates` must be sorted ascending.
    Returns `None` if no session in `master_dates` is on or before
    today."""
    ny_now = to_ny_datetime(now_utc)
    ny_today = ny_now.date()
    on_or_before_today = [d for d in master_dates if d <= ny_today]
    if not on_or_before_today:
        return None
    latest = on_or_before_today[-1]
    if latest == ny_today:
        close_time = (session_close_time or {}).get(ny_today, default_close_time)
        if ny_now.time() < close_time:
            prior = [d for d in master_dates if d < ny_today]
            return prior[-1] if prior else None
    return latest


def month_end_sessions(master_dates: Sequence[date]) -> tuple[date, ...]:
    """The last session of every calendar month present in
    `master_dates`, sorted ascending. Pure grouping -- no M52-specific
    "plus the month before start" behavior (that convention belongs to
    `cross_sectional_momentum_research.month_end_signal_dates`, which
    exists to align a historical backtest's window; M55 has no such
    need since it only ever looks forward from the freeze date)."""
    by_month: dict[tuple[int, int], date] = {}
    for d in master_dates:
        key = (d.year, d.month)
        if key not in by_month or d > by_month[key]:
            by_month[key] = d
    return tuple(sorted(by_month.values()))


def first_eligible_signal_date(*, freeze_date_ny: date, master_dates: Sequence[date]) -> date | None:
    """The earliest month-end session strictly after `freeze_date_ny`.
    `None` if `master_dates` does not extend far enough into the future
    to contain one -- never guessed."""
    for d in month_end_sessions(master_dates):
        if d > freeze_date_ny:
            return d
    return None


def next_pending_signal_date(
    *, last_processed_signal_date: date | None, freeze_date_ny: date, master_dates: Sequence[date],
) -> date | None:
    """The next signal date this monitor still needs to process: the
    first eligible signal if none has been processed yet, otherwise the
    next month-end session strictly after the last one processed."""
    if last_processed_signal_date is None:
        return first_eligible_signal_date(freeze_date_ny=freeze_date_ny, master_dates=master_dates)
    for d in month_end_sessions(master_dates):
        if d > last_processed_signal_date:
            return d
    return None


def checkpoint_run_decision(
    *, now_utc: datetime, freeze_date_ny: date, master_dates: Sequence[date],
    last_processed_signal_date: date | None,
) -> dict[str, Any]:
    """The single run-time decision this monitor makes: is a new signal
    due, or not. Never fabricates a signal/transaction -- if the
    pending signal date's session has not yet completed, returns
    NOT_DUE (or INITIALIZED_WAITING_FOR_FIRST_SIGNAL on a first run)
    with the diagnostic fields Issue #362 requires."""
    latest_completed = latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates)
    pending_signal = next_pending_signal_date(
        last_processed_signal_date=last_processed_signal_date, freeze_date_ny=freeze_date_ny, master_dates=master_dates,
    )
    is_first_run = last_processed_signal_date is None
    if pending_signal is None:
        raise MonitorProtocolError("master_dates does not extend far enough into the future to determine the next pending signal date")

    execution_candidates = [d for d in master_dates if d > pending_signal]
    earliest_next_useful_run_date = execution_candidates[0] if execution_candidates else None

    if latest_completed is None or latest_completed < pending_signal:
        return {
            "status": RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL if is_first_run else RUN_STATUS_NOT_DUE,
            "last_processed_session": latest_completed.isoformat() if latest_completed else None,
            "next_expected_signal_date": pending_signal.isoformat(),
            "earliest_next_useful_run_date": earliest_next_useful_run_date.isoformat() if earliest_next_useful_run_date else None,
        }
    execution_date = earliest_next_useful_run_date
    return {
        "status": RUN_STATUS_SIGNAL_DUE,
        "signal_date": pending_signal.isoformat(),
        "execution_date": execution_date.isoformat() if execution_date else None,
        "last_processed_session": latest_completed.isoformat(),
    }


def assert_frozen_candidate_config(*, top_n: int, vol_target_annualized: Any) -> None:
    """Initialization-gate item 2: M53 changes must never leak into the
    M55 candidate. Raises unless the configuration is bit-for-bit the
    M52 default (`top_n=15`, no volatility-exposure cap)."""
    from src.cross_sectional_momentum_research import TOP_N
    if top_n != TOP_N:
        raise MonitorProtocolError(f"M55 must monitor the frozen M52 top_n={TOP_N} candidate, got top_n={top_n} -- M53 Arm A (BREADTH_30) leakage refused")
    if vol_target_annualized is not None:
        raise MonitorProtocolError(f"M55 must monitor the frozen M52 candidate with no volatility-exposure cap, got vol_target_annualized={vol_target_annualized} -- M53 Arm B (MARKET_VOL_15) leakage refused")


def canonical_checkpoint_json(checkpoint_without_hash: Mapping[str, Any]) -> str:
    """Deterministic JSON serialization used both to compute a
    checkpoint's own hash and to hash-chain the next one -- sorted
    keys, fixed separators, so the same logical content always hashes
    identically regardless of dict insertion order."""
    return json.dumps(checkpoint_without_hash, sort_keys=True, separators=(",", ":"), default=str)


def compute_checkpoint_hash(checkpoint_without_hash: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_checkpoint_json(checkpoint_without_hash).encode("utf-8")).hexdigest()


def validate_hash_chain(checkpoints: Sequence[Mapping[str, Any]]) -> bool:
    """Walks an ordered checkpoint ledger and confirms: checkpoint 0 has
    `previous_checkpoint_sha256=None`; every later checkpoint's
    `previous_checkpoint_sha256` equals the immediately preceding
    checkpoint's own `checkpoint_sha256`; and every checkpoint's own
    `checkpoint_sha256` matches a fresh recomputation over its content
    (tamper-evident -- a hand-edited checkpoint fails this)."""
    if not checkpoints:
        return True
    if checkpoints[0].get("previous_checkpoint_sha256") is not None:
        return False
    for i, cp in enumerate(checkpoints):
        content = {k: v for k, v in cp.items() if k != "checkpoint_sha256"}
        if compute_checkpoint_hash(content) != cp.get("checkpoint_sha256"):
            return False
        if i > 0 and cp.get("previous_checkpoint_sha256") != checkpoints[i - 1].get("checkpoint_sha256"):
            return False
    return True
