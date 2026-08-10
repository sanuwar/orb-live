"""Read-only preflight immediately before the governed commissioning boundary."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.shadow_candidate_commissioning_review import (
    CandidateFreshnessStatus, ShadowCandidateCommissioningReview,
    compute_shadow_candidate_commissioning_review_digest,
)
from src.supervised_micro_live_dry_run import (
    AUTHORITY_CEILING, DryRunState, ONE_SHARE, SafetyInputPreview, SupervisedMicroLiveDryRunPlan,
    compute_supervised_micro_live_dry_run_digest,
)

PREFLIGHT_POLICY_VERSION = "supervised-micro-live-preflight-v1"
_DIGEST_DOMAIN = b"orb-live/supervised-micro-live-preflight/v1"


class PreflightState(str, Enum):
    PREFLIGHT_READY = "PREFLIGHT_READY"
    PREFLIGHT_BLOCKED = "PREFLIGHT_BLOCKED"
    STALE_CANDIDATE = "STALE_CANDIDATE"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    OPERATOR_ACK_REQUIRED = "OPERATOR_ACK_REQUIRED"
    ACCOUNT_VERIFICATION_REQUIRED = "ACCOUNT_VERIFICATION_REQUIRED"
    SUSPENSION_CLEARANCE_REQUIRED = "SUSPENSION_CLEARANCE_REQUIRED"
    SAFETY_CONTEXT_INCOMPLETE = "SAFETY_CONTEXT_INCOMPLETE"
    PROTECTION_INCOMPLETE = "PROTECTION_INCOMPLETE"


@dataclass(frozen=True)
class PreflightInputs:
    """Explicit, offline-supplied facts; none has a permissive default."""

    evaluated_at: datetime
    acknowledgements: tuple[bool, bool, bool, bool]
    source_hash_verified: bool = False
    release_sha: str = ""
    account_fingerprint_matched: bool = False
    account_fingerprint_reference: str = ""
    suspension_state: str = "NOT_EVALUATED"


@dataclass(frozen=True)
class SupervisedMicroLivePreflight:
    preflight_id: str
    preflight_digest: str
    source_review_id: str
    source_dry_run_id: str
    source_shadow_decision_id: str
    symbol: str
    trading_date: str
    quantity: Decimal
    side: str
    intended_entry: Decimal
    intended_stop: Decimal
    intended_target: Decimal | None
    implementation_digest: str
    strategy_version: str
    market_data_digest: str
    opening_range_identity: str
    required_operator_acknowledgements: tuple[str, ...]
    acknowledgement_status: str
    account_fingerprint_status: str
    suspension_clearance_status: str
    source_hash_status: str
    candidate_freshness_status: str
    safety_context: SafetyInputPreview | None
    safety_context_readiness: str
    protection_plan_readiness: str
    blocking_reasons: tuple[str, ...]
    preflight_state: PreflightState
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.preflight_id != self.preflight_digest:
            raise ValueError("preflight identity must equal its digest")
        if self.quantity != ONE_SHARE:
            raise ValueError("preflight quantity must remain exactly one share")
        if self.execution_authorized is not False or self.broker_write_count != 0:
            raise ValueError("preflight cannot authorize or record a broker write")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _fields(result: SupervisedMicroLivePreflight) -> dict[str, Any]:
    return {
        "source_review_id": result.source_review_id, "source_dry_run_id": result.source_dry_run_id,
        "source_shadow_decision_id": result.source_shadow_decision_id, "symbol": result.symbol,
        "trading_date": result.trading_date, "quantity": format(result.quantity, "f"),
        "side": result.side, "intended_entry": format(result.intended_entry, "f"),
        "intended_stop": format(result.intended_stop, "f"),
        "intended_target": "" if result.intended_target is None else format(result.intended_target, "f"),
        "implementation_digest": result.implementation_digest, "strategy_version": result.strategy_version,
        "market_data_digest": result.market_data_digest, "opening_range_identity": result.opening_range_identity,
        "required_operator_acknowledgements": result.required_operator_acknowledgements,
        "acknowledgement_status": result.acknowledgement_status,
        "account_fingerprint_status": result.account_fingerprint_status,
        "suspension_clearance_status": result.suspension_clearance_status,
        "source_hash_status": result.source_hash_status,
        "candidate_freshness_status": result.candidate_freshness_status,
        "safety_context_readiness": result.safety_context_readiness,
        "protection_plan_readiness": result.protection_plan_readiness,
        "blocking_reasons": result.blocking_reasons, "preflight_state": result.preflight_state.value,
        "execution_authorized": False, "broker_write_count": 0, "authority_ceiling": result.authority_ceiling,
    }


def compute_supervised_micro_live_preflight_digest(result: SupervisedMicroLivePreflight) -> str:
    if not isinstance(result, SupervisedMicroLivePreflight):
        raise TypeError("result must be a SupervisedMicroLivePreflight")
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00preflight\x00" + canonical_bytes(_fields(result))).hexdigest()


def build_supervised_micro_live_preflight(
    review: ShadowCandidateCommissioningReview,
    dry_run: SupervisedMicroLiveDryRunPlan,
    *, inputs: PreflightInputs, trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> SupervisedMicroLivePreflight:
    """Assemble context only; it deliberately never evaluates a safety decision."""
    if not isinstance(review, ShadowCandidateCommissioningReview) or not isinstance(dry_run, SupervisedMicroLiveDryRunPlan):
        raise TypeError("review and dry_run must be authentic artifact types")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(root or Path(__file__).resolve().parents[1])
    if dry_run.quantity != ONE_SHARE:
        raise ValueError("preflight quantity must remain exactly one share")
    reasons: list[str] = []
    if compute_shadow_candidate_commissioning_review_digest(review) != review.review_digest:
        reasons.append("REVIEW_DIGEST_MISMATCH")
    if compute_supervised_micro_live_dry_run_digest(dry_run) != dry_run.dry_run_digest:
        reasons.append("DRY_RUN_DIGEST_MISMATCH")
    if review.review_id != dry_run.source_review_id or review.source_shadow_decision_id != dry_run.source_shadow_decision_id:
        reasons.append("SOURCE_MISMATCH")
    if any((review.symbol != dry_run.symbol, review.trading_date != dry_run.trading_date,
            review.implementation_digest != dry_run.implementation_digest,
            review.market_data_digest != dry_run.market_data_digest,
            review.opening_range_identity != dry_run.opening_range_identity)):
        reasons.append("SOURCE_MISMATCH")
    if dry_run.dry_run_state is not DryRunState.DRY_RUN_READY:
        reasons.append("DRY_RUN_NOT_READY")
    if review.candidate_freshness_status is not CandidateFreshnessStatus.FRESH:
        reasons.append("STALE_CANDIDATE")
    if not review.protection_plan_complete or dry_run.proposed_stop <= 0 or dry_run.target_price is None:
        reasons.append("PROTECTION_INCOMPLETE")
    if dry_run.quantity != ONE_SHARE:
        reasons.append("QUANTITY_INVALID")
    if review.implementation_digest != trusted or dry_run.trusted_implementation_digest != trusted:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if inputs.evaluated_at.tzinfo is None or inputs.evaluated_at.utcoffset() is None:
        reasons.append("SAFETY_CONTEXT_INCOMPLETE")
    acknowledgement_status = "SATISFIED" if len(inputs.acknowledgements) == 4 and all(inputs.acknowledgements) else "REQUIRED"
    account_status = "MATCHED" if inputs.account_fingerprint_matched and bool(inputs.account_fingerprint_reference) else "REQUIRED"
    suspension_status = "CLEAR" if inputs.suspension_state == "CLEAR" else "REQUIRED"
    source_status = "VERIFIED" if inputs.source_hash_verified and bool(inputs.release_sha) else "REQUIRED"
    if acknowledgement_status != "SATISFIED": reasons.append("OPERATOR_ACK_REQUIRED")
    if account_status != "MATCHED": reasons.append("ACCOUNT_VERIFICATION_REQUIRED")
    if suspension_status != "CLEAR": reasons.append("SUSPENSION_CLEARANCE_REQUIRED")
    if source_status != "VERIFIED": reasons.append("SAFETY_CONTEXT_INCOMPLETE")
    context = dry_run.required_safety_context if not any(x == "SAFETY_CONTEXT_INCOMPLETE" for x in reasons) else None
    state = PreflightState.PREFLIGHT_READY if not reasons else PreflightState.PREFLIGHT_BLOCKED
    preliminary = SupervisedMicroLivePreflight(
        preflight_id="0" * 64, preflight_digest="0" * 64, source_review_id=review.review_id,
        source_dry_run_id=dry_run.dry_run_id, source_shadow_decision_id=review.source_shadow_decision_id,
        symbol=dry_run.symbol, trading_date=dry_run.trading_date.isoformat(), quantity=ONE_SHARE,
        side=dry_run.entry_intent.side, intended_entry=dry_run.proposed_entry, intended_stop=dry_run.proposed_stop,
        intended_target=dry_run.target_price, implementation_digest=dry_run.implementation_digest,
        strategy_version=dry_run.strategy_version, market_data_digest=dry_run.market_data_digest,
        opening_range_identity=dry_run.opening_range_identity,
        required_operator_acknowledgements=tuple(x.name for x in dry_run.required_operator_acknowledgements),
        acknowledgement_status=acknowledgement_status, account_fingerprint_status=account_status,
        suspension_clearance_status=suspension_status, source_hash_status=source_status,
        candidate_freshness_status=review.candidate_freshness_status.value,
        safety_context=context, safety_context_readiness="READY" if context else "INCOMPLETE",
        protection_plan_readiness="COMPLETE" if "PROTECTION_INCOMPLETE" not in reasons else "INCOMPLETE",
        blocking_reasons=_dedupe(reasons), preflight_state=state, execution_authorized=False,
        broker_write_count=0, authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_supervised_micro_live_preflight_digest(preliminary)
    return SupervisedMicroLivePreflight(**{**preliminary.__dict__, "preflight_id": digest, "preflight_digest": digest})


__all__ = ["PreflightInputs", "PreflightState", "SupervisedMicroLivePreflight", "build_supervised_micro_live_preflight", "compute_supervised_micro_live_preflight_digest"]