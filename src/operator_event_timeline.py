"""Read-only observational events derived from validated snapshot transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.operator_snapshot_history import HistoryError, HistoryItem, SnapshotHistory
from src.supervised_operator_console import SupervisedOperatorConsoleResult


TIMELINE_SCHEMA_VERSION = 1
EVENT_LIST_KEYS = frozenset({"schema_version", "page", "page_size", "total_count", "items"})
EVENT_DETAIL_KEYS = frozenset({"schema_version", "event"})
EVENT_KEYS = frozenset({
    "schema_version", "event_id", "event_type", "severity", "occurred_at", "source_snapshot_id",
    "source_component", "headline", "detail_code", "operational_status", "supervised_readiness_status",
    "blocker_reasons", "operator_action", "permits_automated_action", "live_ready", "unattended_ready",
})


class EventType(str, Enum):
    SUSPENSION_ACTIVATED = "SUSPENSION_ACTIVATED"
    SUSPENSION_CLEARED = "SUSPENSION_CLEARED"
    HEARTBEAT_BECAME_STALE = "HEARTBEAT_BECAME_STALE"
    HEARTBEAT_RECOVERED = "HEARTBEAT_RECOVERED"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    ALERT_DELIVERY_CONFIRMED = "ALERT_DELIVERY_CONFIRMED"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    AMBIGUOUS_RECOVERY_DETECTED = "AMBIGUOUS_RECOVERY_DETECTED"
    RECOVERY_VALIDATED = "RECOVERY_VALIDATED"
    READINESS_BLOCKER_ADDED = "READINESS_BLOCKER_ADDED"
    READINESS_BLOCKER_REMOVED = "READINESS_BLOCKER_REMOVED"


class EventSeverity(str, Enum):
    INFO = "INFO"
    REVIEW = "REVIEW"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


EVENT_TYPE_FILTERS = frozenset(item.value for item in EventType)
SEVERITY_FILTERS = frozenset(item.value for item in EventSeverity)
_SEVERITY = {
    EventType.SUSPENSION_ACTIVATED: EventSeverity.BLOCKED, EventType.SUSPENSION_CLEARED: EventSeverity.INFO,
    EventType.HEARTBEAT_BECAME_STALE: EventSeverity.WARNING, EventType.HEARTBEAT_RECOVERED: EventSeverity.INFO,
    EventType.ALERT_DELIVERY_UNCONFIRMED: EventSeverity.WARNING, EventType.ALERT_DELIVERY_CONFIRMED: EventSeverity.INFO,
    EventType.OPERATOR_REVIEW_REQUIRED: EventSeverity.REVIEW, EventType.AMBIGUOUS_RECOVERY_DETECTED: EventSeverity.BLOCKED,
    EventType.RECOVERY_VALIDATED: EventSeverity.INFO, EventType.READINESS_BLOCKER_ADDED: EventSeverity.BLOCKED,
    EventType.READINESS_BLOCKER_REMOVED: EventSeverity.INFO,
}
_COMPONENT = {
    EventType.SUSPENSION_ACTIVATED: "suspension", EventType.SUSPENSION_CLEARED: "suspension",
    EventType.HEARTBEAT_BECAME_STALE: "heartbeat", EventType.HEARTBEAT_RECOVERED: "heartbeat",
    EventType.ALERT_DELIVERY_UNCONFIRMED: "alerts", EventType.ALERT_DELIVERY_CONFIRMED: "alerts",
    EventType.OPERATOR_REVIEW_REQUIRED: "acknowledgment", EventType.AMBIGUOUS_RECOVERY_DETECTED: "recovery",
    EventType.RECOVERY_VALIDATED: "recovery", EventType.READINESS_BLOCKER_ADDED: "readiness",
    EventType.READINESS_BLOCKER_REMOVED: "readiness",
}


class TimelineError(HistoryError):
    """The timeline cannot prove a safe event projection."""


class TimelineNotFound(TimelineError):
    """The event ID is not present."""


@dataclass(frozen=True)
class OperatorEvent:
    schema_version: int
    event_id: str
    event_type: EventType
    severity: EventSeverity
    occurred_at: str
    source_snapshot_id: str
    source_component: str
    headline: str
    detail_code: str
    operational_status: str
    supervised_readiness_status: str
    blocker_reasons: tuple[str, ...]
    operator_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "event_id": self.event_id, "event_type": self.event_type.value,
            "severity": self.severity.value, "occurred_at": self.occurred_at, "source_snapshot_id": self.source_snapshot_id,
            "source_component": self.source_component, "headline": self.headline, "detail_code": self.detail_code,
            "operational_status": self.operational_status, "supervised_readiness_status": self.supervised_readiness_status,
            "blocker_reasons": list(self.blocker_reasons), "operator_action": self.operator_action,
            "permits_automated_action": False, "live_ready": False, "unattended_ready": False,
        }


def _event(event_type: EventType, current: HistoryItem, identity_suffix: str = "") -> OperatorEvent:
    snapshot = current.snapshot
    compact_time = snapshot.generated_as_of.replace("-", "").replace(":", "").replace("+00:00", "").replace("Z", "").lower()
    suffix = f"-{identity_suffix.lower()}" if identity_suffix else ""
    event_id = f"event-{current.snapshot_id.removeprefix('snapshot-')}-{compact_time}-{event_type.value.lower()}{suffix}"
    return OperatorEvent(
        schema_version=TIMELINE_SCHEMA_VERSION, event_id=event_id, event_type=event_type, severity=_SEVERITY[event_type],
        occurred_at=snapshot.generated_as_of, source_snapshot_id=current.snapshot_id, source_component=_COMPONENT[event_type],
        headline=event_type.value.replace("_", " ").title(), detail_code=event_type.value,
        operational_status=snapshot.operational_status, supervised_readiness_status=snapshot.supervised_readiness_status,
        blocker_reasons=snapshot.blocker_reasons, operator_action=snapshot.recommended_operator_action.value,
    )


def _derive(current: HistoryItem, previous: HistoryItem) -> list[OperatorEvent]:
    now, old = current.snapshot, previous.snapshot
    events: list[OperatorEvent] = []
    if now.suspension_status != old.suspension_status:
        events.append(_event(EventType.SUSPENSION_ACTIVATED if now.suspension_status in {"ACTIVE", "SUSPENDED"} else EventType.SUSPENSION_CLEARED, current))
    if now.heartbeat_status != old.heartbeat_status:
        events.append(_event(EventType.HEARTBEAT_BECAME_STALE if now.heartbeat_status in {"STALE", "MISSED"} else EventType.HEARTBEAT_RECOVERED, current))
    if now.alert_status != old.alert_status:
        events.append(_event(EventType.ALERT_DELIVERY_UNCONFIRMED if now.alert_status in {"UNCONFIRMED", "DELIVERY_UNCONFIRMED"} else EventType.ALERT_DELIVERY_CONFIRMED, current))
    if now.acknowledgment_status != old.acknowledgment_status and now.acknowledgment_status in {"PENDING", "REVIEW_REQUIRED"}:
        events.append(_event(EventType.OPERATOR_REVIEW_REQUIRED, current))
    if now.recovery_status != old.recovery_status:
        events.append(_event(EventType.AMBIGUOUS_RECOVERY_DETECTED if now.recovery_status in {"AMBIGUOUS", "REVIEW_REQUIRED", "FAILED_CLOSED"} else EventType.RECOVERY_VALIDATED, current))
    for blocker in sorted(set(now.blocker_reasons) - set(old.blocker_reasons)):
        events.append(_event(EventType.READINESS_BLOCKER_ADDED, current, blocker))
    for blocker in sorted(set(old.blocker_reasons) - set(now.blocker_reasons)):
        events.append(_event(EventType.READINESS_BLOCKER_REMOVED, current, blocker))
    return events


class OperatorEventTimeline:
    def __init__(self, history: SnapshotHistory):
        self.history = history

    def build(self, *, as_of) -> tuple[OperatorEvent, ...]:
        snapshots = self.history.load(as_of=as_of)
        events: list[OperatorEvent] = []
        for index in range(len(snapshots) - 1):
            events.extend(_derive(snapshots[index], snapshots[index + 1]))
        ids = [event.event_id for event in events]
        if len(ids) != len(set(ids)):
            raise TimelineError("duplicate event identity")
        return tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id), reverse=True))

    def query(self, *, as_of, event_type: str | None = None, severity: str | None = None, from_value: str | None = None, to_value: str | None = None, page: int = 1, page_size: int = 25) -> tuple[tuple[OperatorEvent, ...], int]:
        if event_type is not None and event_type not in EVENT_TYPE_FILTERS:
            raise TimelineError("event type filter is invalid")
        if severity is not None and severity not in SEVERITY_FILTERS:
            raise TimelineError("severity filter is invalid")
        if type(page) is not int or page < 1 or type(page_size) is not int or not 10 <= page_size <= 100:
            raise TimelineError("pagination is invalid")
        from src.operator_snapshot_history import _moment
        try:
            lower = _moment(from_value) if from_value else None
            upper = _moment(to_value) if to_value else None
        except HistoryError as exc:
            raise TimelineError(str(exc)) from None
        if lower and upper and lower > upper:
            raise TimelineError("time range is invalid")
        events = [event for event in self.build(as_of=as_of) if (event_type is None or event.event_type.value == event_type) and (severity is None or event.severity.value == severity)]
        events = [event for event in events if lower is None or _moment(event.occurred_at) >= lower]
        events = [event for event in events if upper is None or _moment(event.occurred_at) <= upper]
        start = (page - 1) * page_size
        return tuple(events[start:start + page_size]), len(events)

    def get(self, *, event_id: str, as_of) -> OperatorEvent:
        if not event_id.startswith("event-") or len(event_id) > 180 or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in event_id):
            raise TimelineNotFound("event id is invalid")
        for event in self.build(as_of=as_of):
            if event.event_id == event_id:
                return event
        raise TimelineNotFound("event is not present")


__all__ = ["EVENT_DETAIL_KEYS", "EVENT_KEYS", "EVENT_LIST_KEYS", "EVENT_TYPE_FILTERS", "EventSeverity", "EventType", "OperatorEvent", "OperatorEventTimeline", "SEVERITY_FILTERS", "TimelineError", "TimelineNotFound", "TIMELINE_SCHEMA_VERSION"]
