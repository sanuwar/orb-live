"""Final immutable attended commissioning handoff; it stops before the adapter."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from src.attended_safety_evaluation_bridge import (
    AttendedSafetyEvaluationResult, AttendedSafetyEvaluationState,
    compute_attended_safety_evaluation_digest,
)
from src.execution_safety_governance import (
    AUTHORITY_CEILING, ExecutionSafetyDecision, validate_execution_safety_decision,
)
from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.supervised_micro_live_dry_run import (
    ONE_SHARE, SupervisedMicroLiveDryRunPlan,
    compute_supervised_micro_live_dry_run_digest,
)

ATTENDED_COMMISSIONING_HANDOFF_POLICY_VERSION = "attended-commissioning-handoff-v1"
_DIGEST_DOMAIN = b"orb-live/attended-commissioning-handoff/v1"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
PROTECTION_SEQUENCE = (
    "ENTRY_REVIEW_REQUIRED", "ENTRY_PLACE_REQUIRES_EXPLICIT_ATTENDED_AUTHORIZATION",
    "ENTRY_FILL_RECONCILIATION_REQUIRED", "PROTECTIVE_STOP_REVIEW_REQUIRED",
    "PROTECTIVE_STOP_PLACE_REQUIRES_FRESH_SAFETY_DECISION",
    "STOP_RECONCILIATION_REQUIRED",
)


class AttendedCommissioningHandoffState(str, Enum):
    HANDOFF_READY = "HANDOFF_READY"
    HANDOFF_BLOCKED = "HANDOFF_BLOCKED"
    SAFETY_DECISION_STALE = "SAFETY_DECISION_STALE"
    SAFETY_DECISION_INVALID = "SAFETY_DECISION_INVALID"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"


@dataclass(frozen=True)
class AttendedCommissioningHandoff:
    handoff_id: str
    handoff_digest: str
    source_live_preflight_id: str
    source_safety_evaluation_id: str
    source_shadow_decision_id: str
    source_review_id: str
    source_dry_run_id: str
    symbol: str
    trading_date: str
    quantity: str
    side: str
    order_type: str
    intended_entry: str
    intended_stop: str
    intended_target: str | None
    strategy_version: str
    implementation_digest: str
    release_sha: str
    market_data_digest: str
    opening_range_identity: str
    account_fingerprint_reference: str
    suspension_decision_identity: str | None
    safety_context_identity: str
    safety_decision_identity: str
    safety_decision_result: str
    safety_decision_freshness: str
    required_operator_acknowledgements: tuple[str, ...]
    acknowledgement_state: str
    intended_commissioning_action_identity: str
    intended_protection_sequence: tuple[str, ...]
    operator_action_required: bool
    blocking_reasons: tuple[str, ...]
    handoff_state: AttendedCommissioningHandoffState
    protection_grounded: bool
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.handoff_id != self.handoff_digest:
            raise ValueError("handoff identity must equal its digest")
        if self.quantity != "1":
            raise ValueError("handoff quantity must remain exactly one share")
        if self.protection_grounded is not False:
            raise ValueError("handoff cannot ground protection")
        if self.execution_authorized is not False or self.broker_write_count != 0:
            raise ValueError("handoff cannot authorize or record a broker write")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _fields(value: AttendedCommissioningHandoff) -> dict[str, Any]:
    return {
        "source_live_preflight_id": value.source_live_preflight_id,
        "source_safety_evaluation_id": value.source_safety_evaluation_id,
        "source_shadow_decision_id": value.source_shadow_decision_id,
        "source_review_id": value.source_review_id, "source_dry_run_id": value.source_dry_run_id,
        "symbol": value.symbol, "trading_date": value.trading_date, "quantity": value.quantity,
        "side": value.side, "order_type": value.order_type, "intended_entry": value.intended_entry,
        "intended_stop": value.intended_stop, "intended_target": value.intended_target or "",
        "strategy_version": value.strategy_version, "implementation_digest": value.implementation_digest,
        "release_sha": value.release_sha, "market_data_digest": value.market_data_digest,
        "opening_range_identity": value.opening_range_identity,
        "account_fingerprint_reference": value.account_fingerprint_reference,
        "suspension_decision_identity": value.suspension_decision_identity or "",
        "safety_context_identity": value.safety_context_identity,
        "safety_decision_identity": value.safety_decision_identity,
        "safety_decision_result": value.safety_decision_result,
        "safety_decision_freshness": value.safety_decision_freshness,
        "required_operator_acknowledgements": value.required_operator_acknowledgements,
        "acknowledgement_state": value.acknowledgement_state,
        "intended_commissioning_action_identity": value.intended_commissioning_action_identity,
        "intended_protection_sequence": value.intended_protection_sequence,
        "operator_action_required": True, "blocking_reasons": value.blocking_reasons,
        "handoff_state": value.handoff_state.value, "protection_grounded": False,
        "execution_authorized": False, "broker_write_count": 0,
        "authority_ceiling": value.authority_ceiling,
    }


def compute_attended_commissioning_handoff_digest(value: AttendedCommissioningHandoff) -> str:
    if not isinstance(value, AttendedCommissioningHandoff):
        raise TypeError("value must be an AttendedCommissioningHandoff")
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00handoff\x00" + canonical_bytes(_fields(value))).hexdigest()


def build_attended_commissioning_handoff(
    evaluation: AttendedSafetyEvaluationResult,
    *, dry_run: SupervisedMicroLiveDryRunPlan, decision: ExecutionSafetyDecision | None,
    evaluated_at: datetime, release_sha: str,
    trusted_implementation_digest: str | None = None, root: Path | None = None,
) -> AttendedCommissioningHandoff:
    """Validate final evidence only; deliberately cannot reach an adapter."""
    if not isinstance(evaluation, AttendedSafetyEvaluationResult) or not isinstance(dry_run, SupervisedMicroLiveDryRunPlan):
        raise TypeError("evaluation and dry_run must be authentic artifacts")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(root or Path(__file__).resolve().parents[1])
    reasons: list[str] = []
    if compute_attended_safety_evaluation_digest(evaluation) != evaluation.evaluation_digest:
        reasons.append("SAFETY_EVALUATION_DIGEST_MISMATCH")
    if compute_supervised_micro_live_dry_run_digest(dry_run) != dry_run.dry_run_digest:
        reasons.append("DRY_RUN_DIGEST_MISMATCH")
    if evaluation.evaluation_state is not AttendedSafetyEvaluationState.SAFETY_EVALUATED_ALLOW:
        reasons.append("SAFETY_EVALUATION_NOT_ALLOW")
    if evaluation.implementation_digest != trusted or dry_run.implementation_digest != trusted:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if any((evaluation.source_dry_run_id != dry_run.dry_run_id,
            evaluation.source_review_id != dry_run.source_review_id,
            evaluation.source_shadow_decision_id != dry_run.source_shadow_decision_id,
            evaluation.symbol != dry_run.symbol, evaluation.trading_date != dry_run.trading_date.isoformat(),
            evaluation.strategy_version != dry_run.strategy_version,
            evaluation.market_data_digest != dry_run.market_data_digest,
            evaluation.opening_range_identity != dry_run.opening_range_identity)):
        reasons.append("SOURCE_MISMATCH")
    if dry_run.quantity != ONE_SHARE or evaluation.quantity != "1":
        reasons.append("QUANTITY_INVALID")
    if evaluation.source_hash_status != "VERIFIED" or evaluation.release_sha != release_sha or not _SHA1.fullmatch(release_sha):
        reasons.append("SOURCE_HASH_MISMATCH")
    if evaluation.account_fingerprint_status != "MATCHED" or not dry_run.account_fingerprint_reference:
        reasons.append("ACCOUNT_FINGERPRINT_REQUIRED")
    if evaluation.suspension_status != "CLEAR":
        reasons.append("SUSPENSION_CLEARANCE_REQUIRED")
    if evaluation.candidate_freshness_status != "FRESH":
        reasons.append("STALE_CANDIDATE")

    action = {
        "symbol": "AAPL", "side": "buy", "type": "market", "quantity": "1", "stop_price": None,
    }
    freshness = "NOT_EVALUATED"
    if decision is None:
        reasons.append("SAFETY_DECISION_REQUIRED")
    else:
        if decision.decision_id != evaluation.safety_decision_id or decision.decision_digest != evaluation.safety_decision_digest or decision.action_identity != evaluation.action_identity:
            reasons.append("SAFETY_DECISION_IDENTITY_MISMATCH")
        validation = validate_execution_safety_decision(decision, role="entry", arguments=action, now=evaluated_at)
        freshness = "FRESH" if validation.valid else ("STALE" if validation.reason_code == "EXECUTION_SAFETY_DECISION_STALE" else "INVALID")
        if not validation.valid:
            reasons.append(validation.reason_code)

    if "EXECUTION_SAFETY_DECISION_STALE" in reasons:
        state = AttendedCommissioningHandoffState.SAFETY_DECISION_STALE
    elif reasons:
        state = AttendedCommissioningHandoffState.HANDOFF_BLOCKED
    else:
        state = AttendedCommissioningHandoffState.HANDOFF_READY
    preliminary = AttendedCommissioningHandoff(
        handoff_id="0" * 64, handoff_digest="0" * 64,
        source_live_preflight_id=evaluation.source_live_preflight_id,
        source_safety_evaluation_id=evaluation.evaluation_id,
        source_shadow_decision_id=evaluation.source_shadow_decision_id,
        source_review_id=evaluation.source_review_id, source_dry_run_id=dry_run.dry_run_id,
        symbol=dry_run.symbol, trading_date=dry_run.trading_date.isoformat(), quantity="1",
        side="buy", order_type="market", intended_entry=format(dry_run.proposed_entry, "f"),
        intended_stop=format(dry_run.proposed_stop, "f"),
        intended_target=None if dry_run.target_price is None else format(dry_run.target_price, "f"),
        strategy_version=dry_run.strategy_version, implementation_digest=dry_run.implementation_digest,
        release_sha=release_sha, market_data_digest=dry_run.market_data_digest,
        opening_range_identity=dry_run.opening_range_identity,
        account_fingerprint_reference=dry_run.account_fingerprint_reference or "",
        suspension_decision_identity=evaluation.suspension_decision_identity, safety_context_identity=evaluation.safety_context_id or "",
        safety_decision_identity="" if decision is None else decision.decision_id,
        safety_decision_result=evaluation.safety_result, safety_decision_freshness=freshness,
        required_operator_acknowledgements=evaluation.required_operator_acknowledgements,
        acknowledgement_state=evaluation.operator_acknowledgements_status,
        intended_commissioning_action_identity=evaluation.action_identity or "",
        intended_protection_sequence=PROTECTION_SEQUENCE, operator_action_required=True,
        blocking_reasons=_dedupe(reasons), handoff_state=state,
        protection_grounded=False, execution_authorized=False, broker_write_count=0,
        authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_attended_commissioning_handoff_digest(preliminary)
    return AttendedCommissioningHandoff(**{**preliminary.__dict__, "handoff_id": digest, "handoff_digest": digest})


__all__ = [
    "ATTENDED_COMMISSIONING_HANDOFF_POLICY_VERSION", "AttendedCommissioningHandoff",
    "AttendedCommissioningHandoffState", "PROTECTION_SEQUENCE",
    "build_attended_commissioning_handoff", "compute_attended_commissioning_handoff_digest",
]
