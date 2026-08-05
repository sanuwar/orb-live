"""Read-only incident projection and direct JSON/HTML export."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.operator_event_timeline import EventSeverity, EventType, OperatorEventTimeline, TimelineError, TimelineNotFound
from src.operator_snapshot_history import SnapshotHistory
from src.supervised_operator_console import ConsoleError, ConsoleSafetyError, load_console_snapshot


INCIDENT_SCHEMA_VERSION = 1
INCIDENT_KEYS = frozenset({
    "schema_version", "incident_id", "source_event_id", "incident_type", "severity", "opened_at", "latest_observed_at",
    "active", "source_component", "headline", "detail_code", "blocker_reasons", "recommended_operator_action",
    "supporting_snapshot_ids", "supporting_event_ids", "operational_status", "supervised_readiness_status",
    "evidence_completeness", "evidence_freshness", "strategy_enabled", "activation_approvals_present",
    "protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready",
})
ADVERSE_EVENT_TYPES = frozenset({
    EventType.SUSPENSION_ACTIVATED.value, EventType.HEARTBEAT_BECAME_STALE.value,
    EventType.ALERT_DELIVERY_UNCONFIRMED.value, EventType.OPERATOR_REVIEW_REQUIRED.value,
    EventType.AMBIGUOUS_RECOVERY_DETECTED.value, EventType.READINESS_BLOCKER_ADDED.value,
})
_INCIDENT_TYPE = {
    EventType.SUSPENSION_ACTIVATED.value: "ACTIVE_SUSPENSION", EventType.HEARTBEAT_BECAME_STALE.value: "STALE_HEARTBEAT",
    EventType.ALERT_DELIVERY_UNCONFIRMED.value: "ALERT_DELIVERY_UNCONFIRMED", EventType.OPERATOR_REVIEW_REQUIRED.value: "OPERATOR_REVIEW_REQUIRED",
    EventType.AMBIGUOUS_RECOVERY_DETECTED.value: "AMBIGUOUS_RECOVERY", EventType.READINESS_BLOCKER_ADDED.value: "EVIDENCE_INCOMPLETE",
}


class IncidentError(TimelineError):
    """The incident cannot be safely projected."""


class IncidentNotFound(IncidentError):
    """The incident ID is absent or not adverse."""


@dataclass(frozen=True)
class OperatorIncident:
    schema_version: int
    incident_id: str
    source_event_id: str
    incident_type: str
    severity: str
    opened_at: str
    latest_observed_at: str
    active: str
    source_component: str
    headline: str
    detail_code: str
    blocker_reasons: tuple[str, ...]
    recommended_operator_action: str
    supporting_snapshot_ids: tuple[str, ...]
    supporting_event_ids: tuple[str, ...]
    operational_status: str
    supervised_readiness_status: str
    evidence_completeness: str
    evidence_freshness: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "incident_id": self.incident_id, "source_event_id": self.source_event_id,
            "incident_type": self.incident_type, "severity": self.severity, "opened_at": self.opened_at,
            "latest_observed_at": self.latest_observed_at, "active": self.active, "source_component": self.source_component,
            "headline": self.headline, "detail_code": self.detail_code, "blocker_reasons": list(self.blocker_reasons),
            "recommended_operator_action": self.recommended_operator_action, "supporting_snapshot_ids": list(self.supporting_snapshot_ids),
            "supporting_event_ids": list(self.supporting_event_ids), "operational_status": self.operational_status,
            "supervised_readiness_status": self.supervised_readiness_status, "evidence_completeness": self.evidence_completeness,
            "evidence_freshness": self.evidence_freshness, "strategy_enabled": False, "activation_approvals_present": False,
            "protection_grounded": False, "activation_approved": False, "permits_automated_action": False,
            "live_ready": False, "unattended_ready": False,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"


def _current_adverse(incident_type: str, current) -> bool:
    if current.evidence_completeness != "COMPLETE" or current.evidence_freshness != "CURRENT":
        return True
    return {
        "ACTIVE_SUSPENSION": current.suspension_status in {"ACTIVE", "SUSPENDED"},
        "STALE_HEARTBEAT": current.heartbeat_status in {"STALE", "MISSED"},
        "ALERT_DELIVERY_UNCONFIRMED": current.alert_status in {"UNCONFIRMED", "DELIVERY_UNCONFIRMED"},
        "OPERATOR_REVIEW_REQUIRED": current.acknowledgment_status in {"PENDING", "REVIEW_REQUIRED"},
        "AMBIGUOUS_RECOVERY": current.recovery_status in {"AMBIGUOUS", "REVIEW_REQUIRED", "FAILED_CLOSED"},
        "EVIDENCE_INCOMPLETE": bool(current.blocker_reasons),
    }.get(incident_type, True)


class OperatorIncidentStore:
    def __init__(self, *, runtime_root: Path, repository_root: Path):
        self.runtime_root = Path(runtime_root)
        self.repository_root = Path(repository_root)
        self.history = SnapshotHistory(runtime_root=self.runtime_root, repository_root=self.repository_root)
        self.timeline = OperatorEventTimeline(self.history)

    def get(self, *, incident_id: str, as_of) -> OperatorIncident:
        if not incident_id.startswith("incident-event-") or len(incident_id) > 220:
            raise IncidentNotFound("incident id is invalid")
        event_id = incident_id.removeprefix("incident-")
        try:
            event = self.timeline.get(event_id=event_id, as_of=as_of)
        except TimelineNotFound as exc:
            raise IncidentNotFound("source event is absent") from exc
        if event.event_type.value not in ADVERSE_EVENT_TYPES or event.severity is EventSeverity.INFO:
            raise IncidentNotFound("source event is not adverse")
        try:
            source = self.history.get(snapshot_id=event.source_snapshot_id, as_of=as_of)
            current = load_console_snapshot(runtime_root=self.runtime_root, as_of=as_of, repository_root=self.repository_root)
        except (TimelineError, ConsoleError, ConsoleSafetyError) as exc:
            raise IncidentError("supporting evidence is unavailable") from exc
        incident_type = _INCIDENT_TYPE[event.event_type.value]
        active = "ACTIVE" if _current_adverse(incident_type, current) else "NO_LONGER_OBSERVED"
        latest = current.generated_as_of if active == "ACTIVE" else event.occurred_at
        return OperatorIncident(
            schema_version=INCIDENT_SCHEMA_VERSION, incident_id=incident_id, source_event_id=event.event_id,
            incident_type=incident_type, severity=event.severity.value, opened_at=event.occurred_at, latest_observed_at=latest,
            active=active, source_component=event.source_component, headline=event.headline, detail_code=event.detail_code,
            blocker_reasons=event.blocker_reasons, recommended_operator_action=event.operator_action,
            supporting_snapshot_ids=(source.snapshot_id,), supporting_event_ids=(event.event_id,),
            operational_status=current.operational_status, supervised_readiness_status=current.supervised_readiness_status,
            evidence_completeness=current.evidence_completeness, evidence_freshness=current.evidence_freshness,
        )


def render_incident_html(incident: OperatorIncident, *, template_path: Path) -> str:
    template = template_path.read_text(encoding="utf-8")
    data = incident.to_dict()
    values = {
        "INCIDENT_ID": data["incident_id"], "INCIDENT_TYPE": data["incident_type"], "SEVERITY": data["severity"], "OPENED": data["opened_at"],
        "LATEST": data["latest_observed_at"], "ACTIVE": data["active"], "COMPONENT": data["source_component"],
        "HEADLINE": data["headline"], "CODE": data["detail_code"], "BLOCKERS": ", ".join(data["blocker_reasons"]) or "None",
        "ACTION": data["recommended_operator_action"], "SNAPSHOTS": ", ".join(data["supporting_snapshot_ids"]),
        "EVENTS": ", ".join(data["supporting_event_ids"]), "STATUS": data["operational_status"],
        "READINESS": data["supervised_readiness_status"], "COMPLETENESS": data["evidence_completeness"],
        "FRESHNESS": data["evidence_freshness"],
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", html.escape(str(value)))
    return template


__all__ = ["ADVERSE_EVENT_TYPES", "INCIDENT_KEYS", "IncidentError", "IncidentNotFound", "OperatorIncident", "OperatorIncidentStore", "render_incident_html"]
