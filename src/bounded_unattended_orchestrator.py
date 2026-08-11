"""Disabled-by-default orchestration for one simulated ORB trade lifecycle.

M21.1 coordinates existing evidence; it does not create another strategy,
safety evaluator, broker adapter, or production write path.  The only mutation
boundary implemented here is ``SimulatedMutationBoundary``.  A reviewed
production policy is deliberately refused until a later milestone explicitly
activates unattended governance at the existing commissioning boundary.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import SimpleNamespace
from typing import Any, Mapping

from src.broker_snapshot import BrokerSnapshot, validate_snapshot
from src.exchange_calendar import EASTERN, CalendarSession, classify
from src.exit_engine import ExitAction, ExitDecision
from src.market_data_canonical import MarketDataQuality
from src.order_lifecycle_model import LifecycleState
from src.protection_grounding_assessment import (
    AuthoritativeGroundingOutcome,
    AuthoritativeProtectionGroundingAssessment,
    compute_authoritative_grounding_digest,
)
from src.protection_observation_model import canonical_bytes
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)

AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
MUTATION_BOUNDARY = "RobinhoodCommissioningAdapter._call"
POLICY_VERSION = "bounded-unattended-pilot-v1"
FIXED_WATCHLIST = (
    "AAPL", "MSFT", "NVDA", "AMD", "AMZN",
    "META", "GOOGL", "TSLA", "AVGO", "NFLX",
)
APPROVED_SAFETY_SOURCE = "src.attended_safety_evaluation_bridge"
_DOMAIN = b"orb-live/bounded-unattended-orchestrator/v1"
_HEX = frozenset("0123456789abcdef")


class UnattendedOrchestratorError(RuntimeError):
    """A malformed policy, evidence item, or transition was refused."""


class PolicyEvidenceOrigin(str, Enum):
    REVIEWED_PRODUCTION = "REVIEWED_PRODUCTION"
    TEST_FIXTURE = "TEST_FIXTURE"


class UnattendedState(str, Enum):
    DISABLED = "DISABLED"
    WAITING_FOR_WINDOW = "WAITING_FOR_WINDOW"
    WATCHING = "WATCHING"
    CANDIDATE_CAPTURED = "CANDIDATE_CAPTURED"
    ENTRY_PREFLIGHT = "ENTRY_PREFLIGHT"
    ENTRY_READY = "ENTRY_READY"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_RECONCILING = "ENTRY_RECONCILING"
    PROTECTION_REQUIRED = "PROTECTION_REQUIRED"
    STOP_READY = "STOP_READY"
    STOP_SUBMITTED = "STOP_SUBMITTED"
    STOP_RECONCILING = "STOP_RECONCILING"
    OPEN_PROTECTED = "OPEN_PROTECTED"
    EXIT_PENDING = "EXIT_PENDING"
    EXIT_TRANSITION = "EXIT_TRANSITION"
    EXIT_READY = "EXIT_READY"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_RECONCILING = "EXIT_RECONCILING"
    TRADE_COMPLETE = "TRADE_COMPLETE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    MARKET_DATA_BLOCKED = "MARKET_DATA_BLOCKED"
    ACCOUNT_BLOCKED = "ACCOUNT_BLOCKED"
    SUSPENSION_BLOCKED = "SUSPENSION_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"
    HEALTH_BLOCKED = "HEALTH_BLOCKED"
    ENTRY_FAILED = "ENTRY_FAILED"
    ENTRY_STATE_UNKNOWN = "ENTRY_STATE_UNKNOWN"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    PROTECTION_UNCONFIRMED = "PROTECTION_UNCONFIRMED"
    UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
    EXIT_BLOCKED = "EXIT_BLOCKED"
    EXIT_STATE_UNKNOWN = "EXIT_STATE_UNKNOWN"
    KILL_STATE = "KILL_STATE"


class SafetyResult(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    INCOMPLETE = "INCOMPLETE"


class SimulatedAction(str, Enum):
    ENTRY_PLACE = "ENTRY_PLACE"
    STOP_PLACE = "STOP_PLACE"
    STOP_CANCEL = "STOP_CANCEL"
    EXIT_PLACE = "EXIT_PLACE"


@dataclass(frozen=True)
class UnattendedPilotPolicy:
    policy_id: str
    policy_digest: str
    enabled: bool
    evidence_origin: PolicyEvidenceOrigin
    account_fingerprint_reference: str
    strategy_id: str
    strategy_version: str
    symbol_allowlist: tuple[str, ...]
    quantity: int
    entry_start: str
    entry_end: str
    maximum_trade_attempts_per_day: int
    maximum_reconciliation_polls: int
    maximum_risk_per_share: str
    maximum_entry_notional: str
    deployment_expiry: date
    release_sha: str
    implementation_digest: str
    activation_approved: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.policy_id != self.policy_digest:
            raise UnattendedOrchestratorError("policy identity mismatch")
        if self.policy_digest != compute_policy_digest(self):
            raise UnattendedOrchestratorError("policy digest mismatch")
        if self.strategy_id != "orb_long_v0" or not self.strategy_version.startswith("orb_long_v0-"):
            raise UnattendedOrchestratorError("only orb_long_v0 is permitted")
        if self.symbol_allowlist != FIXED_WATCHLIST:
            raise UnattendedOrchestratorError("policy watchlist is not the fixed bounded list")
        if self.quantity != 1 or self.maximum_trade_attempts_per_day != 1:
            raise UnattendedOrchestratorError("pilot is exactly one share and one attempt")
        if not 1 <= self.maximum_reconciliation_polls <= 10:
            raise UnattendedOrchestratorError("reconciliation poll bound is invalid")
        if time.fromisoformat(self.entry_start) != time(9, 40) or time.fromisoformat(self.entry_end) != time(11, 0):
            raise UnattendedOrchestratorError("entry window must be 09:40 through 11:00 ET")
        if not _sha(self.account_fingerprint_reference, 64):
            raise UnattendedOrchestratorError("account fingerprint is invalid")
        if not _sha(self.release_sha, 40) or not _sha(self.implementation_digest, 64):
            raise UnattendedOrchestratorError("source identity is invalid")
        for name, value in (("maximum risk", self.maximum_risk_per_share), ("maximum notional", self.maximum_entry_notional)):
            try:
                parsed = Decimal(value)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise UnattendedOrchestratorError(f"{name} is invalid") from exc
            if not parsed.is_finite() or parsed <= 0:
                raise UnattendedOrchestratorError(f"{name} must be positive")
        if self.evidence_origin is PolicyEvidenceOrigin.TEST_FIXTURE and (
            self.activation_approved or self.permits_automated_action
        ):
            raise UnattendedOrchestratorError("fixture policy cannot activate production")


def _sha(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(ch in _HEX for ch in value)


def _policy_payload(value: Any) -> dict[str, Any]:
    return {
        name: (item.value if isinstance(item, Enum) else item.isoformat() if isinstance(item, date) else list(item) if isinstance(item, tuple) else item)
        for name, item in value.__dict__.items()
        if name not in {"policy_id", "policy_digest"}
    }


def compute_policy_digest(value: Any) -> str:
    return hashlib.sha256(_DOMAIN + b"\0policy\0" + canonical_bytes(_policy_payload(value))).hexdigest()


def build_unattended_pilot_policy(**values: Any) -> UnattendedPilotPolicy:
    values = {
        "activation_approved": False,
        "permits_automated_action": False,
        **values,
    }
    placeholder = SimpleNamespace(**values)
    digest = compute_policy_digest(placeholder)
    return UnattendedPilotPolicy(policy_id=digest, policy_digest=digest, **values)


@dataclass(frozen=True)
class DailyAttemptEvidence:
    durable_local_source_available: bool
    broker_same_day_source_available: bool
    durable_attempt_recorded: bool
    broker_same_day_entry_seen: bool
    evidence_id: str

    @property
    def complete(self) -> bool:
        return self.durable_local_source_available and self.broker_same_day_source_available

    @property
    def consumed(self) -> bool:
        return self.durable_attempt_recorded or self.broker_same_day_entry_seen


@dataclass(frozen=True)
class MachineGateEvidence:
    suspension_state: str
    suspension_fresh: bool
    risk_state_available: bool
    risk_state_allows_new_risk: bool
    operational_health_available: bool
    operational_health_allows_new_risk: bool
    source_hash_verified: bool


@dataclass(frozen=True)
class ExistingSafetyEvidence:
    source_component: str
    action: SimulatedAction
    action_identity: str
    safety_decision_identity: str
    result: SafetyResult
    evaluated_at: datetime
    simulation_only: bool
    attended_acknowledgements_synthesized: bool = False

    def __post_init__(self) -> None:
        if self.source_component != APPROVED_SAFETY_SOURCE:
            raise UnattendedOrchestratorError("safety evidence is not from the approved bridge")
        if not _sha(self.action_identity, 64) or not _sha(self.safety_decision_identity, 64):
            raise UnattendedOrchestratorError("safety identities are invalid")
        if self.evaluated_at.tzinfo is None:
            raise UnattendedOrchestratorError("safety timestamp must be timezone-aware")
        if not self.simulation_only or self.attended_acknowledgements_synthesized:
            raise UnattendedOrchestratorError("M21 safety evidence must remain simulation-only without synthetic acknowledgements")


@dataclass(frozen=True)
class SimulatedMutationReceipt:
    action: SimulatedAction
    action_identity: str
    result: str
    broker_order_identity: str

    def __post_init__(self) -> None:
        if self.result != "SIMULATED_WRITE" or not _sha(self.action_identity, 64):
            raise UnattendedOrchestratorError("only a simulated write receipt is accepted")


class SimulatedMutationBoundary:
    """Offline recorder. It has no session, adapter, credential, or network handle."""

    def __init__(self) -> None:
        self.calls: list[tuple[SimulatedAction, Mapping[str, str], str]] = []

    def submit(self, action: SimulatedAction, payload: Mapping[str, str], safety: ExistingSafetyEvidence) -> SimulatedMutationReceipt:
        if safety.result is not SafetyResult.ALLOW or safety.action is not action:
            raise UnattendedOrchestratorError("simulated mutation requires matching ALLOW evidence")
        self.calls.append((action, dict(payload), safety.safety_decision_identity))
        order_id = hashlib.sha256(
            _DOMAIN + b"\0simulated-order\0" + canonical_bytes({
                "action": action.value,
                "identity": safety.action_identity,
                "payload": tuple(
                    f"{key}={payload[key]}" for key in sorted(payload)
                ),
            })
        ).hexdigest()
        return SimulatedMutationReceipt(action, safety.action_identity, "SIMULATED_WRITE", order_id)


@dataclass(frozen=True)
class UnattendedRun:
    run_id: str
    state_digest: str
    state: UnattendedState
    policy_id: str
    trading_date: date
    candidate_id: str | None
    symbol: str | None
    intended_entry: str | None
    intended_stop: str | None
    intended_target: str | None
    account_snapshot_identity: str | None
    daily_attempt_evidence_id: str | None
    daily_allowance_consumed: bool
    entry_order_identity: str | None
    protective_order_identity: str | None
    last_action_identity: str | None
    protection_grounding_assessment_id: str | None
    exit_reason: str | None
    maximum_reconciliation_polls: int
    reconciliation_polls: int
    simulated_write_count: int
    blocking_reasons: tuple[str, ...]
    broker_write_count: int = 0
    execution_authorized: bool = False
    protection_grounded: bool = False
    activation_approved: bool = False
    permits_automated_action: bool = False
    live_ready: bool = False
    unattended_ready: bool = False
    authority_ceiling: str = AUTHORITY_CEILING
    mutation_boundary: str = MUTATION_BOUNDARY

    def __post_init__(self) -> None:
        if self.state_digest != compute_run_digest(self):
            raise UnattendedOrchestratorError("run state digest mismatch")
        if self.broker_write_count != 0 or self.execution_authorized:
            raise UnattendedOrchestratorError("M21.1 cannot write or authorize")
        if any((self.activation_approved, self.permits_automated_action, self.live_ready, self.unattended_ready)):
            raise UnattendedOrchestratorError("M21.1 cannot activate unattended authority")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise UnattendedOrchestratorError("authority ceiling drift")


def _run_payload(value: Any) -> dict[str, Any]:
    return {
        name: (item.value if isinstance(item, Enum) else item.isoformat() if isinstance(item, date) else list(item) if isinstance(item, tuple) else item)
        for name, item in value.__dict__.items()
        if name != "state_digest"
    }


def compute_run_digest(value: Any) -> str:
    return hashlib.sha256(_DOMAIN + b"\0run-state\0" + canonical_bytes(_run_payload(value))).hexdigest()


def _run(**values: Any) -> UnattendedRun:
    values = {
        "broker_write_count": 0,
        "execution_authorized": False,
        "activation_approved": False,
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
        "authority_ceiling": AUTHORITY_CEILING,
        "mutation_boundary": MUTATION_BOUNDARY,
        **values,
    }
    placeholder = SimpleNamespace(**values)
    digest = compute_run_digest(placeholder)
    return UnattendedRun(state_digest=digest, **values)


def _transition(run: UnattendedRun, state: UnattendedState, *, reasons: tuple[str, ...] = (), **updates: Any) -> UnattendedRun:
    values = {**run.__dict__, **updates, "state": state, "blocking_reasons": tuple(dict.fromkeys(reasons))}
    values.pop("state_digest", None)
    return _run(**values)


def _candidate_authentic(decision: ShadowTradingDecision, policy: UnattendedPilotPolicy) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    try:
        digest = compute_shadow_decision_digest(
            shadow_run_id=decision.shadow_run_id, symbol=decision.symbol,
            trading_date=decision.trading_date, strategy_version=decision.strategy_version,
            implementation_digest=decision.implementation_digest,
            market_data_digest=decision.market_data_digest,
            opening_range_identity=decision.opening_range_identity,
            signal_timestamp=decision.signal_timestamp, decision_type=decision.decision_type,
            proposed_entry=decision.proposed_entry, proposed_stop=decision.proposed_stop,
            proposed_target=decision.proposed_target, reason=decision.reason,
            market_data_quality=decision.market_data_quality,
            exchange_session=decision.exchange_session,
        )
    except Exception:
        reasons.append("CANDIDATE_DIGEST_INVALID")
    else:
        if digest != decision.decision_id:
            reasons.append("CANDIDATE_DIGEST_INVALID")
    if decision.decision_type is not ShadowDecisionType.LONG_CANDIDATE:
        reasons.append("NOT_LONG_CANDIDATE")
    if decision.market_data_quality is not MarketDataQuality.VALID:
        reasons.append("MARKET_DATA_INVALID")
    if decision.symbol not in policy.symbol_allowlist:
        reasons.append("SYMBOL_NOT_ALLOWED")
    if decision.strategy_version != policy.strategy_version or decision.implementation_digest != policy.implementation_digest:
        reasons.append("SOURCE_MISMATCH")
    if decision.exchange_session != "REGULAR":
        reasons.append("OUTSIDE_REGULAR_SESSION")
    entry, stop, target = decision.proposed_entry, decision.proposed_stop, decision.proposed_target
    if entry is None or stop is None or stop <= 0 or entry <= stop:
        reasons.append("PROTECTION_PLAN_INVALID")
    if target is not None and (entry is None or target <= entry):
        reasons.append("TARGET_INVALID")
    if entry is not None and entry > Decimal(policy.maximum_entry_notional):
        reasons.append("ENTRY_NOTIONAL_EXCEEDS_POLICY")
    if (
        entry is not None
        and stop is not None
        and entry - stop > Decimal(policy.maximum_risk_per_share)
    ):
        reasons.append("RISK_PER_SHARE_EXCEEDS_POLICY")
    return not reasons, tuple(dict.fromkeys(reasons))


class BoundedUnattendedOrchestrator:
    """Pure state coordinator plus an explicitly simulation-only call recorder."""

    def __init__(self, mutation_boundary: SimulatedMutationBoundary) -> None:
        if not isinstance(mutation_boundary, SimulatedMutationBoundary):
            raise UnattendedOrchestratorError("M21.1 accepts only the simulated mutation boundary")
        self.mutation_boundary = mutation_boundary

    def start(self, *, policy: UnattendedPilotPolicy, candidate: ShadowTradingDecision | None,
              snapshot: BrokerSnapshot, daily_attempt: DailyAttemptEvidence,
              gates: MachineGateEvidence, now: datetime) -> UnattendedRun:
        if now.tzinfo is None:
            raise UnattendedOrchestratorError("clock must be timezone-aware")
        run_id = hashlib.sha256(_DOMAIN + b"\0run\0" + canonical_bytes({"policy": policy.policy_id, "date": now.astimezone(EASTERN).date().isoformat(), "candidate": None if candidate is None else candidate.decision_id})).hexdigest()
        base = dict(
            run_id=run_id, state=UnattendedState.DISABLED, policy_id=policy.policy_id,
            trading_date=now.astimezone(EASTERN).date(), candidate_id=None if candidate is None else candidate.decision_id,
            symbol=None if candidate is None else candidate.symbol,
            intended_entry=None if candidate is None or candidate.proposed_entry is None else format(candidate.proposed_entry, "f"),
            intended_stop=None if candidate is None or candidate.proposed_stop is None else format(candidate.proposed_stop, "f"),
            intended_target=None if candidate is None or candidate.proposed_target is None else format(candidate.proposed_target, "f"),
            account_snapshot_identity=None, daily_attempt_evidence_id=daily_attempt.evidence_id,
            daily_allowance_consumed=daily_attempt.consumed, entry_order_identity=None,
            protective_order_identity=None, last_action_identity=None,
            protection_grounding_assessment_id=None, exit_reason=None,
            maximum_reconciliation_polls=policy.maximum_reconciliation_polls,
            reconciliation_polls=0, simulated_write_count=0, blocking_reasons=(),
            protection_grounded=False,
        )
        run = _run(**base)
        if not policy.enabled:
            return _transition(run, UnattendedState.DISABLED, reasons=("POLICY_DISABLED",))
        if policy.evidence_origin is not PolicyEvidenceOrigin.TEST_FIXTURE:
            return _transition(run, UnattendedState.POLICY_BLOCKED, reasons=("PRODUCTION_ACTIVATION_NOT_IMPLEMENTED",))
        if now.astimezone(EASTERN).date() > policy.deployment_expiry:
            return _transition(run, UnattendedState.POLICY_BLOCKED, reasons=("POLICY_EXPIRED",))
        if not gates.source_hash_verified:
            return _transition(run, UnattendedState.POLICY_BLOCKED, reasons=("SOURCE_MISMATCH",))
        validation = validate_snapshot(snapshot, evaluated_at=now)
        if not validation.valid:
            return _transition(run, UnattendedState.ACCOUNT_BLOCKED, reasons=(validation.result_code,))
        run = _transition(run, run.state, account_snapshot_identity=validation.snapshot_digest)
        if snapshot.metadata.account_fingerprint != policy.account_fingerprint_reference:
            return _transition(run, UnattendedState.ACCOUNT_BLOCKED, reasons=("ACCOUNT_MISMATCH",))
        if snapshot.account_state.positions or snapshot.account_state.open_orders:
            return _transition(run, UnattendedState.ACCOUNT_BLOCKED, reasons=("ACCOUNT_NOT_CLEAN",))
        if not daily_attempt.complete:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("DAILY_ATTEMPT_STATE_UNKNOWN",))
        if daily_attempt.consumed:
            return _transition(run, UnattendedState.POLICY_BLOCKED, reasons=("DAILY_ATTEMPT_CONSUMED",))
        if not gates.suspension_fresh or gates.suspension_state != "CLEAR":
            return _transition(run, UnattendedState.SUSPENSION_BLOCKED, reasons=("SUSPENSION_NOT_CLEAR_OR_STALE",))
        if not gates.risk_state_available or not gates.risk_state_allows_new_risk:
            return _transition(run, UnattendedState.RISK_BLOCKED, reasons=("RISK_STATE_BLOCKED",))
        if not gates.operational_health_available or not gates.operational_health_allows_new_risk:
            return _transition(run, UnattendedState.HEALTH_BLOCKED, reasons=("OPERATIONAL_HEALTH_BLOCKED",))
        session = classify(now, today=now.astimezone(EASTERN).date())
        if session.session is not CalendarSession.REGULAR or not session.is_regular_session:
            return _transition(run, UnattendedState.MARKET_DATA_BLOCKED, reasons=("OUTSIDE_REGULAR_SESSION",))
        current = now.astimezone(EASTERN).time()
        if current < time.fromisoformat(policy.entry_start):
            return _transition(run, UnattendedState.WAITING_FOR_WINDOW)
        if current > time.fromisoformat(policy.entry_end):
            return _transition(run, UnattendedState.MARKET_DATA_BLOCKED, reasons=("OUTSIDE_ENTRY_WINDOW",))
        if candidate is None:
            return _transition(run, UnattendedState.WATCHING)
        authentic, reasons = _candidate_authentic(candidate, policy)
        if not authentic:
            return _transition(run, UnattendedState.MARKET_DATA_BLOCKED, reasons=reasons)
        if candidate.trading_date != run.trading_date or candidate.signal_timestamp.astimezone(EASTERN).date() != run.trading_date:
            return _transition(run, UnattendedState.MARKET_DATA_BLOCKED, reasons=("CANDIDATE_TRADING_DATE_MISMATCH",))
        return _transition(run, UnattendedState.ENTRY_PREFLIGHT)

    @staticmethod
    def mark_safety(
        run: UnattendedRun,
        safety: ExistingSafetyEvidence,
        *,
        now: datetime,
    ) -> UnattendedRun:
        age = (now - safety.evaluated_at).total_seconds()
        if now.tzinfo is None or not 0 <= age <= 30:
            return _transition(
                run, UnattendedState.KILL_STATE,
                reasons=("SAFETY_DECISION_STALE",),
            )
        expected = {
            UnattendedState.ENTRY_PREFLIGHT: (SimulatedAction.ENTRY_PLACE, UnattendedState.ENTRY_READY),
            UnattendedState.PROTECTION_REQUIRED: (SimulatedAction.STOP_PLACE, UnattendedState.STOP_READY),
            UnattendedState.EXIT_PENDING: (SimulatedAction.STOP_CANCEL, UnattendedState.EXIT_TRANSITION),
            UnattendedState.EXIT_READY: (SimulatedAction.EXIT_PLACE, UnattendedState.EXIT_READY),
        }.get(run.state)
        if expected is None or safety.action is not expected[0]:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("SAFETY_ACTION_MISMATCH",))
        if safety.result is SafetyResult.DENY:
            blocked = UnattendedState.ENTRY_FAILED if safety.action is SimulatedAction.ENTRY_PLACE else UnattendedState.EXIT_BLOCKED if safety.action in {SimulatedAction.STOP_CANCEL, SimulatedAction.EXIT_PLACE} else UnattendedState.PROTECTION_FAILED
            return _transition(run, blocked, reasons=("SAFETY_DENY",))
        if safety.result is SafetyResult.INCOMPLETE:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("SAFETY_INCOMPLETE",))
        return _transition(run, expected[1], last_action_identity=safety.action_identity)

    def simulate_write(
        self,
        run: UnattendedRun,
        safety: ExistingSafetyEvidence,
        *,
        now: datetime,
    ) -> UnattendedRun:
        age = (now - safety.evaluated_at).total_seconds()
        if now.tzinfo is None or not 0 <= age <= 30:
            return _transition(
                run, UnattendedState.KILL_STATE,
                reasons=("SAFETY_DECISION_STALE",),
            )
        expected = {
            UnattendedState.ENTRY_READY: (SimulatedAction.ENTRY_PLACE, UnattendedState.ENTRY_SUBMITTED),
            UnattendedState.STOP_READY: (SimulatedAction.STOP_PLACE, UnattendedState.STOP_SUBMITTED),
            UnattendedState.EXIT_TRANSITION: (SimulatedAction.STOP_CANCEL, UnattendedState.EXIT_PENDING),
            UnattendedState.EXIT_READY: (SimulatedAction.EXIT_PLACE, UnattendedState.EXIT_SUBMITTED),
        }.get(run.state)
        if expected is None or safety.action is not expected[0] or safety.result is not SafetyResult.ALLOW:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("SIMULATED_WRITE_NOT_ALLOWED",))
        payload = {"symbol": run.symbol or "", "quantity": "1", "stop_price": run.intended_stop or ""}
        receipt = self.mutation_boundary.submit(safety.action, payload, safety)
        updates: dict[str, Any] = {"simulated_write_count": run.simulated_write_count + 1, "last_action_identity": safety.action_identity}
        if safety.action is SimulatedAction.ENTRY_PLACE:
            if run.daily_allowance_consumed:
                return _transition(run, UnattendedState.POLICY_BLOCKED, reasons=("SECOND_ENTRY_REFUSED",))
            updates.update(daily_allowance_consumed=True, entry_order_identity=receipt.broker_order_identity)
        elif safety.action is SimulatedAction.STOP_PLACE:
            updates["protective_order_identity"] = receipt.broker_order_identity
        return _transition(run, expected[1], **updates)

    @staticmethod
    def reconcile_entry(run: UnattendedRun, lifecycle: LifecycleState) -> UnattendedRun:
        if run.state not in {UnattendedState.ENTRY_SUBMITTED, UnattendedState.ENTRY_RECONCILING}:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("ENTRY_RECONCILIATION_OUT_OF_SEQUENCE",))
        polls = run.reconciliation_polls + 1
        if lifecycle is LifecycleState.FILLED:
            return _transition(run, UnattendedState.PROTECTION_REQUIRED, reconciliation_polls=polls)
        if lifecycle in {LifecycleState.SUBMITTED, LifecycleState.ACCEPTED, LifecycleState.WORKING}:
            if polls >= run.maximum_reconciliation_polls:
                return _transition(
                    run, UnattendedState.ENTRY_STATE_UNKNOWN,
                    reasons=("RECONCILIATION_POLL_LIMIT",),
                    reconciliation_polls=polls,
                )
            return _transition(run, UnattendedState.ENTRY_RECONCILING, reconciliation_polls=polls)
        if lifecycle in {LifecycleState.REJECTED, LifecycleState.CANCELED, LifecycleState.EXPIRED}:
            return _transition(run, UnattendedState.ENTRY_FAILED, reasons=(f"ENTRY_{lifecycle.value}",), reconciliation_polls=polls)
        if lifecycle is LifecycleState.PARTIALLY_FILLED:
            return _transition(run, UnattendedState.PROTECTION_FAILED, reasons=("PARTIAL_FILL_UNSUPPORTED",), reconciliation_polls=polls)
        return _transition(run, UnattendedState.ENTRY_STATE_UNKNOWN, reasons=("ENTRY_STATE_UNKNOWN",), reconciliation_polls=polls)

    @staticmethod
    def reconcile_stop(run: UnattendedRun, assessment: AuthoritativeProtectionGroundingAssessment) -> UnattendedRun:
        if run.state not in {UnattendedState.STOP_SUBMITTED, UnattendedState.STOP_RECONCILING}:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("STOP_RECONCILIATION_OUT_OF_SEQUENCE",))
        polls = run.reconciliation_polls + 1
        try:
            authentic = assessment.assessment_digest == compute_authoritative_grounding_digest(assessment)
        except Exception:
            authentic = False
        if not authentic or assessment.source_candidate_id != run.candidate_id:
            return _transition(run, UnattendedState.PROTECTION_UNCONFIRMED, reasons=("GROUNDING_EVIDENCE_INVALID",), reconciliation_polls=polls, protection_grounded=False)
        if assessment.outcome is AuthoritativeGroundingOutcome.GROUNDED and assessment.protection_grounded:
            return _transition(run, UnattendedState.OPEN_PROTECTED, reconciliation_polls=polls, protection_grounding_assessment_id=assessment.assessment_id, protection_grounded=True)
        return _transition(run, UnattendedState.PROTECTION_UNCONFIRMED, reasons=tuple(reason.value for reason in assessment.reason_codes), reconciliation_polls=polls, protection_grounding_assessment_id=assessment.assessment_id, protection_grounded=False)

    @staticmethod
    def observe_grounding(run: UnattendedRun, assessment: AuthoritativeProtectionGroundingAssessment) -> UnattendedRun:
        if run.state is not UnattendedState.OPEN_PROTECTED:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("GROUNDING_OBSERVATION_OUT_OF_SEQUENCE",))
        if not assessment.protection_grounded:
            return _transition(run, UnattendedState.UNPROTECTED_POSITION, reasons=("PROTECTION_DEMOTED",), protection_grounded=False, protection_grounding_assessment_id=assessment.assessment_id)
        return _transition(run, UnattendedState.OPEN_PROTECTED, protection_grounded=True, protection_grounding_assessment_id=assessment.assessment_id)

    @staticmethod
    def request_exit(run: UnattendedRun, decision: ExitDecision) -> UnattendedRun:
        if run.state is not UnattendedState.OPEN_PROTECTED or not run.protection_grounded:
            return _transition(run, UnattendedState.EXIT_BLOCKED, reasons=("POSITION_NOT_GROUNDED",))
        if decision.action is ExitAction.HOLD:
            return run
        if decision.action is ExitAction.MOVE_STOP_TO_BREAKEVEN:
            return _transition(run, UnattendedState.EXIT_BLOCKED, reasons=("STOP_REPLACEMENT_REQUIRES_M22",))
        if decision.action not in {ExitAction.EXIT_PROFIT_TARGET, ExitAction.EXIT_TIME}:
            return _transition(run, UnattendedState.EXIT_BLOCKED, reasons=("EXIT_ACTION_UNSUPPORTED",))
        return _transition(run, UnattendedState.EXIT_PENDING, exit_reason=decision.action.value, protection_grounded=True)

    @staticmethod
    def confirm_stop_cancelled(run: UnattendedRun, confirmed: bool) -> UnattendedRun:
        if run.state is not UnattendedState.EXIT_PENDING or not confirmed:
            return _transition(run, UnattendedState.EXIT_BLOCKED, reasons=("STOP_CANCEL_NOT_CONFIRMED",), protection_grounded=False)
        return _transition(run, UnattendedState.EXIT_READY, protection_grounded=False)

    @staticmethod
    def reconcile_exit(run: UnattendedRun, *, position_flat: bool, unresolved_orders: bool) -> UnattendedRun:
        if run.state not in {UnattendedState.EXIT_SUBMITTED, UnattendedState.EXIT_RECONCILING}:
            return _transition(run, UnattendedState.KILL_STATE, reasons=("EXIT_RECONCILIATION_OUT_OF_SEQUENCE",))
        polls = run.reconciliation_polls + 1
        if position_flat and not unresolved_orders:
            return _transition(run, UnattendedState.TRADE_COMPLETE, reconciliation_polls=polls, protection_grounded=False)
        if not position_flat and not unresolved_orders:
            return _transition(run, UnattendedState.EXIT_STATE_UNKNOWN, reasons=("OPEN_POSITION_WITHOUT_EXIT_ORDER",), reconciliation_polls=polls, protection_grounded=False)
        return _transition(run, UnattendedState.EXIT_RECONCILING, reconciliation_polls=polls, protection_grounded=False)

    @staticmethod
    def restart(*, policy: UnattendedPilotPolicy, snapshot: BrokerSnapshot,
                daily_attempt: DailyAttemptEvidence, now: datetime) -> UnattendedState:
        validation = validate_snapshot(snapshot, evaluated_at=now)
        if not validation.valid or snapshot.metadata.account_fingerprint != policy.account_fingerprint_reference:
            return UnattendedState.KILL_STATE
        positions = tuple(snapshot.account_state.positions)
        orders = tuple(snapshot.account_state.open_orders)
        if positions:
            return UnattendedState.PROTECTION_REQUIRED
        if orders:
            return UnattendedState.ENTRY_RECONCILING
        if not daily_attempt.complete:
            return UnattendedState.KILL_STATE
        if daily_attempt.consumed:
            return UnattendedState.POLICY_BLOCKED
        return UnattendedState.WATCHING if policy.enabled else UnattendedState.DISABLED

    @staticmethod
    def enforce_poll_bound(run: UnattendedRun, policy: UnattendedPilotPolicy) -> UnattendedRun:
        if run.maximum_reconciliation_polls != policy.maximum_reconciliation_polls:
            return _transition(
                run, UnattendedState.KILL_STATE,
                reasons=("POLICY_IDENTITY_MISMATCH",),
            )
        if run.reconciliation_polls >= run.maximum_reconciliation_polls:
            state = UnattendedState.PROTECTION_UNCONFIRMED if run.state in {UnattendedState.STOP_RECONCILING, UnattendedState.STOP_SUBMITTED} else UnattendedState.EXIT_STATE_UNKNOWN if run.state in {UnattendedState.EXIT_RECONCILING, UnattendedState.EXIT_SUBMITTED} else UnattendedState.ENTRY_STATE_UNKNOWN
            return _transition(run, state, reasons=("RECONCILIATION_POLL_LIMIT",))
        return run


__all__ = [
    "APPROVED_SAFETY_SOURCE", "AUTHORITY_CEILING", "FIXED_WATCHLIST",
    "MUTATION_BOUNDARY", "POLICY_VERSION", "BoundedUnattendedOrchestrator",
    "DailyAttemptEvidence", "ExistingSafetyEvidence", "MachineGateEvidence",
    "PolicyEvidenceOrigin", "SafetyResult", "SimulatedAction",
    "SimulatedMutationBoundary", "SimulatedMutationReceipt", "UnattendedPilotPolicy",
    "UnattendedRun", "UnattendedState", "build_unattended_pilot_policy",
    "compute_policy_digest", "compute_run_digest",
]
