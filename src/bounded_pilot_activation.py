"""Read-only M23 commissioning-readiness and pilot-activation evidence.

This module evaluates immutable M19/M20 evidence against the existing M22
policy.  It deliberately does not enable a policy, construct a safety
decision, import a broker adapter, or perform network I/O.  A later explicit
activation milestone must consume the resulting eligible evidence through a
separately reviewed production path.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.bounded_unattended_orchestrator import (
    AUTHORITY_CEILING,
    FIXED_WATCHLIST,
    UnattendedPilotPolicy,
    compute_policy_digest,
)
from src.protection_grounding_assessment import (
    AuthoritativeGroundingOutcome,
    AuthoritativeProtectionGroundingAssessment,
    compute_authoritative_grounding_digest,
)
from src.protection_observation_model import EvidenceSourceClass, canonical_bytes
from src.protective_stop_exit_rehearsal import (
    ProtectiveStopExitRehearsal,
    StopExitRehearsalState,
    compute_protective_stop_exit_rehearsal_digest,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest


ACTIVATION_POLICY_VERSION = "bounded-pilot-activation-v1"
_DOMAIN = b"orb-live/bounded-pilot-activation/v1"
_HEX = frozenset("0123456789abcdef")


class CommissioningReadinessState(str, Enum):
    COMMISSIONING_NOT_READY = "COMMISSIONING_NOT_READY"
    COMMISSIONING_EVIDENCE_INCOMPLETE = "COMMISSIONING_EVIDENCE_INCOMPLETE"
    COMMISSIONING_FAILED = "COMMISSIONING_FAILED"
    COMMISSIONING_READY = "COMMISSIONING_READY"
    ACTIVATION_ELIGIBLE = "ACTIVATION_ELIGIBLE"
    ACTIVATION_BLOCKED = "ACTIVATION_BLOCKED"


@dataclass(frozen=True)
class BoundedPilotActivation:
    activation_id: str
    activation_digest: str
    commissioning_status: CommissioningReadinessState
    commissioning_evidence_id: str
    protection_grounding_assessment_id: str
    account_fingerprint_reference: str
    strategy_id: str
    strategy_version: str
    quantity: int
    symbol_allowlist: tuple[str, ...]
    regular_session_only: bool
    entry_start: str
    entry_end: str
    maximum_trade_attempts_per_day: int
    maximum_risk_per_share: str
    maximum_entry_notional: str
    policy_id: str
    policy_digest: str
    release_sha: str
    implementation_digest: str
    activated_at: datetime
    valid_until: str
    blocking_reasons: tuple[str, ...]
    activation_approved: bool = False
    permits_automated_action: bool = False
    live_ready: bool = False
    unattended_ready: bool = False
    execution_authorized: bool = False
    broker_write_count: int = 0
    protection_grounded: bool = False
    authority_ceiling: str = AUTHORITY_CEILING

    def __post_init__(self) -> None:
        if self.activation_id != self.activation_digest:
            raise ValueError("activation identity must equal digest")
        if self.activation_digest != compute_bounded_pilot_activation_digest(self):
            raise ValueError("activation digest mismatch")
        if self.activated_at.tzinfo is None:
            raise ValueError("activation timestamp must be timezone-aware")
        if self.quantity != 1 or self.symbol_allowlist != FIXED_WATCHLIST:
            raise ValueError("activation must preserve bounded pilot limits")
        if any((
            self.activation_approved, self.permits_automated_action,
            self.live_ready, self.unattended_ready, self.execution_authorized,
            self.broker_write_count != 0, self.protection_grounded,
        )):
            raise ValueError("M23 activation evidence cannot grant runtime authority")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _payload(value: BoundedPilotActivation) -> dict[str, Any]:
    return {
        name: (
            item.value if isinstance(item, Enum) else
            item.isoformat() if isinstance(item, datetime) else
            list(item) if isinstance(item, tuple) else item
        )
        for name, item in value.__dict__.items()
        if name not in {"activation_id", "activation_digest"}
    }


def _compute_payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        _DOMAIN + b"\0activation\0" + canonical_bytes(payload)
    ).hexdigest()


def compute_bounded_pilot_activation_digest(value: BoundedPilotActivation) -> str:
    if not isinstance(value, BoundedPilotActivation):
        raise TypeError("value must be BoundedPilotActivation")
    return _compute_payload_digest(_payload(value))


def _sha(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(ch in _HEX for ch in value)


def _reason_state(reasons: list[str]) -> CommissioningReadinessState:
    if not reasons:
        return CommissioningReadinessState.ACTIVATION_ELIGIBLE
    if any(reason.endswith("DIGEST_MISMATCH") or reason.endswith("MISMATCH") for reason in reasons):
        return CommissioningReadinessState.ACTIVATION_BLOCKED
    if any(reason in {"COMMISSIONING_EXIT_NOT_RECONCILED", "GROUNDING_NOT_PROVEN"} for reason in reasons):
        return CommissioningReadinessState.COMMISSIONING_EVIDENCE_INCOMPLETE
    if any(reason.endswith("FAILED") or reason.endswith("UNKNOWN") for reason in reasons):
        return CommissioningReadinessState.COMMISSIONING_FAILED
    return CommissioningReadinessState.COMMISSIONING_NOT_READY


def assess_bounded_pilot_activation(
    policy: UnattendedPilotPolicy,
    *, completed_commissioning: ProtectiveStopExitRehearsal | None,
    protection_grounding: AuthoritativeProtectionGroundingAssessment | None,
    activated_at: datetime,
    release_sha: str,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> BoundedPilotActivation:
    """Bind one completed, broker-authoritative supervised lifecycle to M22.

    Eligibility is evidence only.  This function never changes M22's disabled
    policy fields and does not confer execution authority.
    """
    if not isinstance(policy, UnattendedPilotPolicy):
        raise TypeError("policy must be UnattendedPilotPolicy")
    if activated_at.tzinfo is None:
        raise ValueError("activated_at must be timezone-aware")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(
        root or Path(__file__).resolve().parents[1]
    )
    reasons: list[str] = []
    if policy.policy_digest != compute_policy_digest(policy):
        reasons.append("POLICY_DIGEST_MISMATCH")
    if not all((
        policy.strategy_id == "orb_long_v0",
        not policy.enabled,
        not policy.activation_approved,
        not policy.permits_automated_action,
        policy.quantity == 1,
        policy.symbol_allowlist == FIXED_WATCHLIST,
        policy.entry_start == "09:40", policy.entry_end == "11:00",
        policy.maximum_trade_attempts_per_day == 1,
        policy.release_sha == release_sha,
        policy.implementation_digest == trusted,
    )):
        reasons.append("POLICY_RUNTIME_MISMATCH")

    commissioning_id = ""
    grounding_id = ""
    if completed_commissioning is None:
        reasons.append("COMMISSIONING_EVIDENCE_MISSING")
    else:
        commissioning_id = completed_commissioning.rehearsal_id
        if compute_protective_stop_exit_rehearsal_digest(completed_commissioning) != completed_commissioning.rehearsal_digest:
            reasons.append("COMMISSIONING_DIGEST_MISMATCH")
        if completed_commissioning.rehearsal_state is not StopExitRehearsalState.STOP_FILLED_EXIT_RECONCILED:
            reasons.append("COMMISSIONING_EXIT_NOT_RECONCILED")
        if not all((
            completed_commissioning.entry_action_identity,
            completed_commissioning.entry_order_identity,
            completed_commissioning.protective_action_identity,
            completed_commissioning.protective_order_identity,
            completed_commissioning.entry_fill_price,
            completed_commissioning.entry_fill_time,
            completed_commissioning.exit_fill_price,
            completed_commissioning.exit_fill_time,
            completed_commissioning.realized_pnl_source == "BROKER_AUTHORITATIVE_FILLS_LESS_REPORTED_FEES",
        )):
            reasons.append("COMMISSIONING_EVIDENCE_INCOMPLETE")
        if not all((
            completed_commissioning.symbol in policy.symbol_allowlist,
            completed_commissioning.release_sha == release_sha,
            completed_commissioning.implementation_digest == trusted,
            completed_commissioning.account_fingerprint_reference == policy.account_fingerprint_reference,
            completed_commissioning.protection_grounding_source_class == EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION.value,
        )):
            reasons.append("COMMISSIONING_RUNTIME_MISMATCH")

    if protection_grounding is None:
        reasons.append("GROUNDING_NOT_PROVEN")
    else:
        grounding_id = protection_grounding.assessment_id
        if compute_authoritative_grounding_digest(protection_grounding) != protection_grounding.assessment_digest:
            reasons.append("GROUNDING_DIGEST_MISMATCH")
        if not all((
            protection_grounding.outcome is AuthoritativeGroundingOutcome.GROUNDED,
            protection_grounding.protection_grounded,
            protection_grounding.evidence_source_class is EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
            protection_grounding.release_identity == release_sha,
            protection_grounding.implementation_digest == trusted,
            protection_grounding.account_fingerprint_reference == policy.account_fingerprint_reference,
        )):
            reasons.append("GROUNDING_NOT_PROVEN")
        if completed_commissioning is not None and not all((
            protection_grounding.source_candidate_id == completed_commissioning.source_shadow_decision_id,
            protection_grounding.source_handoff_id == completed_commissioning.source_handoff_id,
            protection_grounding.source_protection_plan_id == completed_commissioning.source_protection_rehearsal_id,
            protection_grounding.position_symbol == completed_commissioning.symbol,
        )):
            reasons.append("GROUNDING_SOURCE_MISMATCH")

    reasons = list(dict.fromkeys(reasons))
    state = _reason_state(reasons)
    fields = dict(
        commissioning_status=state, commissioning_evidence_id=commissioning_id,
        protection_grounding_assessment_id=grounding_id,
        account_fingerprint_reference=policy.account_fingerprint_reference,
        strategy_id=policy.strategy_id, strategy_version=policy.strategy_version,
        quantity=policy.quantity, symbol_allowlist=policy.symbol_allowlist,
        regular_session_only=True, entry_start=policy.entry_start, entry_end=policy.entry_end,
        maximum_trade_attempts_per_day=policy.maximum_trade_attempts_per_day,
        maximum_risk_per_share=policy.maximum_risk_per_share,
        maximum_entry_notional=policy.maximum_entry_notional,
        policy_id=policy.policy_id, policy_digest=policy.policy_digest,
        release_sha=release_sha, implementation_digest=trusted,
        activated_at=activated_at, valid_until=policy.deployment_expiry.isoformat(),
        blocking_reasons=tuple(reasons), activation_approved=False,
        permits_automated_action=False, live_ready=False, unattended_ready=False,
        execution_authorized=False, broker_write_count=0, protection_grounded=False,
        authority_ceiling=AUTHORITY_CEILING,
    )
    digest = _compute_payload_digest({
        name: (
            item.value if isinstance(item, Enum) else
            item.isoformat() if isinstance(item, datetime) else
            list(item) if isinstance(item, tuple) else item
        )
        for name, item in fields.items()
    })
    return BoundedPilotActivation(
        activation_id=digest, activation_digest=digest, **fields,
    )


__all__ = [
    "ACTIVATION_POLICY_VERSION", "BoundedPilotActivation",
    "CommissioningReadinessState", "assess_bounded_pilot_activation",
    "compute_bounded_pilot_activation_digest",
]
