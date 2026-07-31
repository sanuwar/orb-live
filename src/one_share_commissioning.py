"""Offline-safe state model for the attended one-share commissioning run.

This module contains no broker initialization or configuration loading. Broker
I/O is injected by the launcher through ``RobinhoodCommissioningAdapter``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields as field_iter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.live_order_contract import (
    CONTRACT_VIOLATION_KINDS,
    PLACE_ORDER_FIELD_ALIAS_VALUES,
    PLACE_ORDER_OBJECT_SCOPE,
)
from src.live_order_state_model import OrderStateClass, classify_order_state


PLANNED_RISK_USD = Decimal("15")
SYMBOL = "AAPL"
QUANTITY = Decimal("1")
ENTRY_FILL_DEADLINE_SECONDS = 30
STOP_REVIEW_DEADLINE_SECONDS = 10
STOP_OBSERVATION_DEADLINE_SECONDS = 30
MAX_UNPROTECTED_SECONDS = 45
ENTRY_AUTHORIZATION_STAGE = "entry"
FLATTEN_AUTHORIZATION_STAGE = "flatten"
EASTERN = ZoneInfo("America/New_York")
MAX_AUTHORIZATION_WINDOW_SECONDS = 15 * 60
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")


class CommissioningError(RuntimeError):
    """A fail-closed commissioning validation error."""


class AuthorizationInputError(CommissioningError):
    """Interactive authorization failed without retaining supplied input."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class AuthorizationDeclinedError(AuthorizationInputError):
    """The operator did not explicitly approve the requested stage."""


class AuthorizationInterruptedError(AuthorizationInputError):
    """The terminal authorization reader was interrupted or unavailable."""


class EntryWindowError(CommissioningError):
    """The timezone-aware reviewed entry window rejected the operation."""


ALLOWED_FAILURE_CATEGORIES = frozenset({
    "SESSION_INITIALIZATION_FAILED", "SILENT_AUTH_FAILED",
    "ACCOUNT_RESOLUTION_FAILED", "ACCOUNT_IDENTITY_MISMATCH",
    "ADAPTER_INITIALIZATION_FAILED", "ORCHESTRATOR_INITIALIZATION_FAILED",
    "AUTHORIZATION_INPUT_FAILED",
    "ENTRY_AUTHORIZATION_APPROVED", "ENTRY_AUTHORIZATION_DECLINED",
    "ENTRY_AUTHORIZATION_INTERRUPTED",
    "FLATTEN_AUTHORIZATION_APPROVED", "FLATTEN_AUTHORIZATION_DECLINED",
    "FLATTEN_AUTHORIZATION_INTERRUPTED", "ENTRY_WINDOW_REJECTED",
    "ENTRY_REVIEW_FAILED_BEFORE_WRITE", "ENTRY_PLACE_AMBIGUOUS",
    "ENTRY_PLACE_RESPONSE_MALFORMED", "ENTRY_PLACE_TRANSPORT_AMBIGUOUS",
    "ENTRY_RECONCILIATION_MULTIPLE_MATCHES",
    "ENTRY_RECONCILIATION_NOT_FOUND",
    "ENTRY_RECONCILIATION_INCONSISTENT", "ENTRY_RECONCILIATION_TIMEOUT",
    "RECONCILIATION_FAILED", "STOP_PROTECTION_FAILED",
    "STOP_PLACE_ACCEPTED_PENDING", "STOP_PLACE_ACCEPTED_OPEN",
    "STOP_PLACE_ACCEPTED_TRIGGERED", "STOP_PLACE_ACCEPTED_FILLED",
    "STOP_PLACE_RESPONSE_MALFORMED", "STOP_PLACE_TRANSPORT_AMBIGUOUS",
    "STOP_RECONCILED_BY_ORDER", "STOP_RECONCILED_BY_POSITION_AND_ORDER",
    "STOP_ALREADY_FILLED_POSITION_FLAT",
    "STOP_RECONCILIATION_MULTIPLE_MATCHES",
    "STOP_RECONCILIATION_WRONG_QUANTITY",
    "STOP_RECONCILIATION_WRONG_SIDE", "STOP_RECONCILIATION_WRONG_SYMBOL",
    "STOP_RECONCILIATION_CONFLICTING_ORDER",
    "STOP_RECONCILIATION_NOT_FOUND", "STOP_RECONCILIATION_INCONSISTENT",
    "STOP_RECONCILIATION_TIMEOUT", "STOP_PARTIALLY_FILLED",
    "STOP_CANCEL_PENDING", "STOP_CANCEL_CONFIRMED",
    "STOP_CANCEL_REQUEST_ACCEPTED", "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
    "STOP_CANCEL_RESPONSE_MALFORMED", "STOP_CANCEL_RECONCILED_BY_ORDER",
    "STOP_CANCEL_ALREADY_TERMINAL", "STOP_CANCEL_STOP_FILLED_POSITION_FLAT",
    "STOP_CANCEL_PARTIAL_FILL", "STOP_CANCEL_STILL_ACTIVE",
    "STOP_CANCEL_MULTIPLE_MATCHES", "STOP_CANCEL_INCONSISTENT",
    "STOP_CANCEL_NOT_FOUND", "STOP_CANCEL_TIMEOUT",
    # An explicit refusal of the cancel request. Terminal on its own: bounded
    # reconciliation never starts, so it is not a reconciliation failure.
    "STOP_CANCEL_REJECTED",
    # The exit write and its bounded read-only reconciliation. An ambiguous
    # exit submission is not a conclusion: the sell may already be filled, so
    # the outcome must be read back rather than assumed either way.
    "EXIT_PLACE_RESPONSE_MALFORMED", "EXIT_PLACE_TRANSPORT_AMBIGUOUS",
    "EXIT_PLACE_ACCEPTED_PENDING", "EXIT_PLACE_ACCEPTED_FILLED",
    "EXIT_RECONCILED_BY_ORDER", "EXIT_RECONCILED_BY_FLAT_POSITION",
    "EXIT_STILL_WORKING", "EXIT_RECONCILIATION_MULTIPLE_MATCHES",
    "EXIT_RECONCILIATION_NOT_FOUND", "EXIT_RECONCILIATION_INCONSISTENT",
    "EXIT_RECONCILIATION_CONFLICTING_ORDER", "EXIT_RECONCILIATION_TIMEOUT",
    "CANCEL_RECONCILIATION_FAILED", "FLATTEN_FAILED",
    "FINAL_FLAT_VERIFICATION_FAILED", "EVIDENCE_PERSISTENCE_FAILED",
    "SESSION_CLOSE_FAILED", "INTERNAL_INVARIANT_FAILURE",
})

ALLOWED_FAILURE_STAGES = frozenset({
    "SESSION_INITIALIZATION", "SILENT_AUTHENTICATION", "ACCOUNT_RESOLUTION",
    "ADAPTER_INITIALIZATION", "ORCHESTRATOR_INITIALIZATION",
    "EVIDENCE_INITIALIZATION", "PREFLIGHT_RECONCILIATION", "ENTRY_REVIEW",
    "ENTRY_AUTHORIZATION_INPUT", "ENTRY_AUTHORIZATION_CONSUMPTION",
    "FLATTEN_AUTHORIZATION_INPUT", "FLATTEN_AUTHORIZATION_CONSUMPTION",
    "ENTRY_WINDOW_VALIDATION", "ENTRY_PLACE", "ENTRY_RECONCILIATION",
    # The cancel write itself, distinct from the bounded reconciliation that
    # may follow it. A failure here means no reconciliation read ever ran.
    "STOP_PROTECTION", "STOP_CANCEL_SUBMISSION", "CANCEL_RECONCILIATION",
    # The exit write, distinct from the bounded reconciliation that follows it.
    "EXIT_SUBMISSION", "EXIT_RECONCILIATION",
    "FLATTENING",
    "FINAL_FLAT_VERIFICATION", "EVIDENCE_PERSISTENCE", "SESSION_CLOSE",
    "INTERNAL_INVARIANT",
})

# "CancelledError" is deliberately absent: asyncio.CancelledError derives from
# BaseException, nothing here catches BaseException, and structured
# cancellation is propagated rather than classified. An entry for it could
# never be produced and would only imply an observation this code cannot make.
SAFE_EXCEPTION_TYPES = frozenset({
    "AccountIdentityMismatchError", "AccountResolutionError",
    "AuthorizationDeclinedError", "AuthorizationInputError",
    "AuthorizationInterruptedError", "BrokerRejectedError",
    "BrowserAuthenticationRequestError",
    "CommissioningError", "CommissioningSessionBindingError",
    "EntryWindowError", "ResponseContractViolationError",
    "SilentAuthenticationError",
    "TimeoutError", "TypeError", "ValueError", "OSError", "RuntimeError",
})

# Exception classifications that genuinely denote a timeout. Anything else that
# raises is a transport or contract failure, not a deadline expiry.
RECOGNIZED_TIMEOUT_CLASSIFICATIONS = frozenset({"TimeoutError"})

# Classifications that are already a terminal, explicit broker verdict. No
# deadline governs them and no reconciliation follows, so no layer may be named.
TERMINAL_REJECTION_CLASSIFICATIONS = frozenset({"BrokerRejectedError"})


def sanitized_exception_type(error: BaseException) -> str:
    """Return an allowlisted class name without retaining exception text."""

    name = type(error).__name__
    return name if name in SAFE_EXCEPTION_TYPES else "UNKNOWN_EXCEPTION"


class CommissioningDiagnosticError(CommissioningError):
    """Sanitized cross-layer failure with no raw exception message."""

    def __init__(self, category: str, stage: str, exception_type: str) -> None:
        if category not in ALLOWED_FAILURE_CATEGORIES:
            category = "INTERNAL_INVARIANT_FAILURE"
        if stage not in ALLOWED_FAILURE_STAGES:
            stage = "INTERNAL_INVARIANT"
        if exception_type not in SAFE_EXCEPTION_TYPES | {"UNKNOWN_EXCEPTION"}:
            exception_type = "UNKNOWN_EXCEPTION"
        self.category = category
        self.stage = stage
        self.exception_type = exception_type
        super().__init__(category)


class State(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    ENTRY_REVIEWED = "ENTRY_REVIEWED"
    ENTRY_AUTHORIZED = "ENTRY_AUTHORIZED"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_RECONCILING = "ENTRY_RECONCILING"
    ENTRY_FILLED = "ENTRY_FILLED"
    STOP_REVIEWED = "STOP_REVIEWED"
    STOP_SUBMITTED = "STOP_SUBMITTED"
    STOP_RECONCILING = "STOP_RECONCILING"
    PROVISIONAL_PROTECTION_OBSERVED = "PROVISIONAL_PROTECTION_OBSERVED"
    EXIT_AUTHORIZATION_REQUIRED = "EXIT_AUTHORIZATION_REQUIRED"
    STOP_CANCEL_REQUESTED = "STOP_CANCEL_REQUESTED"
    STOP_CANCEL_RECONCILING = "STOP_CANCEL_RECONCILING"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_RECONCILING = "EXIT_RECONCILING"
    VERIFIED_FLAT = "VERIFIED_FLAT"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"
    FAILED_CLOSED = "FAILED_CLOSED"
    COMPLETE = "COMPLETE"


class WriteDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class CommissioningPolicy:
    symbol: str = SYMBOL
    quantity: Decimal = QUANTITY
    planned_risk_usd: Decimal = PLANNED_RISK_USD
    long_only: bool = True
    fractional_allowed: bool = False
    margin_allowed: bool = False
    short_allowed: bool = False
    averaging_down_allowed: bool = False
    second_entry_allowed: bool = False
    second_trade_allowed: bool = False
    regular_market_hours_only: bool = True
    protective_order_type: str = "stop_market"
    protective_duration: str = "gtc"


@dataclass
class RuntimeAuthorization:
    run_id: str
    eastern_date: date
    window_start: datetime
    window_end: datetime
    stage: str
    symbol: str
    quantity: Decimal
    planned_risk_usd: Decimal
    acknowledges_loss_may_exceed_plan: bool
    operator_present: bool
    robinhood_ui_available: bool
    no_second_trade: bool
    used: bool = False

    def consume(self, *, now: datetime, expected_stage: str, run_id: str) -> None:
        if self.used:
            raise CommissioningError("authorization is not reusable")
        self.used = True
        if (
            self.run_id != run_id
            or self.stage != expected_stage
            or self.stage not in {
                ENTRY_AUTHORIZATION_STAGE,
                FLATTEN_AUTHORIZATION_STAGE,
            }
        ):
            raise CommissioningError("authorization scope mismatch")
        times = (now, self.window_start, self.window_end)
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise CommissioningError("authorization timestamps must be timezone-aware")
        if any(value.tzinfo != EASTERN for value in times):
            raise CommissioningError("authorization timestamps must use America/New_York")
        window_seconds = (self.window_end - self.window_start).total_seconds()
        if window_seconds <= 0 or window_seconds > MAX_AUTHORIZATION_WINDOW_SECONDS:
            raise CommissioningError("authorization window is not narrow")
        if now.date() != self.eastern_date or not self.window_start <= now <= self.window_end:
            raise CommissioningError("authorization window is not active")
        if self.symbol != SYMBOL or self.quantity != QUANTITY:
            raise CommissioningError("authorization instrument mismatch")
        if self.planned_risk_usd != PLANNED_RISK_USD:
            raise CommissioningError("authorization planned risk mismatch")
        if not all(
            (
                self.acknowledges_loss_may_exceed_plan,
                self.operator_present,
                self.robinhood_ui_available,
                self.no_second_trade,
            )
        ):
            raise CommissioningError("attended authorization acknowledgments incomplete")
@dataclass(frozen=True)
class OrderObservation:
    commissioned_account: bool
    symbol: str
    side: str
    order_type: str
    requested_quantity: Decimal | None
    cumulative_quantity: Decimal | None
    remaining_quantity: Decimal | None
    stop_price: Decimal | None
    state: str
    related: bool = True


@dataclass(frozen=True)
class EntryFillObservation:
    commissioned_account: bool
    symbol: str
    side: str
    order_type: str
    requested_quantity: Decimal | None
    cumulative_quantity: Decimal | None
    remaining_quantity: Decimal | None
    state: str
    average_fill_price: Decimal | None


@dataclass(frozen=True)
class ProtectionAssessment:
    provisional: bool
    classification: str
    validations: Mapping[str, bool]


PROVISIONAL_NONTERMINAL_STATES = frozenset(
    {"new", "queued", "confirmed", "unconfirmed", "partially_filled"}
)


def decimal_value(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CommissioningError("invalid decimal value") from exc
    if not result.is_finite():
        raise CommissioningError("non-finite decimal value")
    return result


def normalize_stop_price(fill_price: object) -> Decimal:
    """Return fill minus planned risk, normalized to an equity cent."""
    stop = decimal_value(fill_price) - PLANNED_RISK_USD
    stop = stop.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if stop <= 0:
        raise CommissioningError("calculated stop price is not positive")
    return stop


def assess_provisional_protection(
    *,
    order: OrderObservation,
    position_quantity: object,
    expected_stop_price: object,
    competing_sell_orders: int,
) -> ProtectionAssessment:
    expected = normalize_explicit_price(expected_stop_price)
    validations = {
        "commissioned_account": order.commissioned_account,
        "symbol_match": order.symbol.upper() == SYMBOL,
        "sell_side": order.side.lower() == "sell",
        "stop_market_type": order.order_type.lower() in {"stop", "stop_market"},
        "requested_quantity_one": order.requested_quantity == QUANTITY,
        "cumulative_quantity_zero": order.cumulative_quantity == Decimal("0"),
        "remaining_quantity_one": order.remaining_quantity == QUANTITY,
        "expected_protection_value_match": order.stop_price == expected,
        "recognized_nonterminal_state": order.state.lower()
        in PROVISIONAL_NONTERMINAL_STATES,
        "position_long_one": decimal_value(position_quantity) == QUANTITY,
        "no_competing_sell_order": competing_sell_orders == 0,
    }
    provisional = all(validations.values())
    return ProtectionAssessment(
        provisional=provisional,
        classification=(
            State.PROVISIONAL_PROTECTION_OBSERVED.value
            if provisional
            else "INDETERMINATE"
        ),
        validations=validations,
    )


def normalize_explicit_price(value: object) -> Decimal:
    result = decimal_value(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if result <= 0:
        raise CommissioningError("price is not positive")
    return result


_TRUTH_VALUES = frozenset({"true", "false", "unknown"})
_DURATION_BUCKETS = frozenset({
    "under_1_second", "1_to_5_seconds", "5_to_15_seconds", "over_15_seconds",
    "unknown",
})
# "cancel_task_cancelled" is deliberately absent. A cancelled task raises
# asyncio.CancelledError, which propagates past every handler here, so no
# evidence write can follow it. The key could only ever have recorded "false",
# which states an observation this code is structurally unable to make.
ALLOWED_CANCEL_DIAGNOSTICS: Mapping[str, frozenset[str]] = MappingProxyType({
    "cancel_call_started": _TRUTH_VALUES,
    "cancel_call_returned": _TRUTH_VALUES,
    "session_healthy_after_cancel": _TRUTH_VALUES,
    "cancel_response_contained_accepted": _TRUTH_VALUES,
    "cancel_accepted_value": _TRUTH_VALUES,
    "cancel_elapsed_duration_bucket": _DURATION_BUCKETS,
    "cancel_timeout_layer": frozenset({
        "none", "not_applicable", "reconciliation_deadline", "transport",
        "unknown",
    }),
    "cancel_transport_classification": frozenset(
        SAFE_EXCEPTION_TYPES | {"UNKNOWN_EXCEPTION", "NONE"}
    ),
    "cancel_response_shape_classification": frozenset({
        "broker_rejected", "documented_envelope", "empty_result",
        "malformed_envelope", "not_reached", "unknown",
    }),
})


# "identifier_field_present" is deliberately not called
# "order_identifier_...": the evidence sanitizer rejects any key containing
# "order_id", and that guard must not be weakened for a diagnostic's benefit.
# The outer MCP layers (structuredContent, content list) are not classified
# here: every read call already traverses them through the same helper and
# they demonstrably work, so a classification would add nothing.
_PLACE_SHAPE_FACTS = frozenset({
    "data_object_present", "order_key_present", "order_value_null",
    "order_object_present", "identifier_field_present",
    "accepted_field_present", "unexpected_data_keys_present",
})
_PLACE_ENVELOPE_CLASSIFICATIONS = frozenset({
    # The order object was present and structurally an object. Whatever the
    # parser rejected was inside it.
    "order_object_returned",
    # data.order was explicitly null. The advertised outputSchema PERMITS
    # this, and the parser rejects it deliberately, so this classification
    # distinguishes a schema-legal response from a genuinely broken one.
    "order_null_schema_legal",
    # data carried keys beyond "order"; additionalProperties=false forbids it.
    "unexpected_data_keys",
    "order_key_absent",
    "data_not_an_object",
    "envelope_absent",
    "unknown",
})
# The order-object contract violation, named rather than inferred. Imported
# from the contract module so the vocabulary cannot drift from the schema it
# describes. An alias never carries a field VALUE.
_CONTRACT_SCOPES = frozenset({PLACE_ORDER_OBJECT_SCOPE})

# Keys are role-prefixed so a placement never overwrites another role's facts.
ALLOWED_PLACE_DIAGNOSTICS: Mapping[str, frozenset[str]] = MappingProxyType({
    **{
        f"{role}_{fact}": _TRUTH_VALUES
        for role in ("entry", "stop", "exit")
        for fact in _PLACE_SHAPE_FACTS
    },
    **{
        f"{role}_envelope_classification": _PLACE_ENVELOPE_CLASSIFICATIONS
        for role in ("entry", "stop", "exit")
    },
    **{
        f"{role}_contract_scope": _CONTRACT_SCOPES
        for role in ("entry", "stop", "exit")
    },
    **{
        f"{role}_contract_field_alias": PLACE_ORDER_FIELD_ALIAS_VALUES
        for role in ("entry", "stop", "exit")
    },
    **{
        f"{role}_contract_violation_kind": CONTRACT_VIOLATION_KINDS
        for role in ("entry", "stop", "exit")
    },
})

ALLOWED_EXIT_DIAGNOSTICS: Mapping[str, frozenset[str]] = MappingProxyType({
    "exit_call_started": _TRUTH_VALUES,
    "exit_call_returned": _TRUTH_VALUES,
    "exit_uniquely_attributed": _TRUTH_VALUES,
    "position_flat_observed": _TRUTH_VALUES,
    "exit_timeout_layer": frozenset({
        "none", "not_applicable", "reconciliation_deadline", "transport",
        "unknown",
    }),
    "exit_transport_classification": frozenset(
        SAFE_EXCEPTION_TYPES | {"UNKNOWN_EXCEPTION", "NONE"}
    ),
    "exit_response_shape_classification": frozenset({
        "broker_rejected", "documented_envelope", "empty_result",
        "malformed_envelope", "not_reached", "unknown",
    }),
    # How the sell itself finally read. "not_observed" means no attributable
    # order was seen at all, which is never on its own a reason to conclude.
    "exit_state_classification": frozenset({
        "filled", "working", "cancellation_pending", "cancelled",
        "rejected_or_failed", "partial_fill", "not_observed", "unknown",
    }),
})


def cancel_timeout_layer(*, returned: bool, classification: str) -> str:
    """Name the layer that timed out, or disclaim a timeout that never occurred.

    A cancel call that raised did not necessarily time out. An argument-contract
    rejection, a transport error, and a deadline expiry are different failures,
    and only the last of them may claim a timeout layer.

    An explicit broker rejection is terminal: no deadline governed it and no
    reconciliation follows it, so it reports "not_applicable" rather than the
    "none" used on the success path, where a later deadline may still expire.
    """

    if classification in TERMINAL_REJECTION_CLASSIFICATIONS:
        return "not_applicable"
    if returned:
        return "none"
    if classification in RECOGNIZED_TIMEOUT_CLASSIFICATIONS:
        return "transport"
    return "not_applicable"


def coarse_duration_bucket(seconds: float | None) -> str:
    """Bucket an elapsed duration; exact broker timings never reach evidence."""

    if seconds is None or seconds != seconds or seconds < 0:
        return "unknown"
    if seconds < 1:
        return "under_1_second"
    if seconds < 5:
        return "1_to_5_seconds"
    if seconds <= 15:
        return "5_to_15_seconds"
    return "over_15_seconds"


PROHIBITED_EVIDENCE_TERMS = (
    "account_number",
    "fingerprint",
    "order_id",
    "ref_id",
    "balance",
    "buying_power",
    "fill_price",
    "stop_price",
    "raw_response",
    "token",
    "cookie",
    "header",
    "oauth",
    "credential",
)


def validate_sanitized_evidence(value: object, path: str = "$") -> None:
    """Reject privacy-sensitive keys and unsupported evidence scalar types."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(term in normalized for term in PROHIBITED_EVIDENCE_TERMS):
                raise CommissioningError(f"prohibited evidence field at {path}")
            validate_sanitized_evidence(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            validate_sanitized_evidence(child, f"{path}[{index}]")
        return
    if value is not None and not isinstance(value, (str, bool, int, float)):
        raise CommissioningError(f"unsupported evidence scalar at {path}")


@dataclass
class SanitizedEvidence:
    run_id: str
    state_transitions: list[str] = field(default_factory=list)
    approved_tool_names: list[str] = field(default_factory=list)
    call_counts: dict[str, int] = field(default_factory=dict)
    boolean_validations: dict[str, bool] = field(default_factory=dict)
    normalized_state_names: list[str] = field(default_factory=list)
    conservative_classifications: list[str] = field(default_factory=list)
    timing_durations_ms: dict[str, int] = field(default_factory=dict)
    expected_vs_observed_json_types: dict[str, str] = field(default_factory=dict)
    record_counts: dict[str, int] = field(default_factory=dict)
    cancel_diagnostics: dict[str, str] = field(default_factory=dict)
    exit_diagnostics: dict[str, str] = field(default_factory=dict)
    place_diagnostics: dict[str, str] = field(default_factory=dict)
    completion_or_failure_reason: str = "IN_PROGRESS"
    planned_risk_usd: int = 15
    sanitized_exception_type: str = "NONE"
    failure_stage: str = "NONE"
    failure_category: str = "NONE"
    write_boundary_reached: bool = False
    write_attempt_counters: dict[str, int] = field(default_factory=lambda: {
        "entry_place": 0, "stop_place": 0, "stop_cancel": 0, "exit_place": 0,
    })
    required_operator_action: str = "NONE"


class AtomicEvidenceStore:
    """Durable one-run store with non-reusable run IDs and atomic completion."""

    def __init__(self, root: Path, run_id: str) -> None:
        if not RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
            raise CommissioningError("run ID is not a safe single path component")
        self.root = root
        self.run_id = run_id
        self.partial = root / f"{run_id}.partial"
        self.completed = root / run_id

    def start(self) -> None:
        if self.partial.exists() or self.completed.exists():
            raise CommissioningError("run ID already used")
        self.root.mkdir(parents=True, exist_ok=True)
        self.partial.mkdir()

    def persist(self, evidence: SanitizedEvidence) -> None:
        if not self.partial.is_dir():
            raise CommissioningError("evidence partial directory is absent")
        payload = asdict(evidence)
        validate_sanitized_evidence(payload)
        target = self.partial / "commissioning_evidence.json"
        temporary = self.partial / "commissioning_evidence.json.tmp"
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def finalize(self, evidence: SanitizedEvidence) -> Path:
        self.persist(evidence)
        os.replace(self.partial, self.completed)
        return self.completed / "commissioning_evidence.json"

    def recover_interrupted(self) -> tuple[SanitizedEvidence, str]:
        """Mark an existing partial run operator-required; never resume it."""
        if not self.partial.is_dir() or self.completed.exists():
            raise CommissioningError("no recoverable interrupted run exists")
        target = self.partial / "commissioning_evidence.json"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CommissioningError("interrupted evidence is unreadable") from exc
        expected = {item.name for item in field_iter(SanitizedEvidence)}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise CommissioningError("interrupted evidence shape is invalid")
        validate_sanitized_evidence(payload)
        evidence = SanitizedEvidence(**payload)
        last_safe_state = evidence.state_transitions[-1] if evidence.state_transitions else "UNKNOWN"
        evidence.state_transitions.append(State.OPERATOR_REQUIRED.value)
        evidence.normalized_state_names.append(State.OPERATOR_REQUIRED.value)
        evidence.conservative_classifications.append("OPERATOR_REQUIRED")
        evidence.completion_or_failure_reason = "INTERRUPTED_RUN_REQUIRES_OPERATOR"
        (self.partial / "commissioning_evidence.json.tmp").unlink(missing_ok=True)
        self.persist(evidence)
        return evidence, last_safe_state


def persist_standalone_failure(
    store: AtomicEvidenceStore,
    run_id: str,
    *,
    category: str,
    stage: str,
    error: BaseException,
    operator_action: str,
) -> Path:
    """Persist a sanitized pre-orchestrator failure without broker values."""

    evidence = SanitizedEvidence(run_id=run_id)
    evidence.state_transitions.append(State.FAILED_CLOSED.value)
    evidence.normalized_state_names.append(State.FAILED_CLOSED.value)
    evidence.conservative_classifications.append("FAILED_CLOSED")
    evidence.completion_or_failure_reason = category
    evidence.sanitized_exception_type = sanitized_exception_type(error)
    evidence.failure_stage = stage
    evidence.failure_category = category
    evidence.write_boundary_reached = False
    evidence.required_operator_action = operator_action
    store.start()
    store.persist(evidence)
    return store.partial / "commissioning_evidence.json"


@dataclass
class CommissioningMachine:
    """Strict state machine; broker results enter only as validated facts."""

    run_id: str
    evidence_store: AtomicEvidenceStore
    policy: CommissioningPolicy = field(default_factory=CommissioningPolicy)
    state: State = State.PREFLIGHT
    evidence: SanitizedEvidence = field(init=False)
    entry_place_attempted: bool = False
    stop_place_attempted: bool = False
    stop_cancel_attempted: bool = False
    stop_cancel_resolved: bool = False
    exit_place_attempted: bool = False
    preflight_complete: bool = False
    flatten_authorized: bool = False
    fill_price: Decimal | None = None
    expected_stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        self.evidence = SanitizedEvidence(run_id=self.run_id)

    def start(self) -> None:
        self.evidence_store.start()
        self._record_state(State.PREFLIGHT)

    def _record_state(self, state: State) -> None:
        self.state = state
        self.evidence.state_transitions.append(state.value)
        self.evidence.normalized_state_names.append(state.value)
        self.evidence_store.persist(self.evidence)

    def _require(self, *states: State) -> None:
        if self.state not in states:
            raise CommissioningError(
                f"state {self.state.value} not permitted for this transition"
            )

    def record_tool_call(self, name: str) -> None:
        allowed = {
            "get_accounts", "get_equity_positions", "get_equity_orders",
            "review_equity_order", "place_equity_order", "cancel_equity_order",
        }
        if name not in allowed:
            self.operator_required("PROHIBITED_TOOL_CALL")
            raise CommissioningError("prohibited tool call")
        self.evidence.approved_tool_names.append(name)
        self.evidence.call_counts[name] = self.evidence.call_counts.get(name, 0) + 1
        self.evidence_store.persist(self.evidence)

    def record_entry_reconciliation(
        self,
        category: str,
        *,
        normalized_order_state: str | None = None,
        attribution_unique: bool | None = None,
        candidate_count: int | None = None,
    ) -> None:
        allowed = {
            "ENTRY_PLACE_ACCEPTED_PENDING", "ENTRY_PLACE_ACCEPTED_FILLED",
            "ENTRY_PLACE_RESPONSE_MALFORMED",
            "ENTRY_PLACE_TRANSPORT_AMBIGUOUS",
            "ENTRY_RECONCILED_BY_ORDER", "ENTRY_RECONCILED_BY_POSITION",
            "ENTRY_RECONCILIATION_MULTIPLE_MATCHES",
            "ENTRY_RECONCILIATION_NOT_FOUND",
            "ENTRY_RECONCILIATION_INCONSISTENT",
            "ENTRY_RECONCILIATION_TIMEOUT",
        }
        if category not in allowed:
            raise CommissioningError("entry reconciliation category is invalid")
        self.evidence.conservative_classifications.append(category)
        if normalized_order_state is not None:
            self.evidence.normalized_state_names.append(normalized_order_state)
        if attribution_unique is not None:
            self.evidence.boolean_validations[
                "entry_attribution_unique"
            ] = attribution_unique
        if candidate_count is not None:
            self.evidence.record_counts[
                "entry_reconciliation_candidate_count"
            ] = candidate_count
        self.evidence_store.persist(self.evidence)

    def record_entry_reconciliation_poll(self) -> None:
        key = "entry_reconciliation_polls"
        self.evidence.record_counts[key] = self.evidence.record_counts.get(
            key, 0
        ) + 1
        self.evidence_store.persist(self.evidence)

    def record_stop_reconciliation(
        self,
        category: str,
        *,
        normalized_order_state: str | None = None,
        attribution_unique: bool | None = None,
        candidate_count: int | None = None,
    ) -> None:
        allowed = {
            "STOP_PLACE_ACCEPTED_PENDING", "STOP_PLACE_ACCEPTED_OPEN",
            "STOP_PLACE_ACCEPTED_TRIGGERED", "STOP_PLACE_ACCEPTED_FILLED",
            "STOP_PLACE_RESPONSE_MALFORMED",
            "STOP_PLACE_TRANSPORT_AMBIGUOUS",
            "STOP_RECONCILED_BY_ORDER",
            "STOP_RECONCILED_BY_POSITION_AND_ORDER",
            "STOP_ALREADY_FILLED_POSITION_FLAT",
            "STOP_RECONCILIATION_MULTIPLE_MATCHES",
            "STOP_RECONCILIATION_WRONG_QUANTITY",
            "STOP_RECONCILIATION_WRONG_SIDE",
            "STOP_RECONCILIATION_WRONG_SYMBOL",
            "STOP_RECONCILIATION_CONFLICTING_ORDER",
            "STOP_RECONCILIATION_NOT_FOUND",
            "STOP_RECONCILIATION_INCONSISTENT",
            "STOP_RECONCILIATION_TIMEOUT",
            "STOP_PARTIALLY_FILLED", "STOP_CANCEL_PENDING",
            "STOP_CANCEL_CONFIRMED",
        }
        if category not in allowed:
            raise CommissioningError("stop reconciliation category is invalid")
        self.evidence.conservative_classifications.append(category)
        if normalized_order_state is not None:
            self.evidence.normalized_state_names.append(normalized_order_state)
        if attribution_unique is not None:
            self.evidence.boolean_validations[
                "stop_attribution_unique"
            ] = attribution_unique
        if candidate_count is not None:
            self.evidence.record_counts[
                "stop_reconciliation_candidate_count"
            ] = candidate_count
        self.evidence.record_counts["stop_place_retries"] = 0
        self.evidence_store.persist(self.evidence)

    def record_stop_reconciliation_read(self, kind: str) -> None:
        if kind not in {"orders", "positions"}:
            raise CommissioningError("stop reconciliation read kind is invalid")
        key = f"stop_reconciliation_{kind}_reads"
        self.evidence.record_counts[key] = self.evidence.record_counts.get(
            key, 0
        ) + 1
        self.evidence_store.persist(self.evidence)

    def record_cancel_reconciliation(
        self,
        category: str,
        *,
        normalized_order_state: str | None = None,
        attribution_unique: bool | None = None,
        candidate_count: int | None = None,
    ) -> None:
        allowed = {
            "STOP_CANCEL_REQUEST_ACCEPTED", "STOP_CANCEL_PENDING",
            "STOP_CANCEL_CONFIRMED", "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
            "STOP_CANCEL_RESPONSE_MALFORMED",
            "STOP_CANCEL_RECONCILED_BY_ORDER",
            "STOP_CANCEL_ALREADY_TERMINAL",
            "STOP_CANCEL_STOP_FILLED_POSITION_FLAT",
            "STOP_CANCEL_PARTIAL_FILL", "STOP_CANCEL_STILL_ACTIVE",
            "STOP_CANCEL_MULTIPLE_MATCHES", "STOP_CANCEL_INCONSISTENT",
            "STOP_CANCEL_NOT_FOUND", "STOP_CANCEL_TIMEOUT",
        }
        if category not in allowed:
            raise CommissioningError(
                "cancel reconciliation category is invalid"
            )
        self.evidence.conservative_classifications.append(category)
        if normalized_order_state is not None:
            self.evidence.normalized_state_names.append(
                normalized_order_state
            )
        if attribution_unique is not None:
            self.evidence.boolean_validations[
                "cancel_attribution_unique"
            ] = attribution_unique
        if candidate_count is not None:
            self.evidence.record_counts[
                "cancel_reconciliation_candidate_count"
            ] = candidate_count
        self.evidence.record_counts["stop_cancel_retries"] = 0
        self.evidence_store.persist(self.evidence)

    def record_cancel_diagnostic(self, key: str, value: str) -> None:
        """Record one allowlisted cancel observation; values are never free text."""
        allowed = ALLOWED_CANCEL_DIAGNOSTICS.get(key)
        if allowed is None:
            raise CommissioningError("cancel diagnostic key is invalid")
        if not isinstance(value, str) or value not in allowed:
            raise CommissioningError("cancel diagnostic value is invalid")
        self.evidence.cancel_diagnostics[key] = value
        self.evidence_store.persist(self.evidence)

    def record_place_diagnostic(self, key: str, value: str) -> None:
        """Record one allowlisted structural fact about a place response.

        These are shape facts only. No response body, key name, identifier,
        price, or exception text can pass: both the key and the value must
        appear in the fixed allowlist or this raises.
        """
        allowed = ALLOWED_PLACE_DIAGNOSTICS.get(key)
        if allowed is None:
            raise CommissioningError("place diagnostic key is invalid")
        if not isinstance(value, str) or value not in allowed:
            raise CommissioningError("place diagnostic value is invalid")
        self.evidence.place_diagnostics[key] = value
        self.evidence_store.persist(self.evidence)

    def record_exit_diagnostic(self, key: str, value: str) -> None:
        """Record one allowlisted exit observation; values are never free text."""
        allowed = ALLOWED_EXIT_DIAGNOSTICS.get(key)
        if allowed is None:
            raise CommissioningError("exit diagnostic key is invalid")
        if not isinstance(value, str) or value not in allowed:
            raise CommissioningError("exit diagnostic value is invalid")
        self.evidence.exit_diagnostics[key] = value
        self.evidence_store.persist(self.evidence)

    def record_exit_reconciliation(
        self,
        category: str,
        *,
        normalized_order_state: str | None = None,
        attribution_unique: bool | None = None,
        candidate_count: int | None = None,
    ) -> None:
        allowed = {
            "EXIT_PLACE_RESPONSE_MALFORMED", "EXIT_PLACE_TRANSPORT_AMBIGUOUS",
            "EXIT_PLACE_ACCEPTED_PENDING", "EXIT_PLACE_ACCEPTED_FILLED",
            "EXIT_RECONCILED_BY_ORDER", "EXIT_RECONCILED_BY_FLAT_POSITION",
            "EXIT_STILL_WORKING", "EXIT_RECONCILIATION_MULTIPLE_MATCHES",
            "EXIT_RECONCILIATION_NOT_FOUND",
            "EXIT_RECONCILIATION_INCONSISTENT",
            "EXIT_RECONCILIATION_CONFLICTING_ORDER",
            "EXIT_RECONCILIATION_TIMEOUT",
        }
        if category not in allowed:
            raise CommissioningError("exit reconciliation category is invalid")
        self.evidence.conservative_classifications.append(category)
        if normalized_order_state is not None:
            self.evidence.normalized_state_names.append(normalized_order_state)
        if attribution_unique is not None:
            self.evidence.boolean_validations[
                "exit_attribution_unique"
            ] = attribution_unique
        if candidate_count is not None:
            self.evidence.record_counts[
                "exit_reconciliation_candidate_count"
            ] = candidate_count
        self.evidence_store.persist(self.evidence)

    def record_exit_reconciliation_read(self, kind: str) -> None:
        if kind not in {"orders", "positions"}:
            raise CommissioningError(
                "exit reconciliation read kind is invalid"
            )
        key = f"exit_reconciliation_{kind}_reads"
        self.evidence.record_counts[key] = self.evidence.record_counts.get(
            key, 0
        ) + 1
        self.evidence_store.persist(self.evidence)

    def record_cancel_reconciliation_read(self, kind: str) -> None:
        if kind not in {"orders", "positions"}:
            raise CommissioningError(
                "cancel reconciliation read kind is invalid"
            )
        key = f"cancel_reconciliation_{kind}_reads"
        self.evidence.record_counts[key] = self.evidence.record_counts.get(
            key, 0
        ) + 1
        self.evidence_store.persist(self.evidence)

    def persist_write_boundary(self, role: str, phase: str) -> None:
        if role not in {"entry", "stop", "cancel", "exit"} or phase not in {"before", "after"}:
            raise CommissioningError("invalid write boundary")
        key = f"{role}_write_{phase}"
        existed = key in self.evidence.boolean_validations
        previous = self.evidence.boolean_validations.get(key)
        self.evidence.boolean_validations[key] = True
        self.evidence.write_attempt_counters = self.write_attempt_counts()
        try:
            self.evidence_store.persist(self.evidence)
        except Exception:
            if existed:
                self.evidence.boolean_validations[key] = previous
            else:
                self.evidence.boolean_validations.pop(key, None)
            self.evidence.write_attempt_counters = self.write_attempt_counts()
            raise

    def write_attempt_counts(self) -> dict[str, int]:
        return {
            "entry_place": int(self.evidence.boolean_validations.get(
                "entry_write_before", False)),
            "stop_place": int(self.evidence.boolean_validations.get(
                "stop_write_before", False)),
            "stop_cancel": int(self.evidence.boolean_validations.get(
                "cancel_write_before", False)),
            "exit_place": int(self.evidence.boolean_validations.get(
                "exit_write_before", False)),
        }

    def record_failure_diagnostic(
        self,
        *,
        category: str,
        stage: str,
        error: BaseException,
        operator_action: str,
    ) -> None:
        if category not in ALLOWED_FAILURE_CATEGORIES:
            category = "INTERNAL_INVARIANT_FAILURE"
        if stage not in ALLOWED_FAILURE_STAGES:
            stage = "INTERNAL_INVARIANT"
        counters = self.write_attempt_counts()
        self.evidence.sanitized_exception_type = sanitized_exception_type(error)
        self.evidence.failure_stage = stage
        self.evidence.failure_category = category
        self.evidence.write_attempt_counters = counters
        self.evidence.write_boundary_reached = any(counters.values())
        self.evidence.required_operator_action = operator_action
        self.evidence.completion_or_failure_reason = category
        self.evidence_store.persist(self.evidence)

    def preflight_passed(self, *, strategy_enabled: bool,
                         commissioned_accounts: int,
                         aapl_position_quantity: object,
                         related_open_orders: int,
                         regular_market_hours: bool) -> None:
        self._require(State.PREFLIGHT)
        checks = {
            "strategy_disabled": strategy_enabled is False,
            "one_commissioned_account": commissioned_accounts == 1,
            "no_existing_aapl_position": decimal_value(aapl_position_quantity) == 0,
            "no_related_open_aapl_order": related_open_orders == 0,
            "regular_market_hours": regular_market_hours,
            "one_share_policy": self.policy.quantity == QUANTITY,
            "no_second_trade": not self.policy.second_trade_allowed,
        }
        self.evidence.boolean_validations.update(checks)
        if not all(checks.values()):
            self.failed_closed("PREFLIGHT_REJECTED")
            raise CommissioningError("preflight rejected")
        self.preflight_complete = True

    def entry_reviewed(self, *, accepted: bool) -> None:
        self._require(State.PREFLIGHT)
        if not self.preflight_complete:
            self.failed_closed("PREFLIGHT_NOT_COMPLETED")
            raise CommissioningError("entry review requires completed preflight")
        self.evidence.boolean_validations["entry_review_accepted"] = accepted
        if not accepted:
            self.failed_closed("ENTRY_REVIEW_REJECTED")
            return
        self._record_state(State.ENTRY_REVIEWED)

    def authorize_entry(self, authorization: RuntimeAuthorization,
                        *, now: datetime) -> None:
        self._require(State.ENTRY_REVIEWED)
        try:
            authorization.consume(now=now, expected_stage=ENTRY_AUTHORIZATION_STAGE,
                                  run_id=self.run_id)
        except CommissioningError:
            self.failed_closed("ENTRY_AUTHORIZATION_REJECTED")
            raise
        self.evidence.conservative_classifications.append(
            "ENTRY_AUTHORIZATION_APPROVED"
        )
        self._record_state(State.ENTRY_AUTHORIZED)

    def entry_submitted(self, disposition: WriteDisposition) -> None:
        self._require(State.ENTRY_AUTHORIZED)
        if self.entry_place_attempted:
            self.operator_required("SECOND_ENTRY_ATTEMPT")
            raise CommissioningError("entry placement may occur only once")
        self.entry_place_attempted = True
        if disposition is WriteDisposition.AMBIGUOUS:
            self.operator_required("AMBIGUOUS_ENTRY_SUBMISSION")
        elif disposition is WriteDisposition.REJECTED:
            self.failed_closed("ENTRY_SUBMISSION_REJECTED")
        else:
            self._record_state(State.ENTRY_SUBMITTED)
            self._record_state(State.ENTRY_RECONCILING)

    def entry_observed(self, *, order: EntryFillObservation,
                       position_quantity: object,
                       deadline_exceeded: bool = False,
                       detection_seconds: float = 0) -> None:
        self._require(State.ENTRY_RECONCILING)
        self.evidence.timing_durations_ms["entry_fill_detection"] = round(
            detection_seconds * 1000
        )
        deadline_exceeded = deadline_exceeded or detection_seconds > ENTRY_FILL_DEADLINE_SECONDS
        identity_checks = {
            "entry_commissioned_account": order.commissioned_account,
            "entry_symbol_match": order.symbol.upper() == SYMBOL,
            "entry_buy_side": order.side.lower() == "buy",
            "entry_market_type": order.order_type.lower() == "market",
            "entry_requested_quantity_one": order.requested_quantity == QUANTITY,
        }
        self.evidence.boolean_validations.update(identity_checks)
        if not all(identity_checks.values()):
            self.operator_required("ENTRY_ORDER_IDENTITY_MISMATCH")
            return
        if order.cumulative_quantity is None or order.remaining_quantity is None:
            self.operator_required("ENTRY_QUANTITY_INDETERMINATE")
            return
        filled = decimal_value(order.cumulative_quantity)
        remaining = decimal_value(order.remaining_quantity)
        position = decimal_value(position_quantity)
        if (filled not in {Decimal("0"), QUANTITY}
                or remaining not in {Decimal("0"), QUANTITY}
                or filled + remaining != QUANTITY
                or position not in {Decimal("0"), QUANTITY}):
            self.operator_required("INVALID_ENTRY_QUANTITY")
            return
        if filled == 0:
            if classify_order_state(order.state) is not OrderStateClass.ACTIVE_OR_WORKING:
                self.operator_required("ENTRY_STATE_INDETERMINATE")
                return
            if deadline_exceeded:
                self.operator_required("ENTRY_FILL_DEADLINE_EXCEEDED")
            return
        if (classify_order_state(order.state) is not OrderStateClass.TERMINAL_FILLED
                or remaining != 0 or position != QUANTITY):
            self.operator_required("ENTRY_POSITION_MISMATCH")
            return
        if order.average_fill_price is None:
            self.operator_required("MISSING_ENTRY_FILL_PRICE")
            return
        self.fill_price = normalize_explicit_price(order.average_fill_price)
        self.expected_stop_price = normalize_stop_price(self.fill_price)
        self._record_state(State.ENTRY_FILLED)

    def stop_reviewed(self, *, accepted: bool, construction_seconds: float) -> None:
        self._require(State.ENTRY_FILLED)
        self.evidence.timing_durations_ms["stop_construction_review"] = round(
            construction_seconds * 1000
        )
        if construction_seconds > STOP_REVIEW_DEADLINE_SECONDS:
            self.operator_required("STOP_REVIEW_DEADLINE_EXCEEDED")
        elif not accepted:
            self.operator_required("STOP_REVIEW_REJECTED")
        else:
            self._record_state(State.STOP_REVIEWED)

    def stop_submitted(self, disposition: WriteDisposition) -> None:
        self._require(State.STOP_REVIEWED)
        if self.stop_place_attempted:
            self.operator_required("SECOND_STOP_ATTEMPT")
            raise CommissioningError("stop placement may occur only once")
        self.stop_place_attempted = True
        if disposition is WriteDisposition.ACCEPTED:
            self._record_state(State.STOP_SUBMITTED)
            self._record_state(State.STOP_RECONCILING)
        elif disposition is WriteDisposition.AMBIGUOUS:
            self.operator_required("AMBIGUOUS_STOP_SUBMISSION")
        else:
            self.operator_required("STOP_SUBMISSION_REJECTED")

    def stop_filled_before_protection(
        self,
        *,
        cumulative_quantity: object,
        remaining_quantity: object,
        position_quantity: object,
    ) -> None:
        self._require(State.STOP_RECONCILING)
        cumulative = decimal_value(cumulative_quantity)
        remaining = decimal_value(remaining_quantity)
        position = decimal_value(position_quantity)
        checks = {
            "stop_filled_quantity_one": cumulative == QUANTITY,
            "stop_filled_remaining_zero": remaining == Decimal("0"),
            "stop_filled_position_flat": position == Decimal("0"),
        }
        self.evidence.boolean_validations.update(checks)
        if not all(checks.values()):
            self.operator_required("STOP_RECONCILIATION_INCONSISTENT")
            return
        self._record_state(State.VERIFIED_FLAT)

    def protection_observed(self, *, order: OrderObservation,
                            position_quantity: object,
                            competing_sell_orders: int,
                            observation_seconds: float,
                            unprotected_seconds: float) -> ProtectionAssessment:
        self._require(State.STOP_RECONCILING)
        self.evidence.timing_durations_ms["stop_observation"] = round(
            observation_seconds * 1000
        )
        self.evidence.timing_durations_ms["detected_fill_unprotected"] = round(
            unprotected_seconds * 1000
        )
        if (observation_seconds > STOP_OBSERVATION_DEADLINE_SECONDS
                or unprotected_seconds > MAX_UNPROTECTED_SECONDS):
            self.operator_required("PROTECTION_DEADLINE_EXCEEDED")
            return ProtectionAssessment(False, "INDETERMINATE", {})
        if self.expected_stop_price is None:
            self.operator_required("EXPECTED_STOP_PRICE_ABSENT")
            return ProtectionAssessment(False, "INDETERMINATE", {})
        result = assess_provisional_protection(
            order=order,
            position_quantity=position_quantity,
            expected_stop_price=self.expected_stop_price,
            competing_sell_orders=competing_sell_orders,
        )
        self.evidence.boolean_validations.update(result.validations)
        self.evidence.conservative_classifications.append(result.classification)
        if not result.provisional:
            self.operator_required("PROTECTION_INDETERMINATE")
            return result
        self._record_state(State.PROVISIONAL_PROTECTION_OBSERVED)
        self._record_state(State.EXIT_AUTHORIZATION_REQUIRED)
        return result

    def authorize_flatten(self, authorization: RuntimeAuthorization,
                          *, now: datetime) -> None:
        self._require(State.EXIT_AUTHORIZATION_REQUIRED)
        try:
            authorization.consume(now=now, expected_stage=FLATTEN_AUTHORIZATION_STAGE,
                                  run_id=self.run_id)
        except CommissioningError:
            self.operator_required("FLATTEN_AUTHORIZATION_REJECTED")
            raise
        self.evidence.conservative_classifications.append(
            "FLATTEN_AUTHORIZATION_APPROVED"
        )
        self.flatten_authorized = True

    def stop_cancel_requested(self, disposition: WriteDisposition) -> None:
        self._require(State.EXIT_AUTHORIZATION_REQUIRED)
        if not self.flatten_authorized:
            self.operator_required("FLATTEN_AUTHORIZATION_REQUIRED")
            raise CommissioningError("flatten authorization is required")
        if self.stop_cancel_attempted:
            self.operator_required("SECOND_CANCEL_ATTEMPT")
            raise CommissioningError("stop cancellation may occur only once")
        self.stop_cancel_attempted = True
        if disposition in {
            WriteDisposition.ACCEPTED,
            WriteDisposition.AMBIGUOUS,
        }:
            self._record_state(State.STOP_CANCEL_REQUESTED)
            self._record_state(State.STOP_CANCEL_RECONCILING)
        else:
            self.operator_required("STOP_CANCEL_REJECTED")

    def stop_cancel_observed(self, *, stop_state: str,
                             cumulative_quantity: object,
                             remaining_quantity: object,
                             position_quantity: object) -> str:
        self._require(State.STOP_CANCEL_RECONCILING)
        classification = classify_order_state(stop_state)
        normalized = stop_state.strip().lower()
        cumulative = decimal_value(cumulative_quantity)
        remaining = decimal_value(remaining_quantity)
        position = decimal_value(position_quantity)
        if (cumulative not in {Decimal("0"), QUANTITY}
                or remaining not in {Decimal("0"), QUANTITY}
                or cumulative + remaining != QUANTITY
                or position not in {Decimal("0"), QUANTITY}):
            self.operator_required("STOP_CANCEL_QUANTITY_INVALID")
            return "STOP_CANCEL_INCONSISTENT"
        if classification is OrderStateClass.TERMINAL_FILLED:
            if cumulative == QUANTITY and remaining == 0 and position == 0:
                category = "STOP_CANCEL_STOP_FILLED_POSITION_FLAT"
                self.record_cancel_reconciliation(
                    category,
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                self._record_state(State.VERIFIED_FLAT)
                return category
            self.operator_required("STOP_FILL_POSITION_MISMATCH")
            return "STOP_CANCEL_INCONSISTENT"
        if classification is OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED:
            self.record_cancel_reconciliation(
                "STOP_CANCEL_PARTIAL_FILL",
                normalized_order_state=normalized,
                attribution_unique=True,
                candidate_count=1,
            )
            self.operator_required("STOP_CANCEL_PARTIAL_FILL")
            return "STOP_CANCEL_PARTIAL_FILL"
        if classification is OrderStateClass.CANCELLATION_PENDING:
            if cumulative != 0 or remaining != QUANTITY:
                self.operator_required("STOP_CANCEL_QUANTITY_INVALID")
                return "STOP_CANCEL_INCONSISTENT"
            category = "STOP_CANCEL_PENDING"
            self.record_cancel_reconciliation(
                category,
                normalized_order_state=normalized,
                attribution_unique=True,
                candidate_count=1,
            )
            return category
        if classification is OrderStateClass.ACTIVE_OR_WORKING:
            if cumulative != 0 or remaining != QUANTITY:
                self.operator_required("STOP_CANCEL_QUANTITY_INVALID")
                return "STOP_CANCEL_INCONSISTENT"
            category = "STOP_CANCEL_STILL_ACTIVE"
            self.record_cancel_reconciliation(
                category,
                normalized_order_state=normalized,
                attribution_unique=True,
                candidate_count=1,
            )
            return category
        if classification not in {
            OrderStateClass.TERMINAL_CANCELLED,
            OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
        }:
            self.operator_required("STOP_CANCEL_STATE_INDETERMINATE")
            return "STOP_CANCEL_INCONSISTENT"
        if cumulative != 0 or remaining != QUANTITY:
            self.operator_required("STOP_CANCEL_QUANTITY_INVALID")
            return "STOP_CANCEL_INCONSISTENT"
        category = (
            "STOP_CANCEL_CONFIRMED"
            if classification is OrderStateClass.TERMINAL_CANCELLED
            else "STOP_CANCEL_ALREADY_TERMINAL"
        )
        self.record_cancel_reconciliation(
            category,
            normalized_order_state=normalized,
            attribution_unique=True,
            candidate_count=1,
        )
        self.stop_cancel_resolved = True
        if position == 0:
            self._record_state(State.VERIFIED_FLAT)
        elif position != QUANTITY:
            self.operator_required("POST_CANCEL_POSITION_INVALID")
            return "STOP_CANCEL_INCONSISTENT"
        return category

    def exit_submitted(self, disposition: WriteDisposition,
                       *, latest_position_quantity: object) -> None:
        self._require(State.STOP_CANCEL_RECONCILING)
        position = decimal_value(latest_position_quantity)
        if position == 0:
            self._record_state(State.VERIFIED_FLAT)
            return
        if position != QUANTITY:
            self.operator_required("EXIT_POSITION_INVALID")
            return
        if not self.stop_cancel_resolved:
            self.operator_required("PROTECTIVE_STOP_UNRESOLVED")
            return
        if self.exit_place_attempted:
            self.operator_required("SECOND_EXIT_ATTEMPT")
            raise CommissioningError("exit placement may occur only once")
        self.exit_place_attempted = True
        # An ambiguous submission is treated exactly like an accepted one for
        # STATE purposes only, so that bounded read-only reconciliation can run.
        # It is not a conclusion: the sell may already be filled at the broker,
        # and refusing to look leaves a filled exit unrecognized. The write is
        # already spent -- exit_place_attempted is set above and the adapter
        # holds a one-shot reservation -- so no second exit can follow, and
        # reconciliation must still prove flatness or hand off.
        if disposition in {WriteDisposition.ACCEPTED, WriteDisposition.AMBIGUOUS}:
            self._record_state(State.EXIT_SUBMITTED)
            self._record_state(State.EXIT_RECONCILING)
        else:
            self.operator_required("EXIT_SUBMISSION_REJECTED")

    def exit_observed(self, *, position_quantity: object,
                      related_nonterminal_orders: int,
                      deadline_exceeded: bool = False) -> None:
        self._require(State.EXIT_RECONCILING)
        position = decimal_value(position_quantity)
        if position not in {Decimal("0"), QUANTITY}:
            self.operator_required("FINAL_POSITION_INVALID")
        elif position != 0:
            if deadline_exceeded:
                self.operator_required("EXIT_CONFIRMATION_DEADLINE_EXCEEDED")
        elif related_nonterminal_orders != 0:
            self.operator_required("RELATED_ORDER_REMAINS")
        else:
            self._record_state(State.VERIFIED_FLAT)

    def operator_required(self, reason: str) -> None:
        self.evidence.completion_or_failure_reason = reason
        self.evidence.conservative_classifications.append("OPERATOR_REQUIRED")
        self._record_state(State.OPERATOR_REQUIRED)

    def failed_closed(self, reason: str) -> None:
        self.evidence.completion_or_failure_reason = reason
        self.evidence.conservative_classifications.append("FAILED_CLOSED")
        self._record_state(State.FAILED_CLOSED)

    def complete(self, *, related_nonterminal_orders: int) -> Path:
        self._require(State.VERIFIED_FLAT)
        if related_nonterminal_orders != 0:
            self.operator_required("RELATED_ORDER_REMAINS")
            raise CommissioningError("cannot complete with a related order")
        self.evidence.boolean_validations["verified_flat"] = True
        self.evidence.boolean_validations["no_related_nonterminal_orders"] = True
        self.evidence.completion_or_failure_reason = "COMPLETE"
        self._record_state(State.COMPLETE)
        return self.evidence_store.finalize(self.evidence)
