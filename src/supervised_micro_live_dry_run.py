"""Deterministic, read-only commissioning dry-run evidence.

This module projects an authentic REVIEW_READY shadow review into the fixed
one-share commissioning intent without constructing a live safety decision or
reaching any broker-capable boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.shadow_candidate_commissioning_review import (
    ReviewReadinessState,
    ShadowCandidateCommissioningReview,
    compute_shadow_candidate_commissioning_review_digest,
)

AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
DRY_RUN_POLICY_VERSION = "supervised-micro-live-dry-run-v1"
QUANTITY_POLICY = "ATTENDED_MICRO_LIVE_ONE_SHARE_DRY_RUN"
COMMISSIONING_SYMBOL = "AAPL"
ONE_SHARE = Decimal("1")
_DIGEST_DOMAIN = b"orb-live/supervised-micro-live-dry-run/v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class DryRunState(str, Enum):
    """Structural state, deliberately distinct from execution authorization."""

    DRY_RUN_READY = "DRY_RUN_READY"
    REVIEW_NOT_READY = "REVIEW_NOT_READY"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    INVALID_PROTECTION_PLAN = "INVALID_PROTECTION_PLAN"
    INVALID_QUANTITY = "INVALID_QUANTITY"


@dataclass(frozen=True)
class DryRunOrderIntent:
    """A non-dispatchable description of one future order role."""

    role: str
    side: str
    order_type: str
    time_in_force: str
    market_hours: str
    reference_entry_price: Decimal | None
    stop_price: Decimal | None


@dataclass(frozen=True)
class OperatorAcknowledgementRequirement:
    name: str
    acknowledged: bool


@dataclass(frozen=True)
class SafetyInputPreview:
    """Required future safety-input fields, not a governance input object."""

    action: str
    context_fields: tuple[str, ...]
    governance_evidence_fields: tuple[str, ...]
    values_resolved: bool


@dataclass(frozen=True)
class SupervisedMicroLiveDryRunPlan:
    dry_run_id: str
    dry_run_digest: str
    dry_run_policy_version: str
    source_review_id: str
    source_shadow_decision_id: str
    symbol: str
    trading_date: date
    strategy_version: str
    implementation_digest: str
    trusted_implementation_digest: str
    opening_range_identity: str
    market_data_digest: str
    quantity_policy: str
    quantity: Decimal
    entry_intent: DryRunOrderIntent
    protective_stop_intent: DryRunOrderIntent
    target_price: Decimal | None
    protection_sequence: tuple[str, ...]
    required_operator_acknowledgements: tuple[OperatorAcknowledgementRequirement, ...]
    account_fingerprint_status: str
    account_fingerprint_reference: str | None
    suspension_clearance_status: str
    source_hash_verification_status: str
    required_safety_context: SafetyInputPreview
    missing_prerequisites: tuple[str, ...]
    dry_run_state: DryRunState
    blocking_reasons: tuple[str, ...]
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.dry_run_id != self.dry_run_digest:
            raise ValueError("dry-run identity must equal its digest")
        if self.execution_authorized is not False:
            raise ValueError("dry-run evidence cannot authorize execution")
        if self.broker_write_count != 0:
            raise ValueError("dry-run evidence cannot record broker writes")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")
        if self.quantity != ONE_SHARE:
            raise ValueError("dry-run quantity must remain exactly one share")
        if self.entry_intent != _entry_intent(self.proposed_entry):
            raise ValueError("entry intent drift")
        if self.protective_stop_intent != _stop_intent(self.proposed_stop):
            raise ValueError("protective stop intent drift")
        if any(item.acknowledged for item in self.required_operator_acknowledgements):
            raise ValueError("dry-run cannot satisfy operator acknowledgements")

    @property
    def proposed_entry(self) -> Decimal:
        assert self.entry_intent.reference_entry_price is not None
        return self.entry_intent.reference_entry_price

    @property
    def proposed_stop(self) -> Decimal:
        assert self.protective_stop_intent.stop_price is not None
        return self.protective_stop_intent.stop_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run_id": self.dry_run_id,
            "dry_run_digest": self.dry_run_digest,
            "dry_run_policy_version": self.dry_run_policy_version,
            "source_review_id": self.source_review_id,
            "source_shadow_decision_id": self.source_shadow_decision_id,
            "symbol": self.symbol,
            "trading_date": self.trading_date.isoformat(),
            "strategy_version": self.strategy_version,
            "implementation_digest": self.implementation_digest,
            "trusted_implementation_digest": self.trusted_implementation_digest,
            "opening_range_identity": self.opening_range_identity,
            "market_data_digest": self.market_data_digest,
            "quantity_policy": self.quantity_policy,
            "quantity": _decimal(self.quantity),
            "entry_intent": _intent_dict(self.entry_intent),
            "protective_stop_intent": _intent_dict(self.protective_stop_intent),
            "target_price": _decimal(self.target_price),
            "protection_sequence": list(self.protection_sequence),
            "required_operator_acknowledgements": [asdict(item) for item in self.required_operator_acknowledgements],
            "account_fingerprint_status": self.account_fingerprint_status,
            "account_fingerprint_reference": self.account_fingerprint_reference,
            "suspension_clearance_status": self.suspension_clearance_status,
            "source_hash_verification_status": self.source_hash_verification_status,
            "required_safety_context": {
                "action": self.required_safety_context.action,
                "context_fields": list(self.required_safety_context.context_fields),
                "governance_evidence_fields": list(self.required_safety_context.governance_evidence_fields),
                "values_resolved": self.required_safety_context.values_resolved,
            },
            "missing_prerequisites": list(self.missing_prerequisites),
            "dry_run_state": self.dry_run_state.value,
            "blocking_reasons": list(self.blocking_reasons),
            "execution_authorized": False,
            "broker_write_count": 0,
            "authority_ceiling": self.authority_ceiling,
        }


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _intent_dict(intent: DryRunOrderIntent) -> dict[str, str | None]:
    return {
        "role": intent.role,
        "side": intent.side,
        "order_type": intent.order_type,
        "time_in_force": intent.time_in_force,
        "market_hours": intent.market_hours,
        "reference_entry_price": _decimal(intent.reference_entry_price),
        "stop_price": _decimal(intent.stop_price),
    }


def _entry_intent(entry: Decimal) -> DryRunOrderIntent:
    return DryRunOrderIntent(
        role="entry", side="buy", order_type="market", time_in_force="gfd",
        market_hours="regular_hours", reference_entry_price=entry, stop_price=None,
    )


def _stop_intent(stop: Decimal) -> DryRunOrderIntent:
    return DryRunOrderIntent(
        role="stop", side="sell", order_type="stop_market", time_in_force="gtc",
        market_hours="regular_hours", reference_entry_price=None, stop_price=stop,
    )


def _acknowledgements() -> tuple[OperatorAcknowledgementRequirement, ...]:
    return tuple(
        OperatorAcknowledgementRequirement(name=name, acknowledged=False)
        for name in (
            "acknowledges_loss_may_exceed_plan",
            "operator_present",
            "robinhood_ui_open",
            "no_second_trade",
        )
    )


def _safety_preview() -> SafetyInputPreview:
    return SafetyInputPreview(
        action="ENTRY_PLACE",
        context_fields=(
            "context_version", "decision_time", "run_id", "action", "symbol",
            "side", "order_type", "quantity", "stop_price", "order_id",
            "governance_evidence", "policy_version",
        ),
        governance_evidence_fields=(
            "commissioning_mode", "operator_attended", "operator_acknowledgements",
            "source_hash_verified", "release_sha", "implementation_digest",
            "account_fingerprint_matched", "account_fingerprint_reference",
            "suspension_state",
        ),
        values_resolved=False,
    )


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _review_reasons(
    review: ShadowCandidateCommissioningReview,
    trusted: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if review.review_id != review.review_digest:
        reasons.append("REVIEW_ID_MISMATCH")
    try:
        expected = compute_shadow_candidate_commissioning_review_digest(review)
    except Exception:
        reasons.append("REVIEW_DIGEST_UNRECONSTRUCTABLE")
    else:
        if expected != review.review_digest:
            reasons.append("REVIEW_DIGEST_MISMATCH")
    if review.readiness_state is not ReviewReadinessState.REVIEW_READY:
        reasons.append("REVIEW_NOT_READY")
    if review.execution_approval_granted is not False:
        reasons.append("REVIEW_EXECUTION_APPROVAL_INVALID")
    if review.broker_write_count != 0:
        reasons.append("REVIEW_BROKER_WRITE_COUNT_INVALID")
    if review.authority_ceiling != AUTHORITY_CEILING:
        reasons.append("REVIEW_AUTHORITY_CEILING_MISMATCH")
    if review.implementation_digest != trusted or review.trusted_implementation_digest != trusted:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if not _nonempty(review.source_shadow_decision_id):
        reasons.append("SOURCE_SHADOW_DECISION_ID_MISSING")
    if not _nonempty(review.opening_range_identity):
        reasons.append("OPENING_RANGE_IDENTITY_MISSING")
    if not _nonempty(review.market_data_digest):
        reasons.append("MARKET_DATA_DIGEST_MISSING")
    return tuple(reasons)


def _protection_reasons(review: ShadowCandidateCommissioningReview) -> tuple[str, ...]:
    entry, stop, target = review.proposed_entry, review.proposed_stop, review.proposed_target
    if (
        review.protection_plan_complete is not True
        or not isinstance(entry, Decimal)
        or not isinstance(stop, Decimal)
        or not isinstance(target, Decimal)
        or entry <= 0
        or stop <= 0
        or target <= entry
        or stop >= entry
    ):
        return ("PROTECTION_PLAN_INVALID",)
    return ()


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _intent_digest_values(intent: DryRunOrderIntent) -> tuple[str, ...]:
    return tuple(
        f"{key}={value or ''}" for key, value in _intent_dict(intent).items()
    )


def _acknowledgement_digest_values(
    acknowledgements: tuple[OperatorAcknowledgementRequirement, ...],
) -> tuple[str, ...]:
    return tuple(f"{item.name}={item.acknowledged}" for item in acknowledgements)


def _safety_preview_digest_values(preview: SafetyInputPreview) -> tuple[str, ...]:
    return (
        f"action={preview.action}",
        f"values_resolved={preview.values_resolved}",
        *(f"context={value}" for value in preview.context_fields),
        *(f"evidence={value}" for value in preview.governance_evidence_fields),
    )

def _fields(plan: SupervisedMicroLiveDryRunPlan) -> dict[str, Any]:
    return {
        "dry_run_policy_version": plan.dry_run_policy_version,
        "source_review_id": plan.source_review_id,
        "source_shadow_decision_id": plan.source_shadow_decision_id,
        "symbol": plan.symbol,
        "trading_date": plan.trading_date.isoformat(),
        "strategy_version": plan.strategy_version,
        "implementation_digest": plan.implementation_digest,
        "trusted_implementation_digest": plan.trusted_implementation_digest,
        "opening_range_identity": plan.opening_range_identity,
        "market_data_digest": plan.market_data_digest,
        "quantity_policy": plan.quantity_policy,
        "quantity": _decimal(plan.quantity),
        "entry_intent": _intent_digest_values(plan.entry_intent),
        "protective_stop_intent": _intent_digest_values(plan.protective_stop_intent),
        "target_price": _decimal(plan.target_price),
        "protection_sequence": list(plan.protection_sequence),
        "required_operator_acknowledgements": _acknowledgement_digest_values(plan.required_operator_acknowledgements),
        "account_fingerprint_status": plan.account_fingerprint_status,
        "account_fingerprint_reference": plan.account_fingerprint_reference,
        "suspension_clearance_status": plan.suspension_clearance_status,
        "source_hash_verification_status": plan.source_hash_verification_status,
        "required_safety_context": _safety_preview_digest_values(plan.required_safety_context),

        "missing_prerequisites": list(plan.missing_prerequisites),
        "dry_run_state": plan.dry_run_state.value,
        "blocking_reasons": list(plan.blocking_reasons),
        "execution_authorized": False,
        "broker_write_count": 0,
        "authority_ceiling": plan.authority_ceiling,
    }


def compute_supervised_micro_live_dry_run_digest(
    plan: SupervisedMicroLiveDryRunPlan,
) -> str:
    if not isinstance(plan, SupervisedMicroLiveDryRunPlan):
        raise TypeError("plan must be a SupervisedMicroLiveDryRunPlan")
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00plan\x00" + canonical_bytes(_fields(plan))
    ).hexdigest()


def build_supervised_micro_live_dry_run_plan(
    review: ShadowCandidateCommissioningReview,
    *,
    quantity: Decimal = ONE_SHARE,
    account_fingerprint_reference: str | None = None,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> SupervisedMicroLiveDryRunPlan:
    """Build immutable read-only intent; all future write prerequisites remain false."""
    if not isinstance(review, ShadowCandidateCommissioningReview):
        raise TypeError("review must be a ShadowCandidateCommissioningReview")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(
        root or Path(__file__).resolve().parents[1]
    )
    if not _nonempty(trusted):
        raise ValueError("trusted implementation digest is required")
    if quantity != ONE_SHARE:
        raise ValueError("dry-run quantity must remain exactly one share")
    review_reasons = _review_reasons(review, trusted)
    protection_reasons = _protection_reasons(review)
    quantity_reasons: tuple[str, ...] = ()
    symbol_reasons = () if review.symbol == COMMISSIONING_SYMBOL else ("COMMISSIONING_SYMBOL_POLICY_MISMATCH",)
    all_reasons = _dedupe(review_reasons + protection_reasons + quantity_reasons + symbol_reasons)
    if quantity_reasons:
        state = DryRunState.INVALID_QUANTITY
    elif review_reasons:
        state = DryRunState.REVIEW_NOT_READY
    elif protection_reasons:
        state = DryRunState.INVALID_PROTECTION_PLAN
    elif symbol_reasons:
        state = DryRunState.SOURCE_MISMATCH
    else:
        state = DryRunState.DRY_RUN_READY

    account_reference = account_fingerprint_reference if (
        isinstance(account_fingerprint_reference, str) and _HEX_64.fullmatch(account_fingerprint_reference)
    ) else None
    account_status = "SUPPLIED_UNVERIFIED" if account_reference else "REQUIRED_NOT_SUPPLIED"
    required = _dedupe(
        review.execution_requirements_not_evaluated + (
            "ATTENDED_OPERATOR_AND_ACKNOWLEDGEMENTS",
            "SOURCE_HASH_VERIFICATION",
            "ACCOUNT_FINGERPRINT_MATCH",
            "COMMISSIONING_ACTION_IDENTITY",
            "FRESH_SUSPENSION_CLEARANCE",
            "WRITE_BOUND_SAFETY_DECISION",
        )
    )
    entry = review.proposed_entry if isinstance(review.proposed_entry, Decimal) else Decimal("0")
    stop = review.proposed_stop if isinstance(review.proposed_stop, Decimal) else Decimal("0")
    target = review.proposed_target if isinstance(review.proposed_target, Decimal) else None
    preliminary = SupervisedMicroLiveDryRunPlan(
        dry_run_id="0" * 64, dry_run_digest="0" * 64, dry_run_policy_version=DRY_RUN_POLICY_VERSION,
        source_review_id=review.review_id, source_shadow_decision_id=review.source_shadow_decision_id,
        symbol=review.symbol, trading_date=review.trading_date, strategy_version=review.strategy_version,
        implementation_digest=review.implementation_digest, trusted_implementation_digest=trusted,
        opening_range_identity=review.opening_range_identity, market_data_digest=review.market_data_digest,
        quantity_policy=QUANTITY_POLICY, quantity=quantity, entry_intent=_entry_intent(entry),
        protective_stop_intent=_stop_intent(stop), target_price=target,
        protection_sequence=(
            "REVIEW_CANDIDATE", "PREPARE_ENTRY_INTENT", "SAFETY_CONTEXT_REQUIRED",
            "OPERATOR_ACKNOWLEDGEMENTS_REQUIRED", "PROTECTIVE_STOP_INTENT_REQUIRED",
            "WRITE_BOUND_SAFETY_DECISION_REQUIRED",
        ),
        required_operator_acknowledgements=_acknowledgements(),
        account_fingerprint_status=account_status, account_fingerprint_reference=account_reference,
        suspension_clearance_status="REQUIRED_NOT_EVALUATED",
        source_hash_verification_status="REQUIRED_NOT_VERIFIED",
        required_safety_context=_safety_preview(), missing_prerequisites=required,
        dry_run_state=state, blocking_reasons=all_reasons, execution_authorized=False,
        broker_write_count=0, authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_supervised_micro_live_dry_run_digest(preliminary)
    return SupervisedMicroLiveDryRunPlan(**{**preliminary.__dict__, "dry_run_id": digest, "dry_run_digest": digest})


def write_supervised_micro_live_dry_run_artifact(
    plan: SupervisedMicroLiveDryRunPlan,
    output_path: Path,
) -> Path:
    """Persist sanitized deterministic dry-run evidence; never contacts a provider."""
    if compute_supervised_micro_live_dry_run_digest(plan) != plan.dry_run_digest:
        raise ValueError("dry-run digest mismatch")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return output_path


__all__ = [
    "AUTHORITY_CEILING", "COMMISSIONING_SYMBOL", "DRY_RUN_POLICY_VERSION",
    "DryRunOrderIntent", "DryRunState", "ONE_SHARE",
    "OperatorAcknowledgementRequirement", "QUANTITY_POLICY", "SafetyInputPreview",
    "SupervisedMicroLiveDryRunPlan", "build_supervised_micro_live_dry_run_plan",
    "compute_supervised_micro_live_dry_run_digest",
    "write_supervised_micro_live_dry_run_artifact",
]
