"""Offline, immutable commissioning-review evidence for shadow candidates.

This module deliberately stops at review.  It authenticates shadow evidence,
projects its unresolved future-commissioning requirements, and never constructs
or evaluates a write-bound safety decision.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from src.config import load_config
from src.exchange_calendar import EASTERN, classify
from src.exit_engine import ExitEngine, PositionState
from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.shadow_execution_simulator import (
    BROKER_WRITE_COUNT,
    ShadowExecutionResult,
    ShadowExecutionState,
    compute_shadow_execution_result_digest,
)
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)

AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
REVIEW_POLICY_VERSION = "shadow-candidate-commissioning-review-v1"
QUANTITY_POLICY = "ATTENDED_MICRO_LIVE_ONE_SHARE_REVIEW_ONLY"
SUGGESTED_QUANTITY = Decimal("1")
_DIGEST_DOMAIN = b"orb-live/shadow-candidate-commissioning-review/v1"


class ReviewReadinessState(str, Enum):
    REVIEW_READY = "REVIEW_READY"
    REVIEW_BLOCKED = "REVIEW_BLOCKED"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    STALE = "STALE"
    INCOMPLETE_PROTECTION = "INCOMPLETE_PROTECTION"
    SIMULATION_MISMATCH = "SIMULATION_MISMATCH"


class CandidateFreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"


@dataclass(frozen=True)
class ShadowCandidateCommissioningReview:
    review_id: str
    review_digest: str
    review_policy_version: str
    source_shadow_decision_id: str
    source_simulation_id: str | None
    symbol: str
    trading_date: date
    strategy_version: str
    implementation_digest: str
    trusted_implementation_digest: str
    opening_range_identity: str
    market_data_digest: str
    proposed_entry: Decimal | None
    proposed_stop: Decimal | None
    proposed_target: Decimal | None
    candidate_timestamp: datetime
    reviewed_at: datetime
    candidate_age_seconds: int
    candidate_freshness_status: CandidateFreshnessStatus
    protection_plan_complete: bool
    simulation_state: ShadowExecutionState | None
    simulation_realized_pnl: Decimal | None
    simulation_mfe: Decimal | None
    simulation_mae: Decimal | None
    quantity_policy: str
    suggested_quantity: Decimal
    authority_ceiling: str
    execution_approval_granted: bool
    execution_requirements_not_evaluated: tuple[str, ...]
    readiness_state: ReviewReadinessState
    blocking_reasons: tuple[str, ...]
    broker_write_count: int

    def __post_init__(self) -> None:
        if self.review_id != self.review_digest:
            raise ValueError("review identity must equal its digest")
        if self.execution_approval_granted is not False:
            raise ValueError("review evidence cannot grant execution approval")
        if self.broker_write_count != 0:
            raise ValueError("review evidence cannot record broker writes")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")
        if self.symbol != self.symbol.upper().strip():
            raise ValueError("symbol must be normalized")
        if not isinstance(self.trading_date, date) or isinstance(self.trading_date, datetime):
            raise ValueError("trading_date must be a plain date")
        for value in (self.candidate_timestamp, self.reviewed_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("review timestamps must be timezone-aware")


def _decimal(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entry_window(config: dict[str, Any]) -> tuple[time, time]:
    signal = config["signal"]
    return time.fromisoformat(signal["entry_start"]), time.fromisoformat(signal["entry_end"])


def _derived_target(decision: ShadowTradingDecision, reviewed_at: datetime) -> Decimal | None:
    if (
        not isinstance(decision.proposed_entry, Decimal)
        or not isinstance(decision.proposed_stop, Decimal)
        or decision.proposed_entry <= 0
        or decision.proposed_stop <= 0
        or decision.proposed_stop >= decision.proposed_entry
    ):
        return None
    position = PositionState(
        symbol=decision.symbol,
        entry_price=decision.proposed_entry,
        initial_stop_price=decision.proposed_stop,
        current_stop_price=decision.proposed_stop,
        current_price=decision.proposed_entry,
        highest_price_seen=decision.proposed_entry,
        timestamp=reviewed_at,
    )
    return ExitEngine(load_config()).evaluate(position).target_price


def _decision_reasons(
    decision: Any, trusted_implementation_digest: str,
) -> tuple[str, ...]:
    if not isinstance(decision, ShadowTradingDecision):
        return ("DECISION_TYPE_INVALID",)
    reasons: list[str] = []
    if decision.decision_type is not ShadowDecisionType.LONG_CANDIDATE:
        reasons.append("DECISION_NOT_LONG_CANDIDATE")
    if not _nonempty(decision.opening_range_identity):
        reasons.append("MISSING_OPENING_RANGE_IDENTITY")
    if not _nonempty(decision.market_data_digest):
        reasons.append("MISSING_MARKET_DATA_DIGEST")
    if not _nonempty(decision.implementation_digest):
        reasons.append("MISSING_IMPLEMENTATION_DIGEST")
    if decision.implementation_digest != trusted_implementation_digest:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if decision.market_data_quality.value != "VALID" or decision.exchange_session != "REGULAR":
        reasons.append("DECISION_MARKET_DATA_NOT_VALID")
    if (
        not isinstance(decision.proposed_entry, Decimal)
        or not isinstance(decision.proposed_stop, Decimal)
        or decision.proposed_entry <= 0
        or decision.proposed_stop <= 0
        or decision.proposed_stop >= decision.proposed_entry
    ):
        reasons.append("DECISION_ENTRY_STOP_INVALID")
    if decision.proposed_target is not None and (
        not isinstance(decision.proposed_target, Decimal)
        or decision.proposed_target <= decision.proposed_entry
    ):
        reasons.append("DECISION_TARGET_INVALID")
    try:
        expected = compute_shadow_decision_digest(
            shadow_run_id=decision.shadow_run_id,
            symbol=decision.symbol,
            trading_date=decision.trading_date,
            strategy_version=decision.strategy_version,
            implementation_digest=decision.implementation_digest,
            market_data_digest=decision.market_data_digest,
            opening_range_identity=decision.opening_range_identity,
            signal_timestamp=decision.signal_timestamp,
            decision_type=decision.decision_type,
            proposed_entry=decision.proposed_entry,
            proposed_stop=decision.proposed_stop,
            proposed_target=decision.proposed_target,
            reason=decision.reason,
            market_data_quality=decision.market_data_quality,
            exchange_session=decision.exchange_session,
        )
    except Exception:
        reasons.append("DECISION_DIGEST_UNRECONSTRUCTABLE")
    else:
        if expected != decision.decision_id:
            reasons.append("DECISION_DIGEST_MISMATCH")
    return tuple(reasons)


def _freshness(
    decision: ShadowTradingDecision, reviewed_at: datetime,
) -> tuple[CandidateFreshnessStatus, int, tuple[str, ...]]:
    if not isinstance(reviewed_at, datetime) or reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        return CandidateFreshnessStatus.INVALID_TIMESTAMP, 0, ("REVIEW_TIMESTAMP_INVALID",)
    if decision.signal_timestamp.tzinfo is None or decision.signal_timestamp.utcoffset() is None:
        return CandidateFreshnessStatus.INVALID_TIMESTAMP, 0, ("CANDIDATE_TIMESTAMP_INVALID",)
    age_seconds = int((reviewed_at - decision.signal_timestamp).total_seconds())
    reviewed_et = reviewed_at.astimezone(EASTERN)
    candidate_et = decision.signal_timestamp.astimezone(EASTERN)
    config = load_config()
    entry_start, entry_end = _entry_window(config)
    reasons: list[str] = []
    if reviewed_et.date() != decision.trading_date or candidate_et.date() != decision.trading_date:
        reasons.append("CANDIDATE_TRADING_DATE_STALE")
    if age_seconds < 0:
        reasons.append("REVIEW_BEFORE_CANDIDATE")
    if not classify(reviewed_at).is_regular_session:
        reasons.append("REVIEW_OUTSIDE_REGULAR_SESSION")
    if not entry_start <= candidate_et.time() <= entry_end:
        reasons.append("CANDIDATE_OUTSIDE_ORB_ENTRY_WINDOW")
    if not entry_start <= reviewed_et.time() <= entry_end:
        reasons.append("REVIEW_OUTSIDE_ORB_ENTRY_WINDOW")
    return (
        CandidateFreshnessStatus.FRESH if not reasons else CandidateFreshnessStatus.STALE,
        max(age_seconds, 0),
        tuple(reasons),
    )


def _simulation_reasons(
    decision: ShadowTradingDecision,
    simulation: ShadowExecutionResult | None,
) -> tuple[str, ...]:
    if simulation is None:
        return ()
    reasons: list[str] = []
    if simulation.simulation_id != simulation.simulation_digest:
        reasons.append("SIMULATION_ID_MISMATCH")
    try:
        expected = compute_shadow_execution_result_digest(simulation)
    except Exception:
        reasons.append("SIMULATION_DIGEST_UNRECONSTRUCTABLE")
    else:
        if expected != simulation.simulation_digest:
            reasons.append("SIMULATION_DIGEST_MISMATCH")
    if simulation.source_shadow_decision_id != decision.decision_id:
        reasons.append("SIMULATION_SOURCE_DECISION_MISMATCH")
    if (
        simulation.symbol != decision.symbol
        or simulation.trading_date != decision.trading_date
        or simulation.strategy_version != decision.strategy_version
        or simulation.implementation_digest != decision.implementation_digest
        or simulation.source_market_data_digest != decision.market_data_digest
        or simulation.opening_range_identity != decision.opening_range_identity
    ):
        reasons.append("SIMULATION_CANDIDATE_FIELDS_MISMATCH")
    if simulation.state in {ShadowExecutionState.NOT_ELIGIBLE, ShadowExecutionState.DATA_INVALID}:
        reasons.append("SIMULATION_NOT_VALID_ADVISORY_EVIDENCE")
    if simulation.broker_write_count != 0:
        reasons.append("SIMULATION_BROKER_WRITE_COUNT_INVALID")
    return tuple(reasons)


def _review_digest(fields: dict[str, Any]) -> str:
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00review\x00" + canonical_bytes(fields)).hexdigest()


def compute_shadow_candidate_commissioning_review_digest(
    review: ShadowCandidateCommissioningReview,
) -> str:
    """Reconstruct a review digest without granting commissioning authority."""
    if not isinstance(review, ShadowCandidateCommissioningReview):
        raise TypeError("review must be a ShadowCandidateCommissioningReview")
    fields = {
        "review_policy_version": review.review_policy_version,
        "source_shadow_decision_id": review.source_shadow_decision_id,
        "source_simulation_id": review.source_simulation_id or "",
        "symbol": review.symbol,
        "trading_date": review.trading_date.isoformat(),
        "strategy_version": review.strategy_version,
        "implementation_digest": review.implementation_digest,
        "trusted_implementation_digest": review.trusted_implementation_digest,
        "opening_range_identity": review.opening_range_identity,
        "market_data_digest": review.market_data_digest,
        "proposed_entry": _decimal(review.proposed_entry),
        "proposed_stop": _decimal(review.proposed_stop),
        "proposed_target": _decimal(review.proposed_target),
        "candidate_timestamp": _timestamp(review.candidate_timestamp),
        "reviewed_at": _timestamp(review.reviewed_at),
        "candidate_age_seconds": review.candidate_age_seconds,
        "candidate_freshness_status": review.candidate_freshness_status.value,
        "protection_plan_complete": review.protection_plan_complete,
        "simulation_state": review.simulation_state.value if review.simulation_state else "",
        "simulation_realized_pnl": _decimal(review.simulation_realized_pnl),
        "simulation_mfe": _decimal(review.simulation_mfe),
        "simulation_mae": _decimal(review.simulation_mae),
        "quantity_policy": review.quantity_policy,
        "suggested_quantity": _decimal(review.suggested_quantity),
        "authority_ceiling": review.authority_ceiling,
        "execution_approval_granted": review.execution_approval_granted,
        "execution_requirements_not_evaluated": list(review.execution_requirements_not_evaluated),
        "readiness_state": review.readiness_state.value,
        "blocking_reasons": list(review.blocking_reasons),
        "broker_write_count": review.broker_write_count,
    }
    return _review_digest(fields)

def build_shadow_candidate_commissioning_review(
    decision: ShadowTradingDecision,
    *,
    reviewed_at: datetime,
    simulation: ShadowExecutionResult | None = None,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> ShadowCandidateCommissioningReview:
    """Create non-executable commissioning-review evidence from shadow facts."""
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(
        root or Path(__file__).resolve().parents[1]
    )
    if not _nonempty(trusted):
        raise ValueError("trusted implementation digest is required")
    if not isinstance(decision, ShadowTradingDecision):
        raise TypeError("decision must be a ShadowTradingDecision")
    if not isinstance(reviewed_at, datetime) or reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware")

    decision_reasons = _decision_reasons(decision, trusted)
    freshness_status, candidate_age_seconds, freshness_reasons = _freshness(decision, reviewed_at)
    simulation_reasons = _simulation_reasons(decision, simulation)
    target = decision.proposed_target or _derived_target(decision, reviewed_at)
    protection_complete = bool(
        isinstance(decision.proposed_stop, Decimal)
        and decision.proposed_stop > 0
        and isinstance(target, Decimal)
        and decision.proposed_entry is not None
        and target > decision.proposed_entry
    )
    protection_reasons = () if protection_complete else ("PROTECTION_PLAN_INCOMPLETE",)

    if decision_reasons:
        state = ReviewReadinessState.INVALID_CANDIDATE
    elif simulation_reasons:
        state = ReviewReadinessState.SIMULATION_MISMATCH
    elif freshness_status is not CandidateFreshnessStatus.FRESH:
        state = ReviewReadinessState.STALE
    elif protection_reasons:
        state = ReviewReadinessState.INCOMPLETE_PROTECTION
    else:
        state = ReviewReadinessState.REVIEW_READY

    blockers = decision_reasons + simulation_reasons + freshness_reasons + protection_reasons
    requirements = (
        "ATTENDED_OPERATOR_AND_ACKNOWLEDGEMENTS",
        "SOURCE_HASH_VERIFICATION",
        "ACCOUNT_FINGERPRINT_MATCH",
        "COMMISSIONING_ACTION_IDENTITY",
        "FRESH_SUSPENSION_CLEARANCE",
        "WRITE_BOUND_SAFETY_DECISION",
    )
    fields = {
        "review_policy_version": REVIEW_POLICY_VERSION,
        "source_shadow_decision_id": decision.decision_id,
        "source_simulation_id": simulation.simulation_id if simulation else "",
        "symbol": decision.symbol,
        "trading_date": decision.trading_date.isoformat(),
        "strategy_version": decision.strategy_version,
        "implementation_digest": decision.implementation_digest,
        "trusted_implementation_digest": trusted,
        "opening_range_identity": decision.opening_range_identity,
        "market_data_digest": decision.market_data_digest,
        "proposed_entry": _decimal(decision.proposed_entry),
        "proposed_stop": _decimal(decision.proposed_stop),
        "proposed_target": _decimal(target),
        "candidate_timestamp": _timestamp(decision.signal_timestamp),
        "reviewed_at": _timestamp(reviewed_at),
        "candidate_age_seconds": candidate_age_seconds,
        "candidate_freshness_status": freshness_status.value,
        "protection_plan_complete": protection_complete,
        "simulation_state": simulation.state.value if simulation else "",
        "simulation_realized_pnl": _decimal(simulation.realized_pnl if simulation else None),
        "simulation_mfe": _decimal(simulation.maximum_favorable_excursion if simulation else None),
        "simulation_mae": _decimal(simulation.maximum_adverse_excursion if simulation else None),
        "quantity_policy": QUANTITY_POLICY,
        "suggested_quantity": _decimal(SUGGESTED_QUANTITY),
        "authority_ceiling": AUTHORITY_CEILING,
        "execution_approval_granted": False,
        "execution_requirements_not_evaluated": list(requirements),
        "readiness_state": state.value,
        "blocking_reasons": list(blockers),
        "broker_write_count": 0,
    }
    digest = _review_digest(fields)
    return ShadowCandidateCommissioningReview(
        review_id=digest,
        review_digest=digest,
        review_policy_version=REVIEW_POLICY_VERSION,
        source_shadow_decision_id=decision.decision_id,
        source_simulation_id=simulation.simulation_id if simulation else None,
        symbol=decision.symbol,
        trading_date=decision.trading_date,
        strategy_version=decision.strategy_version,
        implementation_digest=decision.implementation_digest,
        trusted_implementation_digest=trusted,
        opening_range_identity=decision.opening_range_identity,
        market_data_digest=decision.market_data_digest,
        proposed_entry=decision.proposed_entry,
        proposed_stop=decision.proposed_stop,
        proposed_target=target,
        candidate_timestamp=decision.signal_timestamp,
        reviewed_at=reviewed_at,
        candidate_age_seconds=candidate_age_seconds,
        candidate_freshness_status=freshness_status,
        protection_plan_complete=protection_complete,
        simulation_state=simulation.state if simulation else None,
        simulation_realized_pnl=simulation.realized_pnl if simulation else None,
        simulation_mfe=simulation.maximum_favorable_excursion if simulation else None,
        simulation_mae=simulation.maximum_adverse_excursion if simulation else None,
        quantity_policy=QUANTITY_POLICY,
        suggested_quantity=SUGGESTED_QUANTITY,
        authority_ceiling=AUTHORITY_CEILING,
        execution_approval_granted=False,
        execution_requirements_not_evaluated=requirements,
        readiness_state=state,
        blocking_reasons=blockers,
        broker_write_count=0,
    )


__all__ = [
    "CandidateFreshnessStatus",
    "compute_shadow_candidate_commissioning_review_digest",
    "QUANTITY_POLICY",
    "REVIEW_POLICY_VERSION",
    "ReviewReadinessState",
    "ShadowCandidateCommissioningReview",
    "build_shadow_candidate_commissioning_review",
]
