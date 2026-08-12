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
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from src.exchange_calendar import EASTERN, classify as _classify_calendar_session
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
    UNATTENDED_PILOT = "UNATTENDED_PILOT"


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
UNATTENDED_POLICY_VERSION = "bounded-unattended-pilot-v1"
AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"
UNATTENDED_AUTHORITY_CEILING = "BOUNDED_UNATTENDED_PILOT"
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

    `suspension_state` (M14 Issue 7) defaults to `"NOT_EVALUATED"` -- a
    value that never equals `"CLEAR"` -- so a caller that forgets to supply
    it (an old test fixture, a future construction site nobody updated)
    fails closed rather than silently passing. This module never reads
    durable suspension state itself (no I/O in a gate, matching every other
    fact here); the caller is expected to read it fresh, immediately
    adjacent to each write, via `src.commissioning_suspension_identity`'s
    `CommissioningSuspensionStateProvider.assess()` -- never via
    `ensure_registered()`/`register()`, which would fabricate a CLEAR row
    for an identity this system has never actually reviewed.
    """

    commissioning_mode: CommissioningMode
    operator_attended: bool
    operator_acknowledgements: OperatorAcknowledgements
    source_hash_verified: bool
    release_sha: str
    implementation_digest: str
    account_fingerprint_matched: bool = False
    account_fingerprint_reference: str = ""
    suspension_state: str = "NOT_EVALUATED"
    unattended_policy: Any = None
    unattended_attempt_id: str = ""
    unattended_broker_snapshot_identity: str = ""
    unattended_snapshot_fresh: bool = False
    unattended_suspension_fresh: bool = False
    unattended_no_conflicting_broker_state: bool = False
    unattended_kill_state_clear: bool = False
    unattended_risk_bounds_verified: bool = False
    unattended_risk_state_allows_new_risk: bool = False
    unattended_health_allows_new_risk: bool = False


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
            "commissioning_mode": self.governance_evidence.commissioning_mode.value,
            "unattended_policy_id": getattr(
                self.governance_evidence.unattended_policy, "policy_id", None
            ),
            "unattended_attempt_id": self.governance_evidence.unattended_attempt_id,
            "unattended_broker_snapshot_identity": (
                self.governance_evidence.unattended_broker_snapshot_identity
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


def gate_unattended_pilot_mode(context: ExecutionSafetyContext) -> GateResult:
    name = "unattended_pilot_mode"
    evidence = context.governance_evidence
    if evidence.commissioning_mode is not CommissioningMode.UNATTENDED_PILOT:
        return _failed(name, "DENY_MODE_NOT_UNATTENDED_PILOT")
    acknowledgements = evidence.operator_acknowledgements
    if evidence.operator_attended is not False or not isinstance(
        acknowledgements, OperatorAcknowledgements
    ):
        return _failed(name, "DENY_UNATTENDED_HUMAN_FACT_MALFORMED")
    if any((acknowledgements.acknowledges_loss_may_exceed_plan,
            acknowledgements.operator_present, acknowledgements.robinhood_ui_open,
            acknowledgements.no_second_trade)):
        return _failed(name, "DENY_UNATTENDED_ACKNOWLEDGEMENT_SYNTHESIZED")
    return _satisfied(name)


def gate_unattended_pilot_policy(context: ExecutionSafetyContext) -> GateResult:
    name = "unattended_pilot_policy"
    policy = context.governance_evidence.unattended_policy
    try:
        from src.bounded_unattended_orchestrator import (
            PolicyEvidenceOrigin, compute_policy_digest,
        )
        valid = all((
            policy.policy_id == policy.policy_digest == compute_policy_digest(policy),
            policy.evidence_origin is PolicyEvidenceOrigin.REVIEWED_PRODUCTION,
            policy.enabled is True, policy.activation_approved is True,
            policy.permits_automated_action is True,
            policy.strategy_id == "orb_long_v0", policy.quantity == 1,
            context.decision_time.astimezone(EASTERN).date()
            <= policy.deployment_expiry,
            context.symbol in policy.symbol_allowlist,
            context.governance_evidence.account_fingerprint_reference
            == policy.account_fingerprint_reference,
            context.governance_evidence.release_sha == policy.release_sha,
            context.governance_evidence.implementation_digest
            == policy.implementation_digest,
        ))
    except Exception:
        valid = False
    return _satisfied(name) if valid else _failed(
        name, "DENY_UNATTENDED_POLICY_INVALID"
    )


def gate_unattended_machine_evidence(context: ExecutionSafetyContext) -> GateResult:
    name = "unattended_machine_evidence"
    evidence = context.governance_evidence
    checks = (
        _is_sha256_hex(evidence.unattended_attempt_id),
        _is_sha256_hex(evidence.unattended_broker_snapshot_identity),
        evidence.unattended_snapshot_fresh is True,
        evidence.unattended_suspension_fresh is True,
        evidence.unattended_no_conflicting_broker_state is True,
        evidence.unattended_kill_state_clear is True,
        evidence.unattended_risk_bounds_verified is True,
        evidence.unattended_risk_state_allows_new_risk is True,
        evidence.unattended_health_allows_new_risk is True,
    )
    return _satisfied(name) if all(checks) else _failed(
        name, "DENY_UNATTENDED_MACHINE_EVIDENCE_INCOMPLETE"
    )


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
    if context.governance_evidence.commissioning_mode is CommissioningMode.ATTENDED:
        symbol_allowed = context.symbol == SYMBOL
    else:
        symbol_allowed = context.symbol in getattr(
            context.governance_evidence.unattended_policy, "symbol_allowlist", ()
        )
    if not symbol_allowed:
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
    if (
        context.governance_evidence.commissioning_mode
        is CommissioningMode.UNATTENDED_PILOT
        and context.role == "entry"
    ):
        policy = context.governance_evidence.unattended_policy
        try:
            current = context.decision_time.astimezone(EASTERN).time()
            start = time.fromisoformat(policy.entry_start)
            end = time.fromisoformat(policy.entry_end)
        except Exception:
            return _failed(name, "DENY_ENTRY_WINDOW_POLICY_INVALID")
        if current < start or current > end:
            return _failed(name, "DENY_OUTSIDE_ENTRY_WINDOW")
    return _satisfied(name)


#: Every value `CommissioningGovernanceEvidence.suspension_state` may
#: honestly hold. Anything outside this set (including the "NOT_EVALUATED"
#: default and a caller's own "READ_FAILED" fail-closed sentinel) already
#: fails the gate below because it is not `"CLEAR"` -- this set exists only
#: to make the vocabulary explicit, not to change the gate's logic.
KNOWN_SUSPENSION_STATES: frozenset[str] = frozenset({
    "CLEAR", "SUSPENDED", "CLEARANCE_PENDING_REVIEW", "PERMANENT_HALT",
    "MALFORMED_STATE", "READ_FAILED", "NOT_EVALUATED",
})


def gate_commissioning_suspension_clear(context: ExecutionSafetyContext) -> GateResult:
    """M14 Issue 7: durable commissioning suspension state must be
    explicitly CLEAR. CLEAR is the only value that satisfies this gate --
    every other value (SUSPENDED, CLEARANCE_PENDING_REVIEW, PERMANENT_HALT,
    MALFORMED_STATE, READ_FAILED, NOT_EVALUATED, or any unrecognized
    string) denies. This gate does no I/O itself: `suspension_state` is a
    fact the caller already read fresh, immediately before this evaluation
    -- consistent with every other gate here never reading live state
    directly."""
    name = "commissioning_suspension_clear"
    state = context.governance_evidence.suspension_state
    if state != "CLEAR":
        return _failed(name, f"DENY_SUSPENSION_{state}")
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
    gate_commissioning_suspension_clear,
)


UNATTENDED_PILOT_GATES: tuple[GateEvaluator, ...] = (
    gate_unattended_pilot_mode,
    gate_unattended_pilot_policy,
    gate_source_hash_verified,
    gate_account_fingerprint_matched,
    gate_commissioning_bounds,
    gate_action_identity_valid,
    gate_exchange_calendar,
    gate_commissioning_suspension_clear,
    gate_unattended_machine_evidence,
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


def compute_decision_digest(
    *,
    policy_version: str,
    action_identity: str,
    outcome: ExecutionSafetyOutcome,
    satisfied: tuple[str, ...],
    failed: tuple[str, ...],
    reasons: tuple[str, ...],
    authority_ceiling: str,
    bound_role: str,
    bound_symbol: str,
    bound_side: str,
    bound_order_type: str,
    bound_quantity: str,
    bound_stop_price: str | None,
    bound_order_id: str | None,
) -> str:
    """Canonical content digest for an `ExecutionSafetyDecision` (M14 Issue 6).

    Binds every field that determines whether this decision authorizes one
    specific write: the decision's own outcome/policy identity AND the exact
    bound write payload (`bound_role`/`bound_symbol`/`bound_side`/
    `bound_order_type`/`bound_quantity`/`bound_stop_price`/`bound_order_id`).
    Mutating any bound field after construction (e.g. via `dataclasses.
    replace`) now changes this digest, closing the gap the M14 Issue 4
    verifier found: previously the digest covered only `action_identity` --
    a hash computed once from the *original* context -- so a bound field
    mutated afterward went undetected because nothing re-derived
    `action_identity` from the mutated value.

    Deliberately excludes:
    - `evaluated_at` -- freshness is an orthogonal, time-varying property
      checked separately by `decision_is_fresh`/
      `validate_execution_safety_decision`; folding a continuously-changing
      timestamp into a content-addressed digest would not add authenticity
      and would make two decisions over identical facts, computed moments
      apart, spuriously non-identical.
    - `decision_id` -- equal to this digest by construction, so including it
      would be circular.
    - `context_version` -- already transitively bound inside
      `action_identity`, itself a hash over the full originating context.

    Reuses the module's one canonical hashing convention (`_DIGEST_DOMAIN` +
    `canonical_bytes`) rather than introducing a second one.
    """
    payload: dict[str, Any] = {
        "policy_version": policy_version,
        "action_identity": action_identity,
        "outcome": outcome.value,
        "satisfied_gates": list(satisfied),
        "failed_gates": list(failed),
        "reason_codes": list(reasons),
        "authority_ceiling": authority_ceiling,
        "bound_role": bound_role,
        "bound_symbol": bound_symbol,
        "bound_side": bound_side,
        "bound_order_type": bound_order_type,
        "bound_quantity": bound_quantity,
        "bound_stop_price": bound_stop_price,
        "bound_order_id": bound_order_id,
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
    digest = compute_decision_digest(
        policy_version=POLICY_VERSION,
        action_identity=action_identity,
        outcome=ExecutionSafetyOutcome.DENY,
        satisfied=satisfied,
        failed=failed,
        reasons=reasons,
        authority_ceiling=AUTHORITY_CEILING,
        bound_role="",
        bound_symbol="",
        bound_side="",
        bound_order_type="",
        bound_quantity="",
        bound_stop_price=None,
        bound_order_id=None,
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
    role = context.role
    authority_ceiling = (
        UNATTENDED_AUTHORITY_CEILING
        if context.governance_evidence.commissioning_mode
        is CommissioningMode.UNATTENDED_PILOT
        else AUTHORITY_CEILING
    )
    digest = compute_decision_digest(
        policy_version=context.policy_version,
        action_identity=action_identity,
        outcome=outcome,
        satisfied=satisfied_t,
        failed=failed_t,
        reasons=reasons_t,
        authority_ceiling=authority_ceiling,
        bound_role=role,
        bound_symbol=context.symbol,
        bound_side=context.side,
        bound_order_type=context.order_type,
        bound_quantity=context.quantity,
        bound_stop_price=context.stop_price,
        bound_order_id=context.order_id,
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
        authority_ceiling=authority_ceiling,
        bound_role=role,
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


class DecisionValidationOutcome(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class DecisionValidationResult:
    outcome: DecisionValidationOutcome
    reason_code: str

    @property
    def valid(self) -> bool:
        return self.outcome is DecisionValidationOutcome.VALID


def _invalid(reason_code: str) -> DecisionValidationResult:
    return DecisionValidationResult(DecisionValidationOutcome.INVALID, reason_code)


_VALID_DECISION = DecisionValidationResult(DecisionValidationOutcome.VALID, "OK")


def validate_execution_safety_decision(
    decision: Any,
    *,
    role: str,
    arguments: Mapping[str, Any],
    now: datetime,
    max_age_seconds: float = MAX_DECISION_AGE_SECONDS,
    expected_policy_version: str = POLICY_VERSION,
    expected_authority_ceiling: str = AUTHORITY_CEILING,
) -> DecisionValidationResult:
    """Independent, write-boundary-time authenticity check for an already-
    computed `ExecutionSafetyDecision` (M14 Issue 6).

    `evaluate_execution_safety` is the only place a *real* decision is ever
    produced, and `decision_binds_write`/`decision_is_fresh` already existed
    as independent checks -- but nothing previously required the actual
    write boundary to use all of them together, or to confirm the decision
    object itself was never forged or mutated after construction. This
    function is meant to be called again at the real write site (not only
    trusted from an upstream caller) as defense in depth: even if every
    caller above the write boundary were wrong, compromised, or bypassed, a
    write still requires an object that independently proves itself
    authentic, exactly bound, fresh, and ALLOW here.

    Checked in this order (each is a hard requirement; the first failure
    wins and no check downstream of a failure is a stronger guarantee than
    the ones already stated):
    1. is actually an `ExecutionSafetyDecision`
    2. outcome is ALLOW (cheapest, most common rejection)
    3. policy_version matches this module's live `POLICY_VERSION`
    4. authority_ceiling matches this module's live `AUTHORITY_CEILING`
    5. decision_digest is well-formed and matches an independent
       recomputation from the decision's OWN stored fields (proves the
       object was not hand-constructed or mutated after the fact -- only
       after this passes are the object's `bound_*` fields trusted at all)
    6. decision_id matches decision_digest (cheap internal-consistency
       sanity check; both are set equal to the same value by
       `evaluate_execution_safety`)
    7. decision_binds_write: the now-trusted bound_* fields match the EXACT
       write about to be sent (role/symbol/side/order_type/quantity/
       stop_price/order_id as applicable)
    8. decision_is_fresh: not stale, not from clock-skewed future, relative
       to the caller-supplied `now`

    Never raises: any structurally wrong input (wrong type, missing
    attribute) is caught by the `isinstance` gate at step 1 and every other
    step operates on an already-confirmed `ExecutionSafetyDecision`, whose
    fields are always the declared types.
    """
    if not isinstance(decision, ExecutionSafetyDecision):
        return _invalid("EXECUTION_SAFETY_DECISION_MISSING")
    if decision.outcome is not ExecutionSafetyOutcome.ALLOW:
        return _invalid("EXECUTION_SAFETY_DENIED")
    if decision.policy_version != expected_policy_version:
        return _invalid("EXECUTION_SAFETY_POLICY_VERSION_MISMATCH")
    if decision.authority_ceiling != expected_authority_ceiling:
        return _invalid("EXECUTION_SAFETY_AUTHORITY_CEILING_MISMATCH")
    if not isinstance(decision.decision_digest, str) or not decision.decision_digest:
        return _invalid("EXECUTION_SAFETY_DECISION_DIGEST_MALFORMED")
    recomputed_digest = compute_decision_digest(
        policy_version=decision.policy_version,
        action_identity=decision.action_identity,
        outcome=decision.outcome,
        satisfied=decision.satisfied_gates,
        failed=decision.failed_gates,
        reasons=decision.reason_codes,
        authority_ceiling=decision.authority_ceiling,
        bound_role=decision.bound_role,
        bound_symbol=decision.bound_symbol,
        bound_side=decision.bound_side,
        bound_order_type=decision.bound_order_type,
        bound_quantity=decision.bound_quantity,
        bound_stop_price=decision.bound_stop_price,
        bound_order_id=decision.bound_order_id,
    )
    if recomputed_digest != decision.decision_digest:
        return _invalid("EXECUTION_SAFETY_DECISION_DIGEST_MISMATCH")
    if decision.decision_id != decision.decision_digest:
        return _invalid("EXECUTION_SAFETY_DECISION_ID_MISMATCH")
    if not decision_binds_write(decision, role=role, arguments=arguments):
        return _invalid("EXECUTION_SAFETY_ACTION_MISMATCH")
    if not decision_is_fresh(decision, now=now, max_age_seconds=max_age_seconds):
        return _invalid("EXECUTION_SAFETY_DECISION_STALE")
    return _VALID_DECISION


__all__ = [
    "ExecutionSafetyError",
    "ExecutionSafetyOutcome",
    "CommissioningMode",
    "ExecutionAction",
    "ROLE_FOR_ACTION",
    "ACTION_FOR_ROLE",
    "POLICY_VERSION",
    "UNATTENDED_POLICY_VERSION",
    "AUTHORITY_CEILING",
    "UNATTENDED_AUTHORITY_CEILING",
    "MAX_DECISION_AGE_SECONDS",
    "OperatorAcknowledgements",
    "CommissioningGovernanceEvidence",
    "ExecutionSafetyContext",
    "GateOutcome",
    "GateResult",
    "GateEvaluator",
    "gate_commissioning_mode_attended",
    "gate_operator_acknowledgements",
    "gate_unattended_pilot_mode",
    "gate_unattended_pilot_policy",
    "gate_unattended_machine_evidence",
    "gate_source_hash_verified",
    "gate_account_fingerprint_matched",
    "gate_commissioning_bounds",
    "gate_action_identity_valid",
    "gate_exchange_calendar",
    "KNOWN_SUSPENSION_STATES",
    "gate_commissioning_suspension_clear",
    "DEFAULT_COMMISSIONING_GATES",
    "UNATTENDED_PILOT_GATES",
    "ExecutionSafetyDecision",
    "evaluate_execution_safety",
    "reviewed_manifest_digest",
    "compute_decision_digest",
    "decision_binds_write",
    "decision_is_fresh",
    "DecisionValidationOutcome",
    "DecisionValidationResult",
    "validate_execution_safety_decision",
]
