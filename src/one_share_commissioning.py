"""Offline-safe state model for the attended one-share commissioning run.

This module contains no broker initialization or configuration loading. Broker
I/O is injected by the launcher through ``RobinhoodCommissioningAdapter``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


PLANNED_RISK_USD = Decimal("15")
SYMBOL = "AAPL"
QUANTITY = Decimal("1")
ENTRY_FILL_DEADLINE_SECONDS = 30
STOP_REVIEW_DEADLINE_SECONDS = 10
STOP_OBSERVATION_DEADLINE_SECONDS = 30
MAX_UNPROTECTED_SECONDS = 45
ENTRY_AUTHORIZATION_PHRASE = (
    "AUTHORIZE ORB LIVE AAPL ONE SHARE ENTRY PLANNED RISK 15"
)
FLATTEN_AUTHORIZATION_PHRASE = (
    "AUTHORIZE ORB LIVE AAPL ONE SHARE CONTROLLED FLATTEN"
)


class CommissioningError(RuntimeError):
    """A fail-closed commissioning validation error."""


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
    phrase: str
    symbol: str
    quantity: Decimal
    planned_risk_usd: Decimal
    acknowledges_loss_may_exceed_plan: bool
    operator_present: bool
    robinhood_ui_available: bool
    no_second_trade: bool
    used: bool = False

    def consume(self, *, now: datetime, expected_phrase: str, run_id: str) -> None:
        if self.used:
            raise CommissioningError("authorization is not reusable")
        if self.run_id != run_id or self.phrase != expected_phrase:
            raise CommissioningError("authorization scope mismatch")
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
        self.used = True


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
    completion_or_failure_reason: str = "IN_PROGRESS"
    planned_risk_usd: int = 15


class AtomicEvidenceStore:
    """Durable one-run store with non-reusable run IDs and atomic completion."""

    def __init__(self, root: Path, run_id: str) -> None:
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
            authorization.consume(now=now, expected_phrase=ENTRY_AUTHORIZATION_PHRASE,
                                  run_id=self.run_id)
        except CommissioningError:
            self.failed_closed("ENTRY_AUTHORIZATION_REJECTED")
            raise
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

    def entry_observed(self, *, filled_quantity: object,
                       position_quantity: object, fill_price: object | None,
                       deadline_exceeded: bool = False,
                       detection_seconds: float = 0) -> None:
        self._require(State.ENTRY_RECONCILING)
        self.evidence.timing_durations_ms["entry_fill_detection"] = round(
            detection_seconds * 1000
        )
        deadline_exceeded = deadline_exceeded or detection_seconds > ENTRY_FILL_DEADLINE_SECONDS
        filled = decimal_value(filled_quantity)
        position = decimal_value(position_quantity)
        if filled not in {Decimal("0"), QUANTITY} or position not in {Decimal("0"), QUANTITY}:
            self.operator_required("INVALID_ENTRY_QUANTITY")
            return
        if filled == 0:
            if deadline_exceeded:
                self.failed_closed("ENTRY_FILL_DEADLINE_EXCEEDED")
            return
        if position != QUANTITY:
            self.operator_required("ENTRY_POSITION_MISMATCH")
            return
        if fill_price is None:
            self.operator_required("MISSING_ENTRY_FILL_PRICE")
            return
        self.fill_price = normalize_explicit_price(fill_price)
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
            authorization.consume(now=now, expected_phrase=FLATTEN_AUTHORIZATION_PHRASE,
                                  run_id=self.run_id)
        except CommissioningError:
            self.operator_required("FLATTEN_AUTHORIZATION_REJECTED")
            raise
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
        if disposition is WriteDisposition.ACCEPTED:
            self._record_state(State.STOP_CANCEL_REQUESTED)
            self._record_state(State.STOP_CANCEL_RECONCILING)
        elif disposition is WriteDisposition.AMBIGUOUS:
            self.operator_required("AMBIGUOUS_STOP_CANCEL")
        else:
            self.operator_required("STOP_CANCEL_REJECTED")

    def stop_cancel_observed(self, *, stop_terminal: bool, stop_filled: bool,
                             position_quantity: object) -> None:
        self._require(State.STOP_CANCEL_RECONCILING)
        position = decimal_value(position_quantity)
        if stop_filled:
            if position == 0:
                self._record_state(State.VERIFIED_FLAT)
            else:
                self.operator_required("STOP_FILL_POSITION_MISMATCH")
            return
        if not stop_terminal:
            return
        self.stop_cancel_resolved = True
        if position == 0:
            self._record_state(State.VERIFIED_FLAT)
        elif position != QUANTITY:
            self.operator_required("POST_CANCEL_POSITION_INVALID")

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
        if disposition is WriteDisposition.ACCEPTED:
            self._record_state(State.EXIT_SUBMITTED)
            self._record_state(State.EXIT_RECONCILING)
        elif disposition is WriteDisposition.AMBIGUOUS:
            self.operator_required("AMBIGUOUS_EXIT_SUBMISSION")
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
