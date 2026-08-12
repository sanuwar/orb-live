"""Read-only live attended-preflight input integration.

This module accepts already-observed broker identity and suspension facts.  It
never reads a broker itself, evaluates an execution safety decision, or reaches
a broker-capable adapter.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from src.broker_snapshot import (
    DEFAULT_MAXIMUM_SNAPSHOT_AGE,
    BrokerSnapshot,
    SnapshotValidation,
    validate_snapshot,
)
from src.durable_suspension_controller import ControllerState, SuspensionDecision
from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.supervised_micro_live_dry_run import AUTHORITY_CEILING, ONE_SHARE
from src.supervised_micro_live_preflight import (
    PreflightState,
    SupervisedMicroLivePreflight,
    compute_supervised_micro_live_preflight_digest,
)

LIVE_ATTENDED_PREFLIGHT_POLICY_VERSION = "live-attended-preflight-inputs-v1"
DEFAULT_MAXIMUM_SUSPENSION_OBSERVATION_AGE = timedelta(seconds=30)
_DIGEST_DOMAIN = b"orb-live/live-attended-preflight-inputs/v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class LiveAttendedPreflightState(str, Enum):
    LIVE_PREFLIGHT_READY = "LIVE_PREFLIGHT_READY"
    LIVE_PREFLIGHT_BLOCKED = "LIVE_PREFLIGHT_BLOCKED"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    ACCOUNT_IDENTITY_REQUIRED = "ACCOUNT_IDENTITY_REQUIRED"
    ACCOUNT_IDENTITY_MISMATCH = "ACCOUNT_IDENTITY_MISMATCH"
    SUSPENSION_NOT_CLEAR = "SUSPENSION_NOT_CLEAR"
    SUSPENSION_STALE = "SUSPENSION_STALE"
    STALE_CANDIDATE = "STALE_CANDIDATE"


@dataclass(frozen=True)
class SuspensionObservation:
    """A timestamped, immutable result of one read-only `assess()` call."""

    controller_id: str
    observed_at: datetime
    decision: SuspensionDecision

    def __post_init__(self) -> None:
        if not self.controller_id.strip():
            raise ValueError("suspension controller ID is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("suspension observation timestamp must be timezone-aware")
        if not isinstance(self.decision, SuspensionDecision):
            raise TypeError("suspension observation requires SuspensionDecision")


def observe_suspension(provider: Any, *, observed_at: datetime) -> SuspensionObservation:
    """Capture the existing provider's read-only assessment without mutation."""
    return SuspensionObservation(
        controller_id=str(provider.controller_id),
        observed_at=observed_at,
        decision=provider.assess(),
    )


@dataclass(frozen=True)
class LiveAttendedPreflightResult:
    live_preflight_id: str
    live_preflight_digest: str
    source_preflight_id: str
    source_review_id: str
    source_dry_run_id: str
    source_shadow_decision_id: str
    symbol: str
    trading_date: str
    implementation_digest: str
    strategy_version: str
    market_data_digest: str
    opening_range_identity: str
    quantity: str
    account_fingerprint_observation_status: str
    account_fingerprint_reference: str | None
    account_fingerprint_observation_digest: str | None
    suspension_observation_status: str
    suspension_observation_id: str | None
    candidate_freshness_status: str
    safety_input_completeness: str
    operator_acknowledgements_status: str
    unresolved_prerequisites: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    result_state: LiveAttendedPreflightState
    protection_grounded: bool
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.live_preflight_id != self.live_preflight_digest:
            raise ValueError("live-preflight identity must equal its digest")
        if self.quantity != "1":
            raise ValueError("live preflight quantity must remain exactly one share")
        if self.protection_grounded is not False:
            raise ValueError("read-only preflight cannot ground protection")
        if self.execution_authorized is not False or self.broker_write_count != 0:
            raise ValueError("live preflight cannot authorize or record a broker write")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _fields(result: LiveAttendedPreflightResult) -> dict[str, Any]:
    return {
        "source_preflight_id": result.source_preflight_id,
        "source_review_id": result.source_review_id,
        "source_dry_run_id": result.source_dry_run_id,
        "source_shadow_decision_id": result.source_shadow_decision_id,
        "symbol": result.symbol,
        "trading_date": result.trading_date,
        "implementation_digest": result.implementation_digest,
        "strategy_version": result.strategy_version,
        "market_data_digest": result.market_data_digest,
        "opening_range_identity": result.opening_range_identity,
        "quantity": result.quantity,
        "account_fingerprint_observation_status": result.account_fingerprint_observation_status,
        "account_fingerprint_reference": result.account_fingerprint_reference or "",
        "account_fingerprint_observation_digest": result.account_fingerprint_observation_digest or "",
        "suspension_observation_status": result.suspension_observation_status,
        "suspension_observation_id": result.suspension_observation_id or "",
        "candidate_freshness_status": result.candidate_freshness_status,
        "safety_input_completeness": result.safety_input_completeness,
        "operator_acknowledgements_status": result.operator_acknowledgements_status,
        "unresolved_prerequisites": result.unresolved_prerequisites,
        "blocking_reasons": result.blocking_reasons,
        "result_state": result.result_state.value,
        "protection_grounded": False,
        "execution_authorized": False,
        "broker_write_count": 0,
        "authority_ceiling": result.authority_ceiling,
    }


def compute_live_attended_preflight_digest(result: LiveAttendedPreflightResult) -> str:
    if not isinstance(result, LiveAttendedPreflightResult):
        raise TypeError("result must be a LiveAttendedPreflightResult")
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00result\x00" + canonical_bytes(_fields(result))).hexdigest()


def validate_suspension_observation(
    observation: SuspensionObservation | None,
    *, evaluated_at: datetime,
    maximum_age: timedelta,
) -> tuple[str, str | None]:
    if observation is None:
        return "REQUIRED", None
    if maximum_age <= timedelta(0) or observation.observed_at > evaluated_at:
        return "INVALID", None
    age = evaluated_at - observation.observed_at
    observation_id = hashlib.sha256(
        (observation.controller_id + "\x00" + observation.observed_at.isoformat() + "\x00" + observation.decision.state.value).encode("utf-8")
    ).hexdigest()
    if age > maximum_age:
        return "STALE", observation_id
    if observation.decision.state is not ControllerState.CLEAR:
        return "NOT_CLEAR", observation_id
    if observation.decision.permits_automated_action:
        return "INVALID", observation_id
    return "CLEAR", observation_id


def build_live_attended_preflight_result(
    preflight: SupervisedMicroLivePreflight,
    *,
    snapshot: BrokerSnapshot | None,
    expected_account_fingerprint: str,
    suspension_observation: SuspensionObservation | None,
    evaluated_at: datetime,
    maximum_snapshot_age: timedelta = DEFAULT_MAXIMUM_SNAPSHOT_AGE,
    maximum_suspension_observation_age: timedelta = DEFAULT_MAXIMUM_SUSPENSION_OBSERVATION_AGE,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> LiveAttendedPreflightResult:
    """Resolve only authoritative, read-only account and suspension facts."""
    if not isinstance(preflight, SupervisedMicroLivePreflight):
        raise TypeError("preflight must be an authentic SupervisedMicroLivePreflight")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(root or Path(__file__).resolve().parents[1])
    reasons: list[str] = []
    if compute_supervised_micro_live_preflight_digest(preflight) != preflight.preflight_digest:
        reasons.append("PREFLIGHT_DIGEST_MISMATCH")
    if preflight.preflight_state is not PreflightState.PREFLIGHT_READY:
        reasons.append("SOURCE_PREFLIGHT_NOT_READY")
    if preflight.implementation_digest != trusted:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if preflight.quantity != ONE_SHARE:
        reasons.append("QUANTITY_INVALID")
    if preflight.candidate_freshness_status != "FRESH":
        reasons.append("STALE_CANDIDATE")

    validation: SnapshotValidation | None = None
    account_status = "REQUIRED"
    if snapshot is not None:
        try:
            validation = validate_snapshot(snapshot, evaluated_at=evaluated_at, maximum_age=maximum_snapshot_age)
        except (AttributeError, TypeError, ValueError):
            validation = None
        if validation is None or not validation.valid:
            account_status = "INVALID"
            reasons.append("ACCOUNT_IDENTITY_REQUIRED")
        elif not _HEX_64.fullmatch(expected_account_fingerprint):
            account_status = "REFERENCE_INVALID"
            reasons.append("ACCOUNT_IDENTITY_REQUIRED")
        elif snapshot.metadata.account_fingerprint != expected_account_fingerprint:
            account_status = "MISMATCH"
            reasons.append("ACCOUNT_IDENTITY_MISMATCH")
        else:
            account_status = "MATCHED"
    else:
        reasons.append("ACCOUNT_IDENTITY_REQUIRED")

    suspension_status, suspension_id = validate_suspension_observation(
        suspension_observation,
        evaluated_at=evaluated_at,
        maximum_age=maximum_suspension_observation_age,
    )
    if suspension_status == "STALE":
        reasons.append("SUSPENSION_STALE")
    elif suspension_status != "CLEAR":
        reasons.append("SUSPENSION_NOT_CLEAR")

    # Human acknowledgement is intentionally never satisfied by these observations.
    unresolved = _dedupe([
        "OPERATOR_ACK_REQUIRED",
        "ALLOWING_EXECUTION_SAFETY_DECISION_REQUIRED",
        "LIVE_PROTECTION_GROUNDING_REQUIRED",
    ])
    if reasons:
        state = LiveAttendedPreflightState.LIVE_PREFLIGHT_BLOCKED
    else:
        state = LiveAttendedPreflightState.LIVE_PREFLIGHT_READY
    preliminary = LiveAttendedPreflightResult(
        live_preflight_id="0" * 64,
        live_preflight_digest="0" * 64,
        source_preflight_id=preflight.preflight_id,
        source_review_id=preflight.source_review_id,
        source_dry_run_id=preflight.source_dry_run_id,
        source_shadow_decision_id=preflight.source_shadow_decision_id,
        symbol=preflight.symbol,
        trading_date=preflight.trading_date,
        implementation_digest=preflight.implementation_digest,
        strategy_version=preflight.strategy_version,
        market_data_digest=preflight.market_data_digest,
        opening_range_identity=preflight.opening_range_identity,
        quantity="1",
        account_fingerprint_observation_status=account_status,
        account_fingerprint_reference=(snapshot.metadata.account_fingerprint if account_status == "MATCHED" else None),
        account_fingerprint_observation_digest=None if validation is None else validation.snapshot_digest,
        suspension_observation_status=suspension_status,
        suspension_observation_id=suspension_id,
        candidate_freshness_status=preflight.candidate_freshness_status,
        safety_input_completeness="READY" if not reasons else "INCOMPLETE",
        operator_acknowledgements_status="REQUIRED_AT_ATTENDED_GATE",
        unresolved_prerequisites=unresolved,
        blocking_reasons=_dedupe(reasons),
        result_state=state,
        protection_grounded=False,
        execution_authorized=False,
        broker_write_count=0,
        authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_live_attended_preflight_digest(preliminary)
    return LiveAttendedPreflightResult(**{**preliminary.__dict__, "live_preflight_id": digest, "live_preflight_digest": digest})


__all__ = [
    "DEFAULT_MAXIMUM_SUSPENSION_OBSERVATION_AGE",
    "LIVE_ATTENDED_PREFLIGHT_POLICY_VERSION",
    "LiveAttendedPreflightResult",
    "LiveAttendedPreflightState",
    "SuspensionObservation",
    "build_live_attended_preflight_result",
    "compute_live_attended_preflight_digest",
    "observe_suspension",
    "validate_suspension_observation",
]
