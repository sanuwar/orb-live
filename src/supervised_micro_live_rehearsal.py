"""Deterministic attended commissioning rehearsal that stops before broker I/O."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.attended_commissioning_handoff import (
    AttendedCommissioningHandoff,
    AttendedCommissioningHandoffState,
    compute_attended_commissioning_handoff_digest,
)

from src.protection_observation_model import canonical_bytes


SUPERVISED_MICRO_LIVE_REHEARSAL_POLICY_VERSION = "supervised-micro-live-rehearsal-v1"
BROKER_MUTATION_BOUNDARY = "RobinhoodCommissioningAdapter._call()"
_DIGEST_DOMAIN = b"orb-live/supervised-micro-live-rehearsal/v1"
_AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
PROTECTION_REHEARSAL_SEQUENCE = (
    "CANDIDATE_ACCEPTED",
    "ENTRY_ACTION_REVIEWED",
    "EXPLICIT_ATTENDED_ENTRY_AUTHORIZATION_REQUIRED",
    "ENTRY_PLACEMENT_WOULD_OCCUR",
    "ENTRY_FILL_RECONCILIATION_REQUIRED",
    "PROTECTIVE_STOP_REVIEW_REQUIRED",
    "FRESH_STOP_SAFETY_DECISION_REQUIRED",
    "EXPLICIT_ATTENDED_STOP_AUTHORIZATION_REQUIRED",
    "STOP_PLACEMENT_WOULD_OCCUR",
    "STOP_RECONCILIATION_REQUIRED",
)


class SupervisedMicroLiveRehearsalState(str, Enum):
    READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION = "READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION"
    REHEARSAL_BLOCKED = "REHEARSAL_BLOCKED"
    HANDOFF_STALE = "HANDOFF_STALE"
    HANDOFF_INVALID = "HANDOFF_INVALID"


@dataclass(frozen=True)
class SupervisedMicroLiveRehearsal:
    rehearsal_id: str
    rehearsal_digest: str
    source_handoff_id: str
    source_shadow_decision_id: str
    source_review_id: str
    source_dry_run_id: str
    source_live_preflight_id: str
    source_safety_evaluation_id: str
    symbol: str
    trading_date: str
    quantity: str
    side: str
    order_type: str
    intended_entry: str
    intended_stop: str
    intended_target: str | None
    safety_decision_result: str
    safety_decision_freshness: str
    action_identity: str
    protection_sequence: tuple[str, ...]
    broker_mutation_boundary: str
    evidence_source: str
    blocking_reasons: tuple[str, ...]
    rehearsal_state: SupervisedMicroLiveRehearsalState
    protection_grounded: bool
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.rehearsal_id != self.rehearsal_digest:
            raise ValueError("rehearsal identity must equal digest")
        if self.quantity != "1":
            raise ValueError("rehearsal quantity must remain exactly one share")
        if self.protection_grounded is not False:
            raise ValueError("rehearsal cannot ground protection")
        if self.execution_authorized is not False or self.broker_write_count != 0:
            raise ValueError("rehearsal cannot authorize or record a broker write")
        if self.authority_ceiling != _AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _fields(value: SupervisedMicroLiveRehearsal) -> dict[str, Any]:
    return {
        "source_handoff_id": value.source_handoff_id,
        "source_shadow_decision_id": value.source_shadow_decision_id,
        "source_review_id": value.source_review_id,
        "source_dry_run_id": value.source_dry_run_id,
        "source_live_preflight_id": value.source_live_preflight_id,
        "source_safety_evaluation_id": value.source_safety_evaluation_id,
        "symbol": value.symbol, "trading_date": value.trading_date,
        "quantity": value.quantity, "side": value.side, "order_type": value.order_type,
        "intended_entry": value.intended_entry, "intended_stop": value.intended_stop,
        "intended_target": value.intended_target or "",
        "safety_decision_result": value.safety_decision_result,
        "safety_decision_freshness": value.safety_decision_freshness,
        "action_identity": value.action_identity,
        "protection_sequence": value.protection_sequence,
        "broker_mutation_boundary": value.broker_mutation_boundary,
        "evidence_source": value.evidence_source,
        "blocking_reasons": value.blocking_reasons,
        "rehearsal_state": value.rehearsal_state.value,
        "protection_grounded": False, "execution_authorized": False,
        "broker_write_count": 0, "authority_ceiling": value.authority_ceiling,
    }


def compute_supervised_micro_live_rehearsal_digest(value: SupervisedMicroLiveRehearsal) -> str:
    if not isinstance(value, SupervisedMicroLiveRehearsal):
        raise TypeError("value must be a SupervisedMicroLiveRehearsal")
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00rehearsal\x00" + canonical_bytes(_fields(value))).hexdigest()


def build_supervised_micro_live_rehearsal(
    handoff: AttendedCommissioningHandoff, *, evidence_source: str = "SAVED_ARTIFACT",
) -> SupervisedMicroLiveRehearsal:
    """Prepare the final attended gate without importing or calling any broker adapter."""
    if not isinstance(handoff, AttendedCommissioningHandoff):
        raise TypeError("handoff must be an AttendedCommissioningHandoff")
    reasons: list[str] = []
    if compute_attended_commissioning_handoff_digest(handoff) != handoff.handoff_digest:
        reasons.append("HANDOFF_DIGEST_MISMATCH")
    if handoff.quantity != "1":
        reasons.append("QUANTITY_INVALID")
    if handoff.handoff_state is AttendedCommissioningHandoffState.SAFETY_DECISION_STALE:
        reasons.append("SAFETY_DECISION_STALE")
    elif handoff.handoff_state is not AttendedCommissioningHandoffState.HANDOFF_READY:
        reasons.append("HANDOFF_NOT_READY")
    if handoff.safety_decision_result != "ALLOW":
        reasons.append("SAFETY_DECISION_NOT_ALLOW")
    if handoff.safety_decision_freshness != "FRESH":
        reasons.append("SAFETY_DECISION_STALE")
    if handoff.acknowledgement_state != "SATISFIED":
        reasons.append("OPERATOR_ACK_REQUIRED")
    if not handoff.operator_action_required:
        reasons.append("EXPLICIT_OPERATOR_ACTION_REQUIRED")
    reasons = list(dict.fromkeys(reasons))
    if "SAFETY_DECISION_STALE" in reasons:
        state = SupervisedMicroLiveRehearsalState.HANDOFF_STALE
    elif reasons:
        state = SupervisedMicroLiveRehearsalState.HANDOFF_INVALID
    else:
        state = SupervisedMicroLiveRehearsalState.READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION
    provisional = SupervisedMicroLiveRehearsal(
        rehearsal_id="0" * 64, rehearsal_digest="0" * 64,
        source_handoff_id=handoff.handoff_id,
        source_shadow_decision_id=handoff.source_shadow_decision_id,
        source_review_id=handoff.source_review_id, source_dry_run_id=handoff.source_dry_run_id,
        source_live_preflight_id=handoff.source_live_preflight_id,
        source_safety_evaluation_id=handoff.source_safety_evaluation_id,
        symbol=handoff.symbol, trading_date=handoff.trading_date, quantity=handoff.quantity,
        side=handoff.side, order_type=handoff.order_type,
        intended_entry=handoff.intended_entry, intended_stop=handoff.intended_stop,
        intended_target=handoff.intended_target,
        safety_decision_result=handoff.safety_decision_result,
        safety_decision_freshness=handoff.safety_decision_freshness,
        action_identity=handoff.intended_commissioning_action_identity,
        protection_sequence=PROTECTION_REHEARSAL_SEQUENCE,
        broker_mutation_boundary=BROKER_MUTATION_BOUNDARY, evidence_source=evidence_source,
        blocking_reasons=tuple(reasons), rehearsal_state=state,
        protection_grounded=False, execution_authorized=False, broker_write_count=0,
        authority_ceiling=_AUTHORITY_CEILING,
    )
    digest = compute_supervised_micro_live_rehearsal_digest(provisional)
    return SupervisedMicroLiveRehearsal(**{**provisional.__dict__, "rehearsal_id": digest, "rehearsal_digest": digest})


__all__ = [
    "BROKER_MUTATION_BOUNDARY", "PROTECTION_REHEARSAL_SEQUENCE",
    "SUPERVISED_MICRO_LIVE_REHEARSAL_POLICY_VERSION", "SupervisedMicroLiveRehearsal",
    "SupervisedMicroLiveRehearsalState", "build_supervised_micro_live_rehearsal",
    "compute_supervised_micro_live_rehearsal_digest",
]
