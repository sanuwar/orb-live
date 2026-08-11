"""Non-writing reconciliation and protective-stop preparation rehearsal."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from src.attended_commissioning_handoff import (
    AttendedCommissioningHandoff, AttendedCommissioningHandoffState,
    compute_attended_commissioning_handoff_digest,
)
from src.broker_snapshot import BrokerSnapshot, snapshot_digest, validate_snapshot
from src.attended_safety_evaluation_bridge import (
    PROTECTIVE_STOP_MUTATION_BOUNDARY, ProtectiveStopSafetyInputs, ProtectiveStopSafetyState,
    evaluate_protective_stop_safety,
)
from src.order_lifecycle_model import LifecycleState, normalize_label
from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest

POLICY_VERSION = "post-entry-protection-rehearsal-v1"
_DOMAIN = b"orb-live/post-entry-protection-rehearsal/v1"
MAX_SUSPENSION_AGE = timedelta(seconds=30)
AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"


class ProtectionRehearsalState(str, Enum):
    PROTECTION_REHEARSAL_READY = "PROTECTION_REHEARSAL_READY"
    ENTRY_NOT_FOUND = "ENTRY_NOT_FOUND"
    ENTRY_NOT_FILLED = "ENTRY_NOT_FILLED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    PARTIAL_FILL_UNSUPPORTED = "PARTIAL_FILL_UNSUPPORTED"
    ENTRY_IDENTITY_MISMATCH = "ENTRY_IDENTITY_MISMATCH"
    ACCOUNT_OBSERVATION_BLOCKED = "ACCOUNT_OBSERVATION_BLOCKED"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    SUSPENSION_BLOCKED = "SUSPENSION_BLOCKED"
    ACKNOWLEDGEMENTS_INCOMPLETE = "ACKNOWLEDGEMENTS_INCOMPLETE"
    PROTECTION_PLAN_MISMATCH = "PROTECTION_PLAN_MISMATCH"
    SAFETY_DENY = "SAFETY_DENY"
    SAFETY_INCOMPLETE = "SAFETY_INCOMPLETE"
    SAFETY_STALE = "SAFETY_STALE"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"


@dataclass(frozen=True)
class PostEntryProtectionRehearsal:
    rehearsal_id: str
    rehearsal_digest: str
    source_handoff_id: str
    source_shadow_decision_id: str
    entry_action_identity: str
    entry_order_id: str | None
    entry_snapshot_digest: str | None
    symbol: str
    filled_quantity: str
    entry_lifecycle_state: str
    stop_price: str
    protective_quantity: str
    protective_action_identity: str | None
    account_fingerprint_reference: str
    suspension_state: str
    suspension_assessed_at: datetime | None
    safety_context_identity: str | None
    safety_decision_identity: str | None
    safety_result: str
    release_sha: str
    implementation_digest: str
    blocking_reasons: tuple[str, ...]
    rehearsal_state: ProtectionRehearsalState
    mutation_boundary: str
    broker_write_count: int
    execution_authorized: bool
    protection_grounded: bool
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.rehearsal_id != self.rehearsal_digest:
            raise ValueError("rehearsal identity must equal digest")
        if self.broker_write_count != 0 or self.execution_authorized or self.protection_grounded:
            raise ValueError("rehearsal cannot write, authorize, or ground protection")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _fields(value: PostEntryProtectionRehearsal) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name in value.__dataclass_fields__:
        if name in {"rehearsal_id", "rehearsal_digest"}:
            continue
        item = getattr(value, name)
        fields[name] = item.isoformat() if isinstance(item, datetime) else (
            item.value if isinstance(item, Enum) else item
        )
    return fields


def compute_post_entry_protection_rehearsal_digest(value: PostEntryProtectionRehearsal) -> str:
    if not isinstance(value, PostEntryProtectionRehearsal):
        raise TypeError("value must be a PostEntryProtectionRehearsal")
    return hashlib.sha256(_DOMAIN + b"\0rehearsal\0" + canonical_bytes(_fields(value))).hexdigest()


def _state_for(reasons: list[str]) -> ProtectionRehearsalState:
    mapping = {
        "ENTRY_NOT_FOUND": ProtectionRehearsalState.ENTRY_NOT_FOUND,
        "ENTRY_NOT_FILLED": ProtectionRehearsalState.ENTRY_NOT_FILLED,
        "ENTRY_REJECTED": ProtectionRehearsalState.ENTRY_REJECTED,
        "ENTRY_CANCELLED": ProtectionRehearsalState.ENTRY_CANCELLED,
        "PARTIAL_FILL_UNSUPPORTED": ProtectionRehearsalState.PARTIAL_FILL_UNSUPPORTED,
        "ENTRY_IDENTITY_MISMATCH": ProtectionRehearsalState.ENTRY_IDENTITY_MISMATCH,
        "SNAPSHOT_STALE": ProtectionRehearsalState.SNAPSHOT_STALE,
        "ACCOUNT_MISMATCH": ProtectionRehearsalState.ACCOUNT_MISMATCH,
        "ACCOUNT_OBSERVATION_BLOCKED": ProtectionRehearsalState.ACCOUNT_OBSERVATION_BLOCKED,
        "SUSPENSION_BLOCKED": ProtectionRehearsalState.SUSPENSION_BLOCKED,
        "ACKNOWLEDGEMENTS_INCOMPLETE": ProtectionRehearsalState.ACKNOWLEDGEMENTS_INCOMPLETE,
        "PROTECTION_PLAN_MISMATCH": ProtectionRehearsalState.PROTECTION_PLAN_MISMATCH,
        "SAFETY_STALE": ProtectionRehearsalState.SAFETY_STALE,
        "SAFETY_DENY": ProtectionRehearsalState.SAFETY_DENY,
        "SAFETY_INCOMPLETE": ProtectionRehearsalState.SAFETY_INCOMPLETE,
        "SOURCE_MISMATCH": ProtectionRehearsalState.SOURCE_MISMATCH,
    }
    return next((mapping[item] for item in reasons if item in mapping), ProtectionRehearsalState.PROTECTION_REHEARSAL_READY)


def build_post_entry_protection_rehearsal(
    handoff: AttendedCommissioningHandoff, *, snapshot: BrokerSnapshot,
    entry_order_id: str, suspension_decision: Any, suspension_assessed_at: datetime,
    governance_evidence: Any, evaluated_at: datetime,
    release_sha: str, trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> PostEntryProtectionRehearsal:
    """Prepare a fresh stop safety evaluation; never talks to an adapter."""
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(root or Path(__file__).resolve().parents[1])
    reasons: list[str] = []
    validation = validate_snapshot(snapshot, evaluated_at=evaluated_at)
    digest = validation.snapshot_digest
    if not validation.valid:
        reasons.append("SNAPSHOT_STALE" if validation.result_code == "stale_snapshot" else "ACCOUNT_OBSERVATION_BLOCKED")
    if compute_attended_commissioning_handoff_digest(handoff) != handoff.handoff_digest or handoff.handoff_state is not AttendedCommissioningHandoffState.HANDOFF_READY:
        reasons.append("SOURCE_MISMATCH")
    if handoff.implementation_digest != trusted or handoff.release_sha != release_sha:
        reasons.append("SOURCE_MISMATCH")
    if snapshot.metadata.account_fingerprint != handoff.account_fingerprint_reference:
        reasons.append("ACCOUNT_MISMATCH")
    if governance_evidence.account_fingerprint_reference != handoff.account_fingerprint_reference or not governance_evidence.account_fingerprint_matched:
        reasons.append("ACCOUNT_MISMATCH")
    if suspension_assessed_at.tzinfo is None or suspension_assessed_at > evaluated_at or evaluated_at - suspension_assessed_at > MAX_SUSPENSION_AGE:
        reasons.append("SUSPENSION_BLOCKED")
    state_value = getattr(getattr(suspension_decision, "state", None), "value", getattr(suspension_decision, "state", ""))
    if state_value != "CLEAR" or getattr(suspension_decision, "permits_automated_action", True):
        reasons.append("SUSPENSION_BLOCKED")
    if governance_evidence.operator_acknowledgements is None or not governance_evidence.operator_acknowledgements.all_satisfied():
        reasons.append("ACKNOWLEDGEMENTS_INCOMPLETE")

    rows = [order for order in snapshot.account_state.open_orders if order.order_id == entry_order_id]
    order = rows[0] if len(rows) == 1 else None
    lifecycle = LifecycleState.UNKNOWN
    filled = Decimal("0")
    if order is None:
        reasons.append("ENTRY_NOT_FOUND")
    else:
        lifecycle = normalize_label(order.state)
        filled = order.cumulative_quantity
        if order.symbol != handoff.symbol or order.side != "buy" or order.order_type != "market" or order.quantity != Decimal("1"):
            reasons.append("ENTRY_IDENTITY_MISMATCH")
        elif lifecycle is LifecycleState.REJECTED:
            reasons.append("ENTRY_REJECTED")
        elif lifecycle is LifecycleState.CANCELED:
            reasons.append("ENTRY_CANCELLED")
        elif lifecycle is LifecycleState.PARTIALLY_FILLED or filled != Decimal("1"):
            reasons.append("PARTIAL_FILL_UNSUPPORTED" if filled > 0 else "ENTRY_NOT_FILLED")
        elif lifecycle is not LifecycleState.FILLED:
            reasons.append("ENTRY_NOT_FILLED")
    try:
        stop = Decimal(handoff.intended_stop)
        entry = Decimal(handoff.intended_entry)
        if stop <= 0 or stop >= entry:
            reasons.append("PROTECTION_PLAN_MISMATCH")
    except (InvalidOperation, ValueError):
        stop = Decimal("0")
        reasons.append("PROTECTION_PLAN_MISMATCH")

    bridge_result = None
    safety_run_id = hashlib.sha256(
        _DOMAIN + b"\0protective_action\0" + canonical_bytes({
            "handoff_id": handoff.handoff_id,
            "candidate_id": handoff.source_shadow_decision_id,
            "entry_action_identity": handoff.intended_commissioning_action_identity,
            "entry_order_id": "" if order is None else order.order_id,
            "snapshot_digest": digest,
        })
    ).hexdigest()
    if not reasons:
        bridge_result = evaluate_protective_stop_safety(ProtectiveStopSafetyInputs(
            decision_time=evaluated_at, run_id=safety_run_id,
            source_shadow_decision_id=handoff.source_shadow_decision_id,
            source_handoff_id=handoff.handoff_id,
            entry_action_identity=handoff.intended_commissioning_action_identity,
            entry_order_identity=order.order_id,
            broker_snapshot_identity=digest, symbol=handoff.symbol,
            authoritative_filled_quantity=format(filled, "f"), protective_quantity="1",
            stop_price=format(stop, "f"), implementation_digest=trusted,
            release_sha=release_sha,
            account_fingerprint_reference=handoff.account_fingerprint_reference,
            suspension_state=state_value,
            operator_acknowledgements=governance_evidence.operator_acknowledgements,
            operator_attended=governance_evidence.operator_attended,
            source_hash_verified=governance_evidence.source_hash_verified,
        ))
        if bridge_result.safety_result is ProtectiveStopSafetyState.INCOMPLETE:
            reasons.append("SAFETY_INCOMPLETE")
        elif bridge_result.safety_result is ProtectiveStopSafetyState.DENY:
            reasons.append("SAFETY_DENY")
    state = _state_for(reasons)
    preliminary = PostEntryProtectionRehearsal(
        rehearsal_id="0" * 64, rehearsal_digest="0" * 64, source_handoff_id=handoff.handoff_id,
        source_shadow_decision_id=handoff.source_shadow_decision_id,
        entry_action_identity=handoff.intended_commissioning_action_identity,
        entry_order_id=None if order is None else order.order_id, entry_snapshot_digest=digest,
        symbol=handoff.symbol, filled_quantity=format(filled, "f"), entry_lifecycle_state=lifecycle.value,
        stop_price=format(stop, "f"), protective_quantity="1" if filled >= Decimal("1") else "0",
        protective_action_identity=None if bridge_result is None else bridge_result.protective_action_identity,
        account_fingerprint_reference=handoff.account_fingerprint_reference, suspension_state=str(state_value),
        suspension_assessed_at=suspension_assessed_at, safety_context_identity=None if bridge_result is None else bridge_result.safety_context_identity,
        safety_decision_identity=None if bridge_result is None else bridge_result.safety_decision_identity,
        safety_result="NOT_EVALUATED" if bridge_result is None else bridge_result.safety_result.value,
        release_sha=release_sha, implementation_digest=trusted, blocking_reasons=tuple(dict.fromkeys(reasons)),
        rehearsal_state=state, mutation_boundary=PROTECTIVE_STOP_MUTATION_BOUNDARY,
        broker_write_count=0, execution_authorized=False, protection_grounded=False,
        authority_ceiling=AUTHORITY_CEILING,
    )
    result_digest = compute_post_entry_protection_rehearsal_digest(preliminary)
    return PostEntryProtectionRehearsal(**{**preliminary.__dict__, "rehearsal_id": result_digest, "rehearsal_digest": result_digest})


__all__ = ["POLICY_VERSION", "PostEntryProtectionRehearsal", "ProtectionRehearsalState", "build_post_entry_protection_rehearsal", "compute_post_entry_protection_rehearsal_digest"]
