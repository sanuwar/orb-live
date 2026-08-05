"""Finite synthetic market-day execution over the reviewed offline stack."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from src.exchange_calendar import CalendarSession, ClassificationReason, DayKind, classify
from src.offline_fixture_package import (
    FixtureScenario, OfflineFixturePackage, available_scenarios, build_fixture,
)
from scripts.run_offline_shadow_rehearsal import (
    RehearsalReport, ScenarioResult, run_rehearsal,
)

MAX_SESSION_SCENARIOS = 10
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SESSION_NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
_EASTERN = ZoneInfo("America/New_York")
_ALLOWED_STATES = frozenset({
    "PRE_MARKET", "REGULAR", "CLOSED", "UNKNOWN", "COMPLETED_BOUND",
    "STOPPED_ON_REVIEW", "STOPPED_ON_SUSPENSION", "SUSPENSION_REQUIRED",
    "DELIVERY_UNCONFIRMED", "STALE", "RECOVERED", "AMBIGUOUS_RESTART",
})
_FORBIDDEN = ("account", "credential", "password", "symbol", "quantity", "price", "balance")


class ShadowSessionError(ValueError):
    """A bounded session request or projection is invalid."""


class MarketDayScenario(str, Enum):
    PRE_MARKET_WAIT = "PRE_MARKET_WAIT"
    REGULAR_SESSION_CALENDAR_BOUNDARY = "REGULAR_SESSION_CALENDAR_BOUNDARY"
    OPERATOR_REVIEW_STOP = "OPERATOR_REVIEW_STOP"
    SUSPENSION_STOP = "SUSPENSION_STOP"
    MISSED_HEARTBEAT = "MISSED_HEARTBEAT"
    FAILED_ALERT_CONFIRMATION = "FAILED_ALERT_CONFIRMATION"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    DURABLE_RESTART_RECOVERY = "DURABLE_RESTART_RECOVERY"
    EARLY_CLOSE_BOUNDARY = "EARLY_CLOSE_BOUNDARY"
    UNSUPPORTED_DATE = "UNSUPPORTED_DATE"


@dataclass(frozen=True)
class SessionObservation:
    scenario: MarketDayScenario
    fixture: OfflineFixturePackage
    expected_state: str
    observed_state: str
    durable_files: tuple[str, ...]
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, MarketDayScenario) or not isinstance(self.fixture, OfflineFixturePackage):
            raise ShadowSessionError("session identity is invalid")
        try:
            self.fixture.validate(evaluation_time=SESSION_NOW, maximum_age=timedelta(days=1))
        except Exception as exc:
            raise ShadowSessionError("fixture evidence is invalid") from exc
        if self.expected_state not in _ALLOWED_STATES or self.observed_state not in _ALLOWED_STATES:
            raise ShadowSessionError("session state projection is invalid")
        if not isinstance(self.durable_files, tuple) or any(
            not isinstance(value, str) or Path(value).name != value
            or Path(value).suffix not in {"", ".json", ".sqlite"}
            or any(fragment in value.casefold() for fragment in _FORBIDDEN)
            for value in self.durable_files
        ):
            raise ShadowSessionError("durable file projection is invalid")
        if self.permits_automated_action is not False:
            raise ShadowSessionError("session observations never authorize action")

    @property
    def fixture_id(self) -> str:
        return self.fixture.fixture_id

    @property
    def passed(self) -> bool:
        return self.expected_state == self.observed_state

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario.value, "fixture_id": self.fixture.fixture_id,
            "fixture_digest": self.fixture.manifest_digest,
            "expected_state": self.expected_state, "observed_state": self.observed_state,
            "durable_files": list(self.durable_files), "pass": self.passed,
            "permits_automated_action": False,
        }


@dataclass(frozen=True)
class BoundedShadowSessionReport:
    session_id: str
    observations: tuple[SessionObservation, ...]
    evaluated_at: datetime = SESSION_NOW
    live_ready: bool = False
    unattended_ready: bool = False
    protection_grounded: bool = False
    activation_approved: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or self.session_id != "shadow-session-market-day":
            raise ShadowSessionError("session identity is invalid")
        if len(self.observations) != MAX_SESSION_SCENARIOS or tuple(x.scenario for x in self.observations) != tuple(MarketDayScenario):
            raise ShadowSessionError("session scenarios are incomplete or unordered")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.tzinfo.utcoffset(self.evaluated_at) is None:
            raise ShadowSessionError("evaluation timestamp is invalid")
        if any(value is not False for value in (self.live_ready, self.unattended_ready, self.protection_grounded, self.activation_approved, self.permits_automated_action)):
            raise ShadowSessionError("session readiness or permission is invalid")

    @property
    def all_passed(self) -> bool:
        return all(item.passed for item in self.observations)

    def to_dict(self) -> dict[str, object]:
        return {"session_id": self.session_id, "observations": [x.to_dict() for x in self.observations], "evaluated_at": self.evaluated_at.isoformat(), "all_passed": self.all_passed, "live_ready": False, "unattended_ready": False, "protection_grounded": False, "activation_approved": False, "permits_automated_action": False}

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _calendar(scenario: MarketDayScenario, fixture: OfflineFixturePackage, moment: datetime, expected: CalendarSession) -> SessionObservation:
    result = classify(moment)
    if result.session is not expected:
        raise ShadowSessionError("calendar result is unexpected")
    return SessionObservation(scenario, fixture, expected.value, result.session.value, ())


def _early_close(fixture: OfflineFixturePackage) -> SessionObservation:
    before = classify(datetime(2026, 11, 27, 12, 59, tzinfo=_EASTERN))
    exact = classify(datetime(2026, 11, 27, 13, 0, tzinfo=_EASTERN))
    after = classify(datetime(2026, 11, 27, 14, 0, tzinfo=_EASTERN))
    if not (before.session is CalendarSession.REGULAR and before.day_kind is DayKind.EARLY_CLOSE_DAY and before.reason is ClassificationReason.OK):
        raise ShadowSessionError("early-close pre-boundary evidence is invalid")
    if not (exact.session is CalendarSession.CLOSED and exact.day_kind is DayKind.EARLY_CLOSE_DAY and exact.reason is ClassificationReason.AFTER_EARLY_CLOSE):
        raise ShadowSessionError("early-close boundary evidence is invalid")
    if after != exact:
        raise ShadowSessionError("early-close post-boundary evidence is invalid")
    return SessionObservation(MarketDayScenario.EARLY_CLOSE_BOUNDARY, fixture, "CLOSED", exact.session.value, ())


def _unsupported(fixture: OfflineFixturePackage) -> SessionObservation:
    result = classify(datetime(2030, 1, 2, 10, tzinfo=_EASTERN))
    if not (result.session is CalendarSession.UNKNOWN and result.day_kind is DayKind.UNSUPPORTED and result.reason is ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE):
        raise ShadowSessionError("unsupported-date evidence is invalid")
    return SessionObservation(MarketDayScenario.UNSUPPORTED_DATE, fixture, "UNKNOWN", result.session.value, ())


def _typed_rehearsal(scenario: MarketDayScenario, fixture: OfflineFixturePackage, result: ScenarioResult, source: str, expected: str) -> SessionObservation:
    if result.scenario != source or not result.passed or not result.privacy_valid or not result.safety_invariants:
        raise ShadowSessionError("rehearsal evidence is not bound to its source")
    return SessionObservation(scenario, fixture, expected, result.actual_state, result.durable_files)


def run_bounded_shadow_session(root: Path, *, tick_timestamps: tuple[datetime, ...] | None = None, maximum_tick_count: int = 2) -> BoundedShadowSessionReport:
    """Run one finite synthetic day; every supplied timestamp is runner input."""
    if not isinstance(maximum_tick_count, int) or isinstance(maximum_tick_count, bool) or maximum_tick_count < 1:
        raise ShadowSessionError("maximum tick count is invalid")
    ticks = (SESSION_NOW - timedelta(minutes=1), SESSION_NOW) if tick_timestamps is None else tick_timestamps
    if not isinstance(ticks, tuple) or len(ticks) != 2 or len(ticks) > maximum_tick_count:
        raise ShadowSessionError("tick schedule is invalid")
    if any(not isinstance(x, datetime) or x.tzinfo is None for x in ticks) or any(a >= b for a, b in zip(ticks, ticks[1:])):
        raise ShadowSessionError("tick schedule is invalid")
    selected_root = Path(root).resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if selected_root.exists() or REPOSITORY_ROOT in selected_root.parents or temp_root not in selected_root.parents:
        raise ShadowSessionError("session root must be a new temporary directory")
    selected_root.mkdir(parents=True)
    packages = {item: build_fixture(item, tick_timestamps=ticks) for item in available_scenarios()}
    rehearsal: RehearsalReport = run_rehearsal(
        selected_root / "rehearsal", tick_timestamps=ticks,
        maximum_tick_count=maximum_tick_count,
    )
    if not all(item.passed for item in rehearsal.scenarios):
        raise ShadowSessionError("underlying rehearsal did not pass")
    by_name = {item.scenario: item for item in rehearsal.scenarios}
    obs = (
        _calendar(MarketDayScenario.PRE_MARKET_WAIT, packages[FixtureScenario.SAFE_OFFLINE_COMPLETION], datetime(2026,8,3,5,tzinfo=_EASTERN), CalendarSession.PRE_MARKET),
        _calendar(MarketDayScenario.REGULAR_SESSION_CALENDAR_BOUNDARY, packages[FixtureScenario.SAFE_OFFLINE_COMPLETION], datetime(2026,8,3,10,tzinfo=_EASTERN), CalendarSession.REGULAR),
        _typed_rehearsal(MarketDayScenario.OPERATOR_REVIEW_STOP, packages[FixtureScenario.OPERATOR_REVIEW_REQUIRED], by_name["operator_review_required"], "operator_review_required", "STOPPED_ON_REVIEW"),
        _typed_rehearsal(MarketDayScenario.SUSPENSION_STOP, packages[FixtureScenario.ACTIVE_SUSPENSION], by_name["active_suspension"], "active_suspension", "STOPPED_ON_SUSPENSION"),
        _typed_rehearsal(MarketDayScenario.MISSED_HEARTBEAT, packages[FixtureScenario.MISSED_HEARTBEAT], by_name["missed_heartbeat"], "missed_heartbeat", "SUSPENSION_REQUIRED"),
        _typed_rehearsal(MarketDayScenario.FAILED_ALERT_CONFIRMATION, packages[FixtureScenario.ALERT_DELIVERY_UNCONFIRMED], by_name["failed_alert_confirmation"], "failed_alert_confirmation", "DELIVERY_UNCONFIRMED"),
        _typed_rehearsal(MarketDayScenario.STALE_EVIDENCE, packages[FixtureScenario.STALE_EVIDENCE], by_name[FixtureScenario.STALE_EVIDENCE.value.lower()], "stale_evidence", "STALE"),
        _typed_rehearsal(MarketDayScenario.DURABLE_RESTART_RECOVERY, packages[FixtureScenario.DURABLE_RESTART], by_name["restart_and_recovery"], "restart_and_recovery", "RECOVERED"),
        _early_close(packages[FixtureScenario.SAFE_OFFLINE_COMPLETION]),
        _unsupported(packages[FixtureScenario.SAFE_OFFLINE_COMPLETION]),
    )
    return BoundedShadowSessionReport("shadow-session-market-day", obs)


def main(argv: Sequence[str] | None = None) -> int:
    values = tuple(argv or ())
    if len(values) != 2 or values[0] != "--root":
        return 50
    try:
        report = run_bounded_shadow_session(Path(values[1]))
    except Exception:
        return 30
    print(report.serialize())
    return 0 if report.all_passed else 30


__all__ = ["BoundedShadowSessionReport", "MarketDayScenario", "SessionObservation", "ShadowSessionError", "run_bounded_shadow_session"]
