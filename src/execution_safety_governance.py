"""Fail-closed safety-governance boundary for the existing attended one-share
commissioning broker write (M14 Issue 1).

This module decides whether an ALREADY-REQUESTED commissioning write (entry
placement, stop placement, exit placement, or stop cancellation) is permitted
to proceed. It never requests a write itself, never talks to a broker or the
network, and never reads live account state directly -- every input arrives
as an already-computed, explicitly injected fact supplied by the caller.

Default is DENY. Missing, unknown, stale, conflicting, or exceptional
evidence all resolve to DENY -- there is deliberately no third permissive
state. A gate that cannot be evaluated is exactly as denying as a gate that
was evaluated and failed.

Only the gates that are honestly authoritative for TODAY's attended,
single-share, fixed-policy commissioning flow are wired here (mode/operator
acknowledgement/source-hash/account-fingerprint/quantity-bound/action-identity
/exchange-calendar). risk_limits, durable_suspension_controller, and
operational_monitor integrations are real and independently tested (see
`src/execution_safety_governance_deferred_gates.py`) but are deliberately not
imported here and not part of the active commissioning policy: each would
require evidence (a realized-daily-loss/kill-switch source, a commissioning
suspension-controller identity, or a heartbeat process) that does not exist
for a synchronous, attended, single-run commissioning flow today. Faking that
evidence would be worse than omitting the gate; see the M14 Issue 1 delivery
report for the documented reasoning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from src.exchange_calendar import classify as _classify_calendar_session
from src.one_share_commissioning import QUANTITY, SYMBOL
from src.protection_observation_model import canonical_bytes


class ExecutionSafetyError(RuntimeError):
    """The governance module's own construction contract was violated.

    Distinct from a DENY decision: this is raised only when a caller passes
    a structurally invalid value to a constructor this module owns (for
    example a naive datetime). Whether the *broker action* itself should be
    denied is always expressed as an `ExecutionSafetyDecision`, never as an
    exception -- `evaluate_execution_safety` never raises for bad input.
    """


class ExecutionSafetyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class CommissioningMode(str, Enum):
    ATTENDED = "ATTENDED"


class ExecutionAction(str, Enum):
    ENTRY_PLACE = "ENTRY_PLACE"
    STOP_PLACE = "STOP_PLACE"
    EXIT_PLACE = "EXIT_PLACE"
    STOP_CANCEL = "STOP_CANCEL"


ROLE_FOR_ACTION: Mapping[ExecutionAction, str] = {
    ExecutionAction.ENTRY_PLACE: "entry",
    ExecutionAction.STOP_PLACE: "stop",
    ExecutionAction.EXIT_PLACE: "exit",
    ExecutionAction.STOP_CANCEL: "cancel",
}
ACTION_FOR_ROLE: Mapping[str, ExecutionAction] = {
    role: action for action, role in ROLE_FOR_ACTION.items()
}

#: Fixed per-role side/type policy. Re-declared here rather than imported
#: from `src/robinhood_commissioning_adapter.py` or
#: `src/one_share_commissioning_orchestrator.py`: this module must be able to
#: independently prove the exact action identity even if either of those
#: modules' own validation were ever weakened, rather than trust a shared
#: table that a single defect could silently corrupt for both sides at once.
_EXPECTED_SIDE_AND_TYPE_BY_ROLE: Mapping[str, tuple[str, str]] = {
    "entry": ("buy", "market"),
    "stop": ("sell", "stop_market"),
    "exit": ("sell", "market"),
}

POLICY_VERSION = "commissioning-v1"
AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
MAX_DECISION_AGE_SECONDS = 30.0

_DIGEST_DOMAIN = b"orb-live/execution-safety-governance/v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in _HEX_DIGITS for character in value)
    )


@dataclass(frozen=True)
class OperatorAcknowledgements:
    """The exact four acknowledgements `LiveRunRequest` already requires.

    Re-expressed here as an explicit, independently checkable bundle rather
    than trusted booleans scattered across the context, so a caller cannot
    supply "close enough" evidence.
    """

    acknowledges_loss_may_exceed_plan: bool
    operator_present: bool
    robinhood_ui_open: bool
    no_second_trade: bool

    def all_satisfied(self) -> bool:
        return bool(
            self.acknowledges_loss_may_exceed_plan is True
            and self.operator_present is True
            and self.robinhood_ui_open is True
            and self.no_second_trade is True
        )


@dataclass(frozen=True)
class CommissioningGovernanceEvidence:
    """Facts already established upstream of the write boundary.

    This module never (re)computes any of these -- it only requires that
    they are explicitly present and true. `account_fingerprint_matched` /
    `account_fingerprint_reference` default to an unverified state because
    they are legitimately not known until account resolution completes;
    every other field has no safe default and must be supplied explicitly.
    """

    commissioning_mode: CommissioningMode
    operator_attended: bool
    operator_acknowledgements: OperatorAcknowledgements
    source_hash_verified: bool
    release_sha: str
    implementation_digest: str
    account_fingerprint_matched: bool = False
    account_fingerprint_reference: str = ""


@dataclass(frozen=True)
class ExecutionSafetyContext:
    """Immutable input to exactly one governance decision for exactly one
    proposed write."""

    context_version: str
    decision_time: datetime
    run_id: str
    action: ExecutionAction
    symbol: str
    side: str
    order_type: str
    quantity: str
    stop_price: str | None
    order_id: str | None
    governance_evidence: CommissioningGovernanceEvidence
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.context_version, str) or not self.context_version:
            raise ExecutionSafetyError("context version is required")
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ExecutionSafetyError("run id is required")
        if not isinstance(self.action, ExecutionAction):
            raise ExecutionSafetyError("action is not a recognized ExecutionAction")
        if not isinstance(self.decision_time, datetime):
            raise ExecutionSafetyError("decision time must be a datetime")
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() is None:
            raise ExecutionSafetyError("decision time must be timezone-aware")
        if not isinstance(self.governance_evidence, CommissioningGovernanceEvidence):
            raise ExecutionSafetyError("governance evidence is required")
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ExecutionSafetyError("policy version is required")

    @property
    def role(self) -> str:
        return ROLE_FOR_ACTION[self.action]

    def action_identity(self) -> str:
        payload: dict[str, Any] = {
            "context_version": self.context_version,
            "policy_version": self.policy_version,
            "run_id": self.run_id,
            "action": self.action.value,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "stop_price": self.stop_price,
            "order_id": self.order_id,
            "account_fingerprint_reference": (
                self.governance_evidence.account_fingerprint_reference
            ),
        }
        return hashlib.sha256(
            _DIGEST_DOMAIN + b"\x00action\x00" + canonical_bytes(payload)
        ).hexdigest()


class GateOutcome(str, Enum):
    SATISFIED = "SATISFIED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class GateResult:
    name: str
    outcome: GateOutcome
    reason_code: str


GateEvaluator = Callable[[ExecutionSafetyContext], GateResult]


def _satisfied(name: str) -> GateResult:
    return GateResult(name, GateOutcome.SATISFIED, "OK")


def _failed(name: str, reason_code: str) -> GateResult:
    return GateResult(name, GateOutcome.FAILED, reason_code)


def gate_commissioning_mode_attended(context: ExecutionSafetyContext) -> GateResult:
    name = "commissioning_mode_attended"
    evidence = context.governance_evidence
    if evidence.commissioning_mode is not CommissioningMode.ATTENDED:
        return _failed(name, "DENY_MODE_NOT_ATTENDED")
    if evidence.operator_attended is not True:
        return _failed(name, "DENY_OPERATOR_NOT_ATTENDED")
    return _satisfied(name)


def gate_operator_acknowledgements(context: ExecutionSafetyContext) -> GateResult:
    name = "operator_acknowledgements"
    acknowledgements = context.governance_evidence.operator_acknowledgements
    if not isinstance(acknowledgements, OperatorAcknowledgements):
        return _failed(name, "DENY_OPERATOR_ACKNOWLEDGEMENT_MISSING")
    if not acknowledgements.all_satisfied():
        return _failed(name, "DENY_OPERATOR_ACKNOWLEDGEMENT_MISSING")
    return _satisfied(name)


def gate_source_hash_verified(context: ExecutionSafetyContext) -> GateResult:
    name = "source_hash_verified"
    if context.governance_evidence.source_hash_verified is not True:
        return _failed(name, "DENY_SOURCE_HASH_UNVERIFIED")
    return _satisfied(name)


def gate_account_fingerprint_matched(context: ExecutionSafetyContext) -> GateResult:
    name = "account_fingerprint_matched"
    evidence = context.governance_evidence
    if evidence.account_fingerprint_matched is not True:
        return _failed(name, "DENY_ACCOUNT_FINGERPRINT_MISMATCH")
    if not _is_sha256_hex(evidence.account_fingerprint_reference):
        return _failed(name, "DENY_ACCOUNT_FINGERPRINT_REFERENCE_MALFORMED")
    return _satisfied(name)


def gate_commissioning_bounds(context: ExecutionSafetyContext) -> GateResult:
    name = "commissioning_bounds"
    try:
        quantity = Decimal(context.quantity)
    except (InvalidOperation, TypeError, ValueError):
        return _failed(name, "DENY_QUANTITY_MALFORMED")
    if quantity != QUANTITY:
        return _failed(name, "DENY_QUANTITY_BOUND")
    return _satisfied(name)


def gate_action_identity_valid(context: ExecutionSafetyContext) -> GateResult:
    name = "action_identity_valid"
    if context.symbol != SYMBOL:
        return _failed(name, "DENY_SYMBOL_MISMATCH")
    role = context.role
    if role == "cancel":
        if not isinstance(context.order_id, str) or not context.order_id.strip():
            return _failed(name, "DENY_ORDER_ID_MISSING")
        return _satisfied(name)
    expected = _EXPECTED_SIDE_AND_TYPE_BY_ROLE.get(role)
    if expected is None:
        return _failed(name, "DENY_ACTION_UNRECOGNIZED")
    expected_side, expected_type = expected
    if context.side != expected_side or context.order_type != expected_type:
        return _failed(name, "DENY_ACTION_IDENTITY_INVALID")
    if role == "stop":
        if not isinstance(context.stop_price, str) or not context.stop_price.strip():
            return _failed(name, "DENY_STOP_PRICE_MISSING")
    elif context.stop_price is not None:
        return _failed(name, "DENY_UNEXPECTED_STOP_PRICE")
    return _satisfied(name)


def gate_exchange_calendar(context: ExecutionSafetyContext) -> GateResult:
    name = "exchange_calendar"
    try:
        classification = _classify_calendar_session(context.decision_time)
    except Exception:
        return _failed(name, "DENY_CALENDAR_EVALUATION_FAILED")
    if not classification.is_regular_session:
        return _failed(name, f"DENY_CALENDAR_{classification.reason.value}")
    return _satisfied(name)


#: The gates actually bound to the live commissioning write path today.
#: Adding a gate here is an authority-tightening change (more restrictive);
#: removing one is an authority-loosening change and must not be made
#: without an explicit new milestone decision.
DEFAULT_COMMISSIONING_GATES: tuple[GateEvaluator, ...] = (
    gate_commissioning_mode_attended,
    gate_operator_acknowledgements,
    gate_source_hash_verified,
    gate_account_fingerprint_matched,
    gate_commissioning_bounds,
    gate_action_identity_valid,
    gate_exchange_calendar,
)


@dataclass(frozen=True)
class ExecutionSafetyDecision:
    decision_id: str
    policy_version: str
    action_identity: str
    outcome: ExecutionSafetyOutcome
    satisfied_gates: tuple[str, ...]
    failed_gates: tuple[str, ...]
    reason_codes: tuple[str, ...]
    decision_digest: str
    evaluated_at: datetime
    authority_ceiling: str
    bound_role: str
    bound_symbol: str
    bound_side: str
    bound_order_type: str
    bound_quantity: str
    bound_stop_price: str | None
    bound_order_id: str | None

    @property
    def allow(self) -> bool:
        return self.outcome is ExecutionSafetyOutcome.ALLOW


def _decision_digest(
    *,
    policy_version: str,
    action_identity: str,
    outcome: ExecutionSafetyOutcome,
    satisfied: tuple[str, ...],
    failed: tuple[str, ...],
    reasons: tuple[str, ...],
    authority_ceiling: str,
) -> str:
    payload: dict[str, Any] = {
        "policy_version": policy_version,
        "action_identity": action_identity,
        "outcome": outcome.value,
        "satisfied_gates": list(satisfied),
        "failed_gates": list(failed),
        "reason_codes": list(reasons),
        "authority_ceiling": authority_ceiling,
    }
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00decision\x00" + canonical_bytes(payload)
    ).hexdigest()


def _denied_for_invalid_context() -> ExecutionSafetyDecision:
    action_identity = hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00invalid_context\x00"
    ).hexdigest()
    satisfied: tuple[str, ...] = ()
    failed = ("context",)
    reasons = ("DENY_CONTEXT_INVALID",)
    digest = _decision_digest(
        policy_version=POLICY_VERSION,
        action_identity=action_identity,
        outcome=ExecutionSafetyOutcome.DENY,
        satisfied=satisfied,
        failed=failed,
        reasons=reasons,
        authority_ceiling=AUTHORITY_CEILING,
    )
    return ExecutionSafetyDecision(
        decision_id=digest,
        policy_version=POLICY_VERSION,
        action_identity=action_identity,
        outcome=ExecutionSafetyOutcome.DENY,
        satisfied_gates=satisfied,
        failed_gates=failed,
        reason_codes=reasons,
        decision_digest=digest,
        evaluated_at=_EPOCH,
        authority_ceiling=AUTHORITY_CEILING,
        bound_role="",
        bound_symbol="",
        bound_side="",
        bound_order_type="",
        bound_quantity="",
        bound_stop_price=None,
        bound_order_id=None,
    )


def evaluate_execution_safety(
    context: Any,
    *,
    gates: Sequence[GateEvaluator] = DEFAULT_COMMISSIONING_GATES,
) -> ExecutionSafetyDecision:
    """Evaluate every gate and fold the results into one deterministic
    decision. Never raises: a context that is not a real
    `ExecutionSafetyContext`, a gate that raises, and a gate that returns
    something other than a `GateResult` all resolve to DENY rather than
    propagating -- this function's own contract is fail-closed, not merely
    the policy it evaluates."""

    if not isinstance(context, ExecutionSafetyContext):
        return _denied_for_invalid_context()

    satisfied: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []

    for gate in gates:
        gate_name = getattr(gate, "__name__", "unnamed_gate")
        try:
            result = gate(context)
        except Exception:
            failed.append(gate_name)
            reasons.append("DENY_GATE_EVALUATION_EXCEPTION")
            continue
        if not isinstance(result, GateResult):
            failed.append(gate_name)
            reasons.append("DENY_GATE_RESULT_MALFORMED")
            continue
        if result.outcome is GateOutcome.SATISFIED:
            satisfied.append(result.name)
        else:
            failed.append(result.name)
            reasons.append(result.reason_code)

    satisfied_t = tuple(sorted(satisfied))
    failed_t = tuple(sorted(failed))
    reasons_t = tuple(sorted(reasons))
    outcome = (
        ExecutionSafetyOutcome.DENY if failed_t else ExecutionSafetyOutcome.ALLOW
    )
    action_identity = context.action_identity()
    digest = _decision_digest(
        policy_version=context.policy_version,
        action_identity=action_identity,
        outcome=outcome,
        satisfied=satisfied_t,
        failed=failed_t,
        reasons=reasons_t,
        authority_ceiling=AUTHORITY_CEILING,
    )
    return ExecutionSafetyDecision(
        decision_id=digest,
        policy_version=context.policy_version,
        action_identity=action_identity,
        outcome=outcome,
        satisfied_gates=satisfied_t,
        failed_gates=failed_t,
        reason_codes=reasons_t,
        decision_digest=digest,
        evaluated_at=context.decision_time,
        authority_ceiling=AUTHORITY_CEILING,
        bound_role=context.role,
        bound_symbol=context.symbol,
        bound_side=context.side,
        bound_order_type=context.order_type,
        bound_quantity=context.quantity,
        bound_stop_price=context.stop_price,
        bound_order_id=context.order_id,
    )


def reviewed_manifest_digest(manifest: Mapping[str, str]) -> str:
    """Deterministic digest over a `{relative path: sha256 hex}` reviewed-
    source manifest, e.g.
    `checkpoint_7c_one_share_commissioning_launcher.py`'s `REVIEWED_HASHES`.

    Gives `CommissioningGovernanceEvidence.implementation_digest` a concrete,
    reproducible value for callers whose own reviewed-hash manifest is not
    `src/read_only_authorization_manifest.py`'s pinned-closure digest.
    """
    payload: dict[str, Any] = {str(path): str(digest) for path, digest in manifest.items()}
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00reviewed_manifest\x00" + canonical_bytes(payload)
    ).hexdigest()


def decision_binds_write(
    decision: Any, *, role: str, arguments: Mapping[str, Any]
) -> bool:
    """Whether `decision` was computed for exactly this write: the same
    role and the same payload-identifying fields about to be sent to the
    broker. Used at the write boundary as a defense-in-depth check
    independent of `evaluate_execution_safety` itself."""

    if not isinstance(decision, ExecutionSafetyDecision):
        return False
    if decision.bound_role != role:
        return False
    if role == "cancel":
        return decision.bound_order_id == arguments.get("order_id")
    return (
        decision.bound_symbol == arguments.get("symbol")
        and decision.bound_side == arguments.get("side")
        and decision.bound_order_type == arguments.get("type")
        and decision.bound_quantity == arguments.get("quantity")
        and decision.bound_stop_price == arguments.get("stop_price")
    )


def decision_is_fresh(
    decision: Any,
    *,
    now: datetime,
    max_age_seconds: float = MAX_DECISION_AGE_SECONDS,
) -> bool:
    """Whether `decision` was evaluated recently enough, relative to `now`,
    to still be trusted. A decision from the future (clock skew) is treated
    as not fresh, not as an error."""

    if not isinstance(decision, ExecutionSafetyDecision):
        return False
    if not isinstance(now, datetime) or now.tzinfo is None:
        return False
    if decision.evaluated_at.tzinfo is None:
        return False
    age = (now - decision.evaluated_at).total_seconds()
    return 0.0 <= age <= max_age_seconds


__all__ = [
    "ExecutionSafetyError",
    "ExecutionSafetyOutcome",
    "CommissioningMode",
    "ExecutionAction",
    "ROLE_FOR_ACTION",
    "ACTION_FOR_ROLE",
    "POLICY_VERSION",
    "AUTHORITY_CEILING",
    "MAX_DECISION_AGE_SECONDS",
    "OperatorAcknowledgements",
    "CommissioningGovernanceEvidence",
    "ExecutionSafetyContext",
    "GateOutcome",
    "GateResult",
    "GateEvaluator",
    "gate_commissioning_mode_attended",
    "gate_operator_acknowledgements",
    "gate_source_hash_verified",
    "gate_account_fingerprint_matched",
    "gate_commissioning_bounds",
    "gate_action_identity_valid",
    "gate_exchange_calendar",
    "DEFAULT_COMMISSIONING_GATES",
    "ExecutionSafetyDecision",
    "evaluate_execution_safety",
    "reviewed_manifest_digest",
    "decision_binds_write",
    "decision_is_fresh",
]
