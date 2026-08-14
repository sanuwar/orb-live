"""Bounded read-only morning watcher for the canonical LIVE_SHADOW runner."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from src.config import load_config
from src.exchange_calendar import EASTERN, CalendarSession, classify
from src.live_shadow_session import (
    DEFAULT_EVIDENCE_DIR,
    LiveShadowError,
    RunMode,
    SessionLimits,
    _watchlist,
    persist_evidence,
    run_session,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.runtime_filter_projection import static_filter_eliminations

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCH_EVIDENCE_DIR = REPOSITORY_ROOT / "artifacts" / "live-shadow-watch"
DEFAULT_WATCHLIST = ("AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "NFLX")
MIN_INTERVAL_MINUTES = 5
DEFAULT_INTERVAL_MINUTES = 5
MAX_EVALUATIONS = 17


class MorningWatchError(RuntimeError):
    """The bounded watcher cannot safely start or continue."""


@dataclass(frozen=True)
class WatchLimits:
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    max_evaluations: int = MAX_EVALUATIONS

    def __post_init__(self) -> None:
        if self.interval_minutes < MIN_INTERVAL_MINUTES:
            raise MorningWatchError("poll interval is below the safe minimum")
        if self.max_evaluations < 1 or self.max_evaluations > MAX_EVALUATIONS:
            raise MorningWatchError("evaluation bound exceeds the safe maximum")


Evaluator = Callable[[datetime], Awaitable[tuple[Mapping[str, Any], Path]]]
Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


def _entry_window(day: date) -> tuple[datetime, datetime]:
    """Bind the watcher to the strategy's configured canonical entry window."""
    signal = load_config()["signal"]
    start = time.fromisoformat(str(signal["entry_start"]))
    end = time.fromisoformat(str(signal["entry_end"]))
    return (
        datetime.combine(day, start, tzinfo=EASTERN).astimezone(timezone.utc),
        datetime.combine(day, end, tzinfo=EASTERN).astimezone(timezone.utc),
    )


def scheduled_evaluations(*, day: date, interval_minutes: int) -> tuple[datetime, ...]:
    """Return the finite configured entry-window schedule, including endpoints."""
    WatchLimits(interval_minutes=interval_minutes)
    start, end = _entry_window(day)
    values: list[datetime] = []
    current = start
    while current <= end and len(values) < MAX_EVALUATIONS:
        values.append(current)
        current += timedelta(minutes=interval_minutes)
    return tuple(values)


def _watch_session_id(*, day: date, symbols: Sequence[str], started_at: datetime, interval_minutes: int) -> str:
    material = f"{day.isoformat()}|{','.join(symbols)}|{started_at.isoformat()}|{interval_minutes}".encode("utf-8")
    return f"m19-morning-live-shadow-watch-{hashlib.sha256(material).hexdigest()[:16]}"


def _base_summary(*, symbols: Sequence[str], started_at: datetime, limits: WatchLimits, root: Path) -> dict[str, Any]:
    day = started_at.astimezone(EASTERN).date()
    session_id = _watch_session_id(day=day, symbols=symbols, started_at=started_at, interval_minutes=limits.interval_minutes)
    return {
        "schema_version": "m19-morning-live-shadow-watch-v1",
        "run_id": session_id,
        "watch_session_id": session_id,
        "run_mode": "MORNING_LIVE_SHADOW_WATCH",
        "trading_date": day,
        "started_at": started_at,
        "completed_at": None,
        "configured_interval_minutes": limits.interval_minutes,
        "configured_symbols": list(symbols),
        "evaluation_count": 0,
        "evaluations": [],
        "first_candidate_timestamp": None,
        "stop_reason": None,
        "final_state": "NOT_STARTED",
        "reviewed_sha": resolve_head_sha(root),
        "implementation_digest": checkpoint_implementation_digest(root),
        "provider": "alpaca",
        "feed": "iex",
        "total_http_request_count": 0,
        "broker_write_count": 0,
        "execution_authorized": False,
        "protection_grounded": False,
        "authority_ceiling": "ATTENDED_COMMISSIONING_ONLY",
    }


def _candidate_decisions(evidence: Mapping[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for symbol, result in dict(evidence.get("symbol_results", {})).items():
        decision = dict(result).get("shadow_trading_decision")
        decision_type = getattr(decision, "decision_type", None)
        decision_value = getattr(decision_type, "value", decision_type)
        if isinstance(decision, Mapping):
            decision_value = decision.get("decision_type")
            decision_id = decision.get("decision_id")
        else:
            decision_id = getattr(decision, "decision_id", None)
        if decision_value == "LONG_CANDIDATE" and isinstance(decision_id, str):
            candidates.append({"symbol": str(symbol), "decision_id": decision_id})
    return candidates


def _evaluation_reference(*, scheduled_at: datetime, evidence: Mapping[str, Any], path: Path) -> dict[str, Any]:
    candidates = _candidate_decisions(evidence)
    return {
        "scheduled_at": scheduled_at,
        "evaluation_run_id": evidence.get("run_id"),
        "evidence_artifact": str(path),
        "candidate_count": len(candidates),
        "candidate_decisions": candidates,
        "request_count": int(evidence.get("request_count", 0)),
        "quality_outcome": evidence.get("quality_outcome"),
        "errors": list(evidence.get("errors", [])),
    }


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_evaluator(*, symbols: Sequence[str], output_dir: Path, environment: Mapping[str, str] | None) -> Evaluator:
    async def evaluate(moment: datetime) -> tuple[Mapping[str, Any], Path]:
        return await run_session(
            mode=RunMode.LIVE_SHADOW,
            candidate_symbols=symbols,
            output_dir=output_dir,
            now=moment,
            environment=environment,
            limits=SessionLimits(),
            production_confirmed=True,
        )
    return evaluate


async def run_morning_watch(
    *,
    symbols: Sequence[str] = DEFAULT_WATCHLIST,
    output_dir: Path = DEFAULT_WATCH_EVIDENCE_DIR,
    live_shadow_output_dir: Path = DEFAULT_EVIDENCE_DIR,
    now: datetime | None = None,
    limits: WatchLimits = WatchLimits(),
    production_confirmed: bool = False,
    environment: Mapping[str, str] | None = None,
    evaluator: Evaluator | None = None,
    sleeper: Sleeper = _sleep,
    clock: Clock | None = None,
    root: Path = REPOSITORY_ROOT,
) -> tuple[dict[str, Any], Path]:
    """Run at most one bounded entry-window schedule of canonical evaluations."""
    # ``now`` remains a deterministic test seam. Production always re-reads
    # the clock before scheduling, sleeping, and evaluating.
    current_clock = clock or ((lambda: now) if now is not None else _system_clock)
    moment = current_clock().astimezone(timezone.utc)
    watchlist = _watchlist("AAPL", symbols, max_symbols=10)
    summary = _base_summary(symbols=watchlist, started_at=moment, limits=limits, root=root)
    day = moment.astimezone(EASTERN).date()
    classification = classify(moment, today=day)
    if not production_confirmed:
        summary.update(final_state="PRODUCTION_CONFIRMATION_REQUIRED", stop_reason="PRODUCTION_CONFIRMATION_REQUIRED", completed_at=moment)
        return summary, persist_evidence(summary, output_dir=output_dir)
    if classification.session is not CalendarSession.REGULAR or not classification.is_regular_session:
        summary.update(final_state="OUTSIDE_ENTRY_WINDOW", stop_reason="NOT_REGULAR_SESSION", completed_at=moment)
        return summary, persist_evidence(summary, output_dir=output_dir)
    start, end = _entry_window(day)
    if moment < start:
        summary.update(final_state="BEFORE_ENTRY_WINDOW", stop_reason="BEFORE_ENTRY_WINDOW", completed_at=moment)
        return summary, persist_evidence(summary, output_dir=output_dir)
    moment = current_clock().astimezone(timezone.utc)
    if moment >= end:
        summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_COMPLETE", completed_at=moment)
        return summary, persist_evidence(summary, output_dir=output_dir)
    schedule = scheduled_evaluations(day=day, interval_minutes=limits.interval_minutes)
    evaluate = evaluator or _default_evaluator(symbols=watchlist, output_dir=live_shadow_output_dir, environment=environment)
    last_scheduled: datetime | None = None
    statically_eliminated: set[str] = set()
    completed_at = moment
    try:
        while True:
            # A schedule is only meaningful relative to the current
            # authoritative time, never the time at which this process began.
            current = current_clock().astimezone(timezone.utc)
            completed_at = current
            if current >= end:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_COMPLETE")
                break
            if summary["evaluation_count"] >= limits.max_evaluations:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_SCHEDULE_EXHAUSTED")
                break

            remaining_points = tuple(
                point for point in schedule
                if point >= current and (last_scheduled is None or point > last_scheduled)
            )
            if not remaining_points:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_SCHEDULE_EXHAUSTED")
                break
            point = remaining_points[0]

            # Re-check immediately before sleeping; the end may have elapsed
            # while calculating the next valid schedule point.
            current = current_clock().astimezone(timezone.utc)
            completed_at = current
            if current >= end:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_COMPLETE")
                break
            configured_wait = max(0.0, (point - current).total_seconds())
            remaining_window = max(0.0, (end - current).total_seconds())
            actual_sleep = min(configured_wait, remaining_window)
            if actual_sleep:
                await sleeper(actual_sleep)

            # A wake-up at or beyond the endpoint persists and exits before
            # the evaluator can issue another market-data request.
            current = current_clock().astimezone(timezone.utc)
            completed_at = current
            if current >= end:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_COMPLETE")
                break

            # This is deliberately a separate pre-evaluation clock check.
            # It closes the interval between waking and invoking the evaluator.
            current = current_clock().astimezone(timezone.utc)
            completed_at = current
            if current >= end:
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="ENTRY_WINDOW_COMPLETE")
                break
            evidence, artifact = await evaluate(point)
            last_scheduled = point
            reference = _evaluation_reference(scheduled_at=point, evidence=evidence, path=artifact)
            summary["evaluations"].append(reference)
            summary["evaluation_count"] += 1
            summary["total_http_request_count"] += reference["request_count"]
            if evidence.get("errors"):
                summary.update(final_state="PROVIDER_FAILURE", stop_reason="SHARED_PROVIDER_FAILURE")
                break
            if reference["candidate_count"]:
                summary.update(final_state="CANDIDATE_CAPTURED", stop_reason="FIRST_GENUINE_LONG_CANDIDATE", first_candidate_timestamp=point)
                break
            statically_eliminated.update(static_filter_eliminations(evidence, symbols=watchlist))
            if statically_eliminated == set(watchlist):
                summary.update(final_state="WINDOW_COMPLETE_NO_CANDIDATE", stop_reason="NO_SURVIVORS_AFTER_STATIC_FILTERS")
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        summary.update(final_state="OPERATOR_STOPPED", stop_reason="OPERATOR_INTERRUPTION")
    except BaseException:
        summary.update(final_state="PROVIDER_FAILURE", stop_reason="WATCHER_FAILURE")
    summary["completed_at"] = completed_at

    summary["broker_write_count"] = 0
    summary["execution_authorized"] = False
    summary["protection_grounded"] = False
    return summary, persist_evidence(summary, output_dir=output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded read-only morning watcher for LIVE_SHADOW")
    parser.add_argument("--production", action="store_true", help="explicitly permit bounded Alpaca market-data reads")
    parser.add_argument("--symbols", help="comma-separated bounded candidate watchlist")
    parser.add_argument("--interval-minutes", type=int, default=DEFAULT_INTERVAL_MINUTES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_WATCH_EVIDENCE_DIR)
    parser.add_argument("--live-shadow-output-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    args = parser.parse_args(argv)
    try:
        summary, path = asyncio.run(run_morning_watch(
            symbols=tuple(args.symbols.split(",")) if args.symbols is not None else DEFAULT_WATCHLIST,
            output_dir=args.output_dir,
            live_shadow_output_dir=args.live_shadow_output_dir,
            limits=WatchLimits(interval_minutes=args.interval_minutes),
            production_confirmed=args.production,
        ))
    except (MorningWatchError, LiveShadowError):
        return 2
    print(path)
    return 0 if summary["final_state"] in {"WINDOW_COMPLETE_NO_CANDIDATE", "CANDIDATE_CAPTURED"} else 2


__all__ = [
    "DEFAULT_INTERVAL_MINUTES", "DEFAULT_WATCHLIST", "DEFAULT_WATCH_EVIDENCE_DIR",
    "MAX_EVALUATIONS", "MIN_INTERVAL_MINUTES", "MorningWatchError", "WatchLimits",
    "main", "run_morning_watch", "scheduled_evaluations",
]
