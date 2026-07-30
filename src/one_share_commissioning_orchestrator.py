"""Injected, attended orchestration for the one-share commissioning run."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from src.live_order_state_model import OrderStateClass, classify_order_state
from src.one_share_commissioning import (
    ALLOWED_FAILURE_CATEGORIES, AtomicEvidenceStore, AuthorizationInputError,
    CommissioningDiagnosticError, CommissioningError,
    CommissioningMachine, EntryWindowError,
    EASTERN, ENTRY_FILL_DEADLINE_SECONDS, EntryFillObservation, OrderObservation,
    RuntimeAuthorization, State, STOP_OBSERVATION_DEADLINE_SECONDS,
    WriteDisposition, cancel_timeout_layer, coarse_duration_bucket,
    sanitized_exception_type,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError, BrokerRejectedError, CommissioningOrderRecord,
    CommissioningPositionRecord, RobinhoodCommissioningAdapter,
)


POLL_SECONDS = 1.0
STOP_CANCEL_DEADLINE_SECONDS = 30
EXIT_FILL_DEADLINE_SECONDS = 30
FINAL_FLAT_DEADLINE_SECONDS = 15


@dataclass(frozen=True)
class CommissioningTimePolicy:
    execution_date: date
    entry_start: datetime
    entry_cutoff: datetime
    mandatory_risk_off: datetime
    verified_flat_deadline: datetime
    attended_end: datetime

    def __post_init__(self) -> None:
        values = (
            self.entry_start, self.entry_cutoff, self.mandatory_risk_off,
            self.verified_flat_deadline, self.attended_end,
        )
        if any(
            value.tzinfo is None or value.utcoffset() is None
            or value.tzinfo != EASTERN
            for value in values
        ):
            raise EntryWindowError(
                "commissioning policy must use America/New_York"
            )
        if any(value.date() != self.execution_date for value in values):
            raise EntryWindowError(
                "commissioning policy values must use the execution date"
            )
        if not (
            self.entry_start < self.entry_cutoff < self.mandatory_risk_off
            <= self.verified_flat_deadline <= self.attended_end
        ):
            raise EntryWindowError("commissioning policy ordering is invalid")


class OperatorRequired(CommissioningError):
    pass

def _validated_policy_time(
    now: datetime, policy: CommissioningTimePolicy
) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None or now.tzinfo != EASTERN:
        raise EntryWindowError("commissioning runtime must use America/New_York")
    if now.date() != policy.execution_date:
        raise EntryWindowError("commissioning execution date mismatch")
    return now


def validate_commissioning_entry_time(
    now: datetime, policy: CommissioningTimePolicy
) -> None:
    current = _validated_policy_time(now, policy)
    if not policy.entry_start <= current < policy.entry_cutoff:
        raise EntryWindowError("commissioning entry time is outside policy")


def mandatory_risk_off_due(
    now: datetime, policy: CommissioningTimePolicy
) -> bool:
    return _validated_policy_time(now, policy) >= policy.mandatory_risk_off


def verified_flat_deadline_reached(
    now: datetime, policy: CommissioningTimePolicy
) -> bool:
    return _validated_policy_time(now, policy) >= policy.verified_flat_deadline


@dataclass(frozen=True)
class EmergencyHandoff:
    current_state: str
    reason_code: str
    position_may_exist: bool
    stop_may_remain_executable: bool
    fixed_one_share: bool
    expected_operator_action: str


@dataclass(frozen=True)
class OrchestrationResult:
    state: State
    evidence_path: Path


def entry_arguments(account_number: str) -> dict[str, Any]:
    return {
        "account_number": account_number, "symbol": "AAPL", "side": "buy",
        "type": "market", "quantity": "1", "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


def stop_arguments(account_number: str, stop_price: Decimal) -> dict[str, Any]:
    return {
        "account_number": account_number, "symbol": "AAPL", "side": "sell",
        "type": "stop_market", "quantity": "1", "time_in_force": "gtc",
        "market_hours": "regular_hours", "stop_price": format(stop_price, "f"),
    }


def exit_arguments(account_number: str) -> dict[str, Any]:
    return {
        "account_number": account_number, "symbol": "AAPL", "side": "sell",
        "type": "market", "quantity": "1", "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass
class OneShareCommissioningOrchestrator:
    run_id: str
    adapter: RobinhoodCommissioningAdapter
    monotonic: Callable[[], float]
    sleep: Callable[[float], Awaitable[None]]
    now_eastern: Callable[[], datetime]
    authorization_provider: Callable[[str], Any]
    evidence_store: AtomicEvidenceStore
    emergency_handoff: Callable[[EmergencyHandoff], Any]
    strategy_enabled: bool
    regular_market_hours: bool
    time_policy: CommissioningTimePolicy
    machine: CommissioningMachine = field(init=False)
    _last_write: str | None = field(default=None, init=False)
    _stage: str = field(default="ORCHESTRATOR_INITIALIZATION", init=False)

    def __post_init__(self) -> None:
        self.machine = CommissioningMachine(self.run_id, self.evidence_store)

    @property
    def account_number(self) -> str:
        return self.adapter.commissioned_account_number

    async def _handoff(self, reason: str, *, position: bool,
                       stop: bool, action: str) -> None:
        if self.machine.evidence.failure_category == "NONE":
            self.machine.record_failure_diagnostic(
                category=self._failure_category(reason), stage=self._stage,
                error=CommissioningError(reason), operator_action=action,
            )
        if self.machine.state is not State.OPERATOR_REQUIRED:
            self.machine.operator_required(reason)
        await _maybe_await(self.emergency_handoff(EmergencyHandoff(
            current_state=self.machine.state.value, reason_code=reason,
            position_may_exist=position, stop_may_remain_executable=stop,
            fixed_one_share=True, expected_operator_action=action,
        )))
        raise OperatorRequired(reason)

    def _failure_category(self, reason: str | None = None) -> str:
        if reason in ALLOWED_FAILURE_CATEGORIES:
            return reason
        if reason in {"AMBIGUOUS_ENTRY_SUBMISSION", "AMBIGUOUS_ENTRY_PLACE"}:
            return "ENTRY_PLACE_AMBIGUOUS"
        mapping = {
            "ENTRY_AUTHORIZATION_INPUT": "ENTRY_AUTHORIZATION_INTERRUPTED",
            "ENTRY_AUTHORIZATION_CONSUMPTION": "ENTRY_AUTHORIZATION_INTERRUPTED",
            "FLATTEN_AUTHORIZATION_INPUT": "FLATTEN_AUTHORIZATION_INTERRUPTED",
            "FLATTEN_AUTHORIZATION_CONSUMPTION": "FLATTEN_AUTHORIZATION_INTERRUPTED",
            "ENTRY_WINDOW_VALIDATION": "ENTRY_WINDOW_REJECTED",
            "ENTRY_REVIEW": "ENTRY_REVIEW_FAILED_BEFORE_WRITE",
            "ENTRY_PLACE": "ENTRY_PLACE_AMBIGUOUS",
            "ENTRY_RECONCILIATION": "RECONCILIATION_FAILED",
            "STOP_PROTECTION": "STOP_PROTECTION_FAILED",
            "CANCEL_RECONCILIATION": "CANCEL_RECONCILIATION_FAILED",
            "FLATTENING": "FLATTEN_FAILED",
            "FINAL_FLAT_VERIFICATION": "FINAL_FLAT_VERIFICATION_FAILED",
            "EVIDENCE_INITIALIZATION": "EVIDENCE_PERSISTENCE_FAILED",
            "PREFLIGHT_RECONCILIATION": "RECONCILIATION_FAILED",
        }
        return mapping.get(self._stage, "INTERNAL_INVARIANT_FAILURE")

    async def _fail_closed_before_write(
        self, category: str, error: BaseException, action: str
    ) -> None:
        self.machine.record_failure_diagnostic(
            category=category, stage=self._stage, error=error,
            operator_action=action,
        )
        if self.machine.state is not State.FAILED_CLOSED:
            self.machine.failed_closed(category)
        else:
            self.machine.evidence.completion_or_failure_reason = category
            self.machine.evidence_store.persist(self.machine.evidence)
        await _maybe_await(self.emergency_handoff(EmergencyHandoff(
            current_state=State.FAILED_CLOSED.value, reason_code=category,
            position_may_exist=False, stop_may_remain_executable=False,
            fixed_one_share=True, expected_operator_action=action,
        )))

    async def _authorization(self, stage: str) -> RuntimeAuthorization:
        value = await _maybe_await(self.authorization_provider(stage))
        if not isinstance(value, RuntimeAuthorization):
            await self._handoff("AUTHORIZATION_PROVIDER_INVALID", position=stage == "flatten",
                                stop=stage == "flatten",
                                action="Keep control in the Robinhood UI and stop the run.")
        return value

    def _aapl_position(self, rows: tuple[CommissioningPositionRecord, ...]) -> Decimal:
        matches = [row for row in rows if row.symbol == "AAPL"]
        if len(matches) > 1:
            raise CommissioningError("multiple AAPL position records")
        return matches[0].quantity if matches else Decimal("0")

    def _exact_order(self, rows: tuple[CommissioningOrderRecord, ...],
                     order_id: str) -> CommissioningOrderRecord:
        matches = [row for row in rows if row.server_order_id == order_id]
        if len(matches) != 1:
            raise CommissioningError("transient order identifier did not match exactly once")
        return matches[0]

    @staticmethod
    def _related_nonterminal(rows: tuple[CommissioningOrderRecord, ...]) -> int:
        return sum(1 for row in rows if row.symbol == "AAPL" and classify_order_state(row.state)
                   not in {OrderStateClass.TERMINAL_CANCELLED,
                           OrderStateClass.TERMINAL_FILLED,
                           OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
                           OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED})

    async def _require_before_flat_deadline(
        self, *, position: bool, stop: bool, action: str
    ) -> None:
        try:
            expired = verified_flat_deadline_reached(self.now_eastern(), self.time_policy)
        except EntryWindowError:
            await self._handoff(
                "FINAL_FLAT_VERIFICATION_FAILED", position=position, stop=stop,
                action="Take immediate manual Robinhood control.",
            )
        if expired:
            await self._handoff(
                "VERIFIED_FLAT_DEADLINE_REACHED", position=position, stop=stop,
                action=action,
            )

    async def _read_orders(self) -> tuple[CommissioningOrderRecord, ...]:
        self.machine.record_tool_call("get_equity_orders")
        return await self.adapter.get_equity_orders()

    async def _read_positions(self) -> tuple[CommissioningPositionRecord, ...]:
        self.machine.record_tool_call("get_equity_positions")
        return await self.adapter.get_equity_positions()

    async def _review(self, role: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.machine.record_tool_call("review_equity_order")
        return await self.adapter.review_equity_order(arguments, role=role)

    async def _place(self, role: str, arguments: Mapping[str, Any]) -> Any:
        if role != "entry":
            await self._require_before_flat_deadline(
                position=True, stop=role == "exit",
                action="Take immediate manual Robinhood control; do not submit another write.",
            )
        self.machine.record_tool_call("place_equity_order")
        self.machine.persist_write_boundary(role, "before")
        self._last_write = role
        try:
            result = await self.adapter.place_equity_order(arguments, role=role)
        except AmbiguousWriteError:
            if role in {"entry", "stop"}:
                raise
            disposition = WriteDisposition.AMBIGUOUS
            if role == "stop":
                self.machine.stop_submitted(disposition)
            else:
                self.machine.exit_submitted(disposition, latest_position_quantity=Decimal("1"))
            await self._handoff(f"AMBIGUOUS_{role.upper()}_SUBMISSION",
                                position=True, stop=role in {"stop", "exit"},
                                action="Inspect the exact order and position in the Robinhood UI; do not retry.")
        except BrokerRejectedError:
            if role == "entry":
                self.machine.entry_submitted(WriteDisposition.REJECTED)
            elif role == "stop":
                self.machine.stop_submitted(WriteDisposition.REJECTED)
            else:
                self.machine.exit_submitted(WriteDisposition.REJECTED,
                                            latest_position_quantity=Decimal("1"))
            await self._handoff(f"{role.upper()}_REJECTED", position=role != "entry",
                                stop=role in {"stop", "exit"},
                                action="Inspect the order and position; do not retry automatically.")
        self.machine.persist_write_boundary(role, "after")
        if role == "entry":
            self.machine.entry_submitted(WriteDisposition.ACCEPTED)
        elif role == "stop":
            self.machine.stop_submitted(WriteDisposition.ACCEPTED)
        else:
            self.machine.exit_submitted(WriteDisposition.ACCEPTED,
                                        latest_position_quantity=Decimal("1"))
        return result

    @staticmethod
    def _entry_identity_matches(order: CommissioningOrderRecord) -> bool:
        return (
            order.symbol == "AAPL"
            and order.side == "buy"
            and order.order_type == "market"
            and order.requested_quantity == Decimal("1")
            and order.time_in_force == "gfd"
            and order.market_hours == "regular_hours"
        )

    @staticmethod
    def _stop_place_category(state: str) -> str:
        normalized = state.strip().lower()
        if normalized in {"new", "unconfirmed"}:
            return "STOP_PLACE_ACCEPTED_PENDING"
        if normalized in {"queued", "confirmed", "placed", "open", "accepted"}:
            return "STOP_PLACE_ACCEPTED_OPEN"
        if normalized == "triggered":
            return "STOP_PLACE_ACCEPTED_TRIGGERED"
        if normalized == "filled":
            return "STOP_PLACE_ACCEPTED_FILLED"
        if normalized == "partially_filled":
            return "STOP_PARTIALLY_FILLED"
        if normalized == "pending_cancelled":
            return "STOP_CANCEL_PENDING"
        if normalized == "cancelled":
            return "STOP_CANCEL_CONFIRMED"
        return "STOP_RECONCILIATION_INCONSISTENT"

    @staticmethod
    def _is_terminal_order(order: CommissioningOrderRecord) -> bool:
        return classify_order_state(order.state) in {
            OrderStateClass.TERMINAL_CANCELLED,
            OrderStateClass.TERMINAL_FILLED,
            OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
            OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
        }

    async def _fail_ambiguous_stop_reconciliation(
        self,
        category: str,
        *,
        normalized_order_state: str | None = None,
        candidate_count: int = 0,
    ) -> None:
        self.machine.record_stop_reconciliation(
            category,
            normalized_order_state=normalized_order_state,
            attribution_unique=False,
            candidate_count=candidate_count,
        )
        self.machine.stop_submitted(WriteDisposition.AMBIGUOUS)
        await self._handoff(
            category,
            position=True,
            stop=True,
            action=(
                "Inspect the exact position and sell orders in the Robinhood UI; "
                "do not retry stop placement or submit an exit."
            ),
        )

    async def _reconcile_ambiguous_stop(
        self,
        baseline_order_ids: frozenset[str],
        expected_stop_price: Decimal,
        ambiguity_category: str,
    ) -> tuple[str, bool]:
        self._stage = "STOP_PROTECTION"
        if ambiguity_category not in {
            "STOP_PLACE_RESPONSE_MALFORMED",
            "STOP_PLACE_TRANSPORT_AMBIGUOUS",
        }:
            ambiguity_category = "STOP_PLACE_TRANSPORT_AMBIGUOUS"
        self.machine.record_stop_reconciliation(ambiguity_category)
        started = self.monotonic()
        inconsistent_seen = False
        while True:
            try:
                self.machine.record_stop_reconciliation_read("orders")
                orders = await self._read_orders()
                self.machine.record_stop_reconciliation_read("positions")
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                orders = ()
                position = Decimal("-1")
                inconsistent_seen = True

            new_orders = [
                row for row in orders
                if row.server_order_id not in baseline_order_ids
            ]
            if len(new_orders) > 1:
                await self._fail_ambiguous_stop_reconciliation(
                    "STOP_RECONCILIATION_MULTIPLE_MATCHES",
                    candidate_count=len(new_orders),
                )
            if len(new_orders) == 1:
                order = new_orders[0]
                normalized = order.state.strip().lower()
                if order.symbol != "AAPL":
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_RECONCILIATION_WRONG_SYMBOL",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                if order.side != "sell":
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_RECONCILIATION_WRONG_SIDE",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                if (
                    order.requested_quantity != Decimal("1")
                    or order.cumulative_quantity not in {Decimal("0"), Decimal("1")}
                    or order.remaining_quantity not in {Decimal("0"), Decimal("1")}
                    or order.cumulative_quantity + order.remaining_quantity
                    != Decimal("1")
                ):
                    category = (
                        "STOP_PARTIALLY_FILLED"
                        if normalized in {
                            "partially_filled",
                            "partially_filled_rest_cancelled",
                        }
                        or Decimal("0") < order.cumulative_quantity < Decimal("1")
                        else "STOP_RECONCILIATION_WRONG_QUANTITY"
                    )
                    await self._fail_ambiguous_stop_reconciliation(
                        category,
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                if (
                    order.order_type not in {"stop", "stop_market"}
                    or order.stop_price != expected_stop_price
                    or order.time_in_force != "gtc"
                    or order.market_hours != "regular_hours"
                ):
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_RECONCILIATION_CONFLICTING_ORDER",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )

                classification = classify_order_state(order.state)
                if (
                    classification is OrderStateClass.ACTIVE_OR_WORKING
                    and normalized != "partially_filled"
                    and order.cumulative_quantity == Decimal("0")
                    and order.remaining_quantity == Decimal("1")
                    and position == Decimal("1")
                ):
                    self.machine.record_stop_reconciliation(
                        self._stop_place_category(order.state),
                        normalized_order_state=normalized,
                        attribution_unique=True,
                        candidate_count=1,
                    )
                    self.machine.record_stop_reconciliation(
                        "STOP_RECONCILED_BY_ORDER",
                        normalized_order_state=normalized,
                        attribution_unique=True,
                        candidate_count=1,
                    )
                    self.machine.persist_write_boundary("stop", "after")
                    self.machine.stop_submitted(WriteDisposition.ACCEPTED)
                    return order.server_order_id, False
                if classification is OrderStateClass.TERMINAL_FILLED:
                    if (
                        order.cumulative_quantity == Decimal("1")
                        and order.remaining_quantity == Decimal("0")
                        and position == Decimal("0")
                    ):
                        self.machine.record_stop_reconciliation(
                            "STOP_PLACE_ACCEPTED_FILLED",
                            normalized_order_state=normalized,
                            attribution_unique=True,
                            candidate_count=1,
                        )
                        self.machine.record_stop_reconciliation(
                            "STOP_RECONCILED_BY_POSITION_AND_ORDER",
                            normalized_order_state=normalized,
                            attribution_unique=True,
                            candidate_count=1,
                        )
                        self.machine.record_stop_reconciliation(
                            "STOP_ALREADY_FILLED_POSITION_FLAT",
                            normalized_order_state=normalized,
                            attribution_unique=True,
                            candidate_count=1,
                        )
                        self.machine.persist_write_boundary("stop", "after")
                        self.machine.stop_submitted(WriteDisposition.ACCEPTED)
                        self.machine.stop_filled_before_protection(
                            cumulative_quantity=order.cumulative_quantity,
                            remaining_quantity=order.remaining_quantity,
                            position_quantity=position,
                        )
                        return order.server_order_id, True
                    inconsistent_seen = True
                elif classification is OrderStateClass.CANCELLATION_PENDING:
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_CANCEL_PENDING",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                elif classification is OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED:
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_PARTIALLY_FILLED",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                elif classification is OrderStateClass.TERMINAL_CANCELLED:
                    await self._fail_ambiguous_stop_reconciliation(
                        "STOP_CANCEL_CONFIRMED",
                        normalized_order_state=normalized,
                        candidate_count=1,
                    )
                else:
                    inconsistent_seen = True
            elif position != Decimal("1"):
                inconsistent_seen = True

            elapsed = self.monotonic() - started
            if elapsed >= STOP_OBSERVATION_DEADLINE_SECONDS:
                terminal_category = (
                    "STOP_RECONCILIATION_INCONSISTENT"
                    if inconsistent_seen
                    else "STOP_RECONCILIATION_NOT_FOUND"
                )
                self.machine.record_stop_reconciliation(
                    terminal_category,
                    attribution_unique=False,
                    candidate_count=len(new_orders),
                )
                await self._fail_ambiguous_stop_reconciliation(
                    "STOP_RECONCILIATION_TIMEOUT",
                    candidate_count=len(new_orders),
                )
            await self.sleep(POLL_SECONDS)

    async def _reconcile_ambiguous_entry(
        self,
        baseline_order_ids: frozenset[str],
        ambiguity_category: str,
    ) -> str:
        self._stage = "ENTRY_RECONCILIATION"
        if ambiguity_category not in {
            "ENTRY_PLACE_RESPONSE_MALFORMED",
            "ENTRY_PLACE_TRANSPORT_AMBIGUOUS",
        }:
            ambiguity_category = "ENTRY_PLACE_TRANSPORT_AMBIGUOUS"
        self.machine.record_entry_reconciliation(ambiguity_category)
        started = self.monotonic()
        position_seen = False
        inconsistent_seen = False
        while True:
            self.machine.record_entry_reconciliation_poll()
            try:
                orders = await self._read_orders()
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                orders = ()
                position = Decimal("-1")
                inconsistent_seen = True
            candidates = [
                row for row in orders
                if row.server_order_id not in baseline_order_ids
                and self._entry_identity_matches(row)
            ]
            if len(candidates) > 1:
                self.machine.record_entry_reconciliation(
                    "ENTRY_RECONCILIATION_MULTIPLE_MATCHES",
                    attribution_unique=False,
                    candidate_count=len(candidates),
                )
                self.machine.entry_submitted(WriteDisposition.AMBIGUOUS)
                await self._handoff(
                    "ENTRY_RECONCILIATION_MULTIPLE_MATCHES",
                    position=True,
                    stop=False,
                    action="Inspect the entry and position; do not submit another buy.",
                )
            if len(candidates) == 1:
                order = candidates[0]
                classification = classify_order_state(order.state)
                quantity_consistent = (
                    order.cumulative_quantity in {Decimal("0"), Decimal("1")}
                    and order.remaining_quantity in {Decimal("0"), Decimal("1")}
                    and order.cumulative_quantity + order.remaining_quantity
                    == Decimal("1")
                )
                active_consistent = (
                    classification is OrderStateClass.ACTIVE_OR_WORKING
                    and order.cumulative_quantity == Decimal("0")
                    and order.remaining_quantity == Decimal("1")
                    and position == Decimal("0")
                )
                filled_consistent = (
                    classification is OrderStateClass.TERMINAL_FILLED
                    and order.cumulative_quantity == Decimal("1")
                    and order.remaining_quantity == Decimal("0")
                    and order.average_fill_price is not None
                    and position == Decimal("1")
                )
                if quantity_consistent and (active_consistent or filled_consistent):
                    self.machine.record_entry_reconciliation(
                        "ENTRY_RECONCILED_BY_ORDER",
                        normalized_order_state=order.state.lower(),
                        attribution_unique=True,
                        candidate_count=1,
                    )
                    self.machine.persist_write_boundary("entry", "after")
                    self.machine.entry_submitted(WriteDisposition.ACCEPTED)
                    return order.server_order_id
                inconsistent_seen = True
            elif position == Decimal("1"):
                if not position_seen:
                    self.machine.record_entry_reconciliation(
                        "ENTRY_RECONCILED_BY_POSITION",
                        attribution_unique=False,
                        candidate_count=0,
                    )
                    position_seen = True
            elif position != Decimal("0"):
                inconsistent_seen = True

            elapsed = self.monotonic() - started
            if elapsed >= ENTRY_FILL_DEADLINE_SECONDS:
                terminal_category = (
                    "ENTRY_RECONCILIATION_INCONSISTENT"
                    if inconsistent_seen or position_seen
                    else "ENTRY_RECONCILIATION_NOT_FOUND"
                )
                self.machine.record_entry_reconciliation(
                    terminal_category,
                    attribution_unique=False,
                    candidate_count=len(candidates),
                )
                self.machine.record_entry_reconciliation(
                    "ENTRY_RECONCILIATION_TIMEOUT",
                    attribution_unique=False,
                    candidate_count=len(candidates),
                )
                self.machine.entry_submitted(WriteDisposition.AMBIGUOUS)
                await self._handoff(
                    "ENTRY_RECONCILIATION_TIMEOUT",
                    position=True,
                    stop=False,
                    action="Inspect the entry and position; do not submit another buy.",
                )
            await self.sleep(POLL_SECONDS)

    def _record_cancel_call_outcome(
        self,
        started: float,
        *,
        returned: bool,
        classification: str,
        response_shape: str,
        accepted_present: str,
        accepted_value: str,
    ) -> None:
        """Persist allowlisted cancel-call observations; no raw values."""
        self.machine.record_cancel_diagnostic(
            "cancel_call_returned", "true" if returned else "false"
        )
        self.machine.record_cancel_diagnostic(
            "cancel_elapsed_duration_bucket",
            coarse_duration_bucket(self.monotonic() - started),
        )
        self.machine.record_cancel_diagnostic(
            "cancel_transport_classification", classification
        )
        self.machine.record_cancel_diagnostic(
            "cancel_response_shape_classification", response_shape
        )
        self.machine.record_cancel_diagnostic(
            "cancel_response_contained_accepted", accepted_present
        )
        self.machine.record_cancel_diagnostic(
            "cancel_accepted_value", accepted_value
        )
        self.machine.record_cancel_diagnostic(
            "cancel_timeout_layer",
            cancel_timeout_layer(returned=returned, classification=classification),
        )
        self.machine.record_cancel_diagnostic(
            "session_healthy_after_cancel", "true" if returned else "unknown"
        )

    async def _cancel(self, stop_id: str) -> None:
        await self._require_before_flat_deadline(
            position=True, stop=True,
            action="Take immediate manual Robinhood control; reconcile the stop and position.",
        )
        self.machine.record_tool_call("cancel_equity_order")
        self.machine.persist_write_boundary("cancel", "before")
        self._last_write = "cancel"
        self.machine.record_cancel_diagnostic("cancel_call_started", "true")
        cancel_started = self.monotonic()
        try:
            result = await self.adapter.cancel_equity_order({"order_id": stop_id})
        except AmbiguousWriteError as error:
            self._record_cancel_call_outcome(
                cancel_started,
                returned=getattr(error, "call_returned", False),
                classification=getattr(
                    error, "transport_classification", "UNKNOWN_EXCEPTION"
                ),
                response_shape=getattr(error, "response_shape", "unknown"),
                accepted_present="unknown",
                accepted_value="unknown",
            )
            category = error.category
            if category not in {
                "STOP_CANCEL_TRANSPORT_AMBIGUOUS",
                "STOP_CANCEL_RESPONSE_MALFORMED",
            }:
                category = "STOP_CANCEL_TRANSPORT_AMBIGUOUS"
            self.machine.stop_cancel_requested(WriteDisposition.AMBIGUOUS)
            self.machine.record_cancel_reconciliation(category)
            return
        except BrokerRejectedError as error:
            self._record_cancel_call_outcome(
                cancel_started, returned=True,
                classification=sanitized_exception_type(error),
                response_shape="documented_envelope",
                accepted_present="unknown", accepted_value="unknown",
            )
            self.machine.stop_cancel_requested(WriteDisposition.REJECTED)
            await self._handoff(
                "STOP_CANCEL_REJECTED", position=True, stop=True,
                action="Retain manual control; inspect the executable stop.",
            )
        accepted = bool(result.request_was_accepted)
        self._record_cancel_call_outcome(
            cancel_started, returned=True, classification="NONE",
            response_shape="documented_envelope",
            accepted_present="true",
            accepted_value="true" if accepted else "false",
        )
        self.machine.persist_write_boundary("cancel", "after")
        disposition = (
            WriteDisposition.ACCEPTED
            if accepted
            else WriteDisposition.REJECTED
        )
        self.machine.stop_cancel_requested(disposition)
        if disposition is WriteDisposition.REJECTED:
            await self._handoff(
                "STOP_CANCEL_REJECTED", position=True, stop=True,
                action="Retain manual control; inspect the executable stop.",
            )
        self.machine.record_cancel_reconciliation(
            "STOP_CANCEL_REQUEST_ACCEPTED"
        )

    async def run(self) -> OrchestrationResult:
        if self.evidence_store.partial.exists():
            evidence, last_state = self.evidence_store.recover_interrupted()
            self.machine.evidence = evidence
            self.machine.state = State.OPERATOR_REQUIRED
            try:
                await self._handoff("INTERRUPTED_RUN_REQUIRES_OPERATOR", position=True,
                                    stop=True,
                                    action=f"Reconcile manually from last safe state {last_state}; never repeat a write.")
            except OperatorRequired:
                return OrchestrationResult(State.OPERATOR_REQUIRED,
                    self.evidence_store.partial / "commissioning_evidence.json")
        self._stage = "EVIDENCE_INITIALIZATION"
        try:
            self.machine.start()
        except Exception as error:
            raise CommissioningDiagnosticError(
                "EVIDENCE_PERSISTENCE_FAILED", self._stage,
                type(error).__name__,
            ) from None
        try:
            self._stage = "PREFLIGHT_RECONCILIATION"
            self.machine.record_tool_call("get_accounts")
            account = await self.adapter.get_accounts()
            positions = await self._read_positions()
            orders = await self._read_orders()
            baseline_order_ids = frozenset(
                row.server_order_id for row in orders
            )
            self.machine.preflight_passed(
                strategy_enabled=self.strategy_enabled,
                commissioned_accounts=account.eligible_count if account.identity_match else 0,
                aapl_position_quantity=self._aapl_position(positions),
                related_open_orders=self._related_nonterminal(orders),
                regular_market_hours=self.regular_market_hours,
            )
            self._stage = "ENTRY_REVIEW"
            entry_payload = entry_arguments(self.account_number)
            await self._review("entry", entry_payload)
            self.machine.entry_reviewed(accepted=True)
            self._stage = "ENTRY_WINDOW_VALIDATION"
            validate_commissioning_entry_time(self.now_eastern(), self.time_policy)
            self._stage = "ENTRY_AUTHORIZATION_INPUT"
            entry_authorization = await self._authorization("entry")
            self._stage = "ENTRY_AUTHORIZATION_CONSUMPTION"
            entry_authorization_time = self.now_eastern()
            validate_commissioning_entry_time(entry_authorization_time, self.time_policy)
            self.machine.authorize_entry(
                entry_authorization,
                now=entry_authorization_time,
            )
            self._stage = "ENTRY_WINDOW_VALIDATION"
            validate_commissioning_entry_time(self.now_eastern(), self.time_policy)
            self._stage = "ENTRY_PLACE"
            try:
                entry_result = await self._place("entry", entry_payload)
            except AmbiguousWriteError as error:
                entry_id = await self._reconcile_ambiguous_entry(
                    baseline_order_ids, error.category
                )
            else:
                entry_class = classify_order_state(entry_result.state)
                if entry_class is OrderStateClass.ACTIVE_OR_WORKING:
                    category = "ENTRY_PLACE_ACCEPTED_PENDING"
                elif entry_class is OrderStateClass.TERMINAL_FILLED:
                    category = "ENTRY_PLACE_ACCEPTED_FILLED"
                else:
                    await self._handoff(
                        "ENTRY_RECONCILIATION_INCONSISTENT",
                        position=True,
                        stop=False,
                        action="Inspect the entry and position; do not submit another buy.",
                    )
                self.machine.record_entry_reconciliation(
                    category,
                    normalized_order_state=entry_result.state.lower(),
                    attribution_unique=True,
                    candidate_count=1,
                )
                entry_id = entry_result.server_order_id
            self._stage = "ENTRY_RECONCILIATION"
            await self._reconcile_entry(entry_id)
            assert self.machine.expected_stop_price is not None
            stop_payload = stop_arguments(self.account_number,
                                          self.machine.expected_stop_price)

            self._stage = "STOP_PROTECTION"
            stop_baseline_orders = await self._read_orders()
            stop_baseline_position = self._aapl_position(
                await self._read_positions()
            )
            competing_baseline_sells = sum(
                1 for row in stop_baseline_orders
                if row.symbol == "AAPL"
                and row.side == "sell"
                and not self._is_terminal_order(row)
            )
            if stop_baseline_position != Decimal("1"):
                await self._handoff(
                    "STOP_RECONCILIATION_INCONSISTENT",
                    position=True,
                    stop=False,
                    action="Inspect the position before submitting protection.",
                )
            if competing_baseline_sells:
                await self._handoff(
                    "STOP_RECONCILIATION_CONFLICTING_ORDER",
                    position=True,
                    stop=True,
                    action=(
                        "Do not place another sell while an existing sell order "
                        "may execute."
                    ),
                )
            stop_baseline_order_ids = frozenset(
                row.server_order_id for row in stop_baseline_orders
            )
            stop_review_started = self.monotonic()
            await self._review("stop", stop_payload)
            self.machine.stop_reviewed(
                accepted=True,
                construction_seconds=self.monotonic() - stop_review_started,
            )
            try:
                stop_result = await self._place("stop", stop_payload)
            except AmbiguousWriteError as error:
                stop_id, stop_filled = await self._reconcile_ambiguous_stop(
                    stop_baseline_order_ids,
                    self.machine.expected_stop_price,
                    error.category,
                )
            else:
                self.machine.record_stop_reconciliation(
                    self._stop_place_category(stop_result.state),
                    normalized_order_state=stop_result.state.strip().lower(),
                    attribution_unique=True,
                    candidate_count=1,
                )
                stop_id = stop_result.server_order_id
                stop_filled = False
            if not stop_filled:
                stop_filled = await self._reconcile_stop(stop_id)
            if stop_filled:
                return await self._finalize_flat()
            self._stage = "FLATTEN_AUTHORIZATION_INPUT"
            flatten_authorization = await self._authorization("flatten")
            self._stage = "FLATTEN_AUTHORIZATION_CONSUMPTION"
            self.machine.authorize_flatten(
                flatten_authorization, now=self.now_eastern()
            )
            self._stage = "CANCEL_RECONCILIATION"
            cancel_baseline_orders = await self._read_orders()
            cancel_baseline_position = self._aapl_position(
                await self._read_positions()
            )
            cancel_baseline_stop = self._exact_order(
                cancel_baseline_orders, stop_id
            )
            cancel_baseline_class = classify_order_state(
                cancel_baseline_stop.state
            )
            if cancel_baseline_position != Decimal("1"):
                await self._handoff(
                    "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                    action="Inspect the stop and position before canceling.",
                )
            if cancel_baseline_class is not OrderStateClass.ACTIVE_OR_WORKING:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_ALREADY_TERMINAL",
                    normalized_order_state=cancel_baseline_stop.state.strip().lower(),
                    attribution_unique=True,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_ALREADY_TERMINAL", position=True, stop=True,
                    action="Inspect the already non-working stop before any exit.",
                )
            cancel_baseline_order_ids = frozenset(
                row.server_order_id for row in cancel_baseline_orders
            )
            await self._cancel(stop_id)
            await self._reconcile_cancel(
                stop_id,
                baseline_order_ids=cancel_baseline_order_ids,
                baseline_position=cancel_baseline_position,
            )
            self._stage = "FLATTENING"
            return await self._finish_after_cancel(stop_id)
        except OperatorRequired:
            return OrchestrationResult(State.OPERATOR_REQUIRED,
                self.evidence_store.partial / "commissioning_evidence.json")
        except Exception as error:
            position = self._last_write is not None
            stop = self._last_write in {"stop", "cancel", "exit"}
            category = (
                "ENTRY_WINDOW_REJECTED"
                if isinstance(error, EntryWindowError)
                else error.category
                if isinstance(error, AuthorizationInputError)
                else self._failure_category()
            )
            action = (
                "Assume manual control and reconcile; never repeat a write automatically."
                if position else
                "Stop the run; no broker write boundary was reached."
            )
            try:
                if position:
                    self.machine.record_failure_diagnostic(
                        category=category, stage=self._stage, error=error,
                        operator_action=action,
                    )
                    await self._handoff(category, position=True, stop=stop,
                                        action=action)
                else:
                    await self._fail_closed_before_write(
                        category, error, action
                    )
                    return OrchestrationResult(
                        State.FAILED_CLOSED,
                        self.evidence_store.partial / "commissioning_evidence.json",
                    )
            except OperatorRequired:
                return OrchestrationResult(State.OPERATOR_REQUIRED,
                    self.evidence_store.partial / "commissioning_evidence.json")

    async def _reconcile_entry(self, entry_id: str) -> None:
        self._stage = "ENTRY_RECONCILIATION"
        started = self.monotonic()
        while True:
            try:
                order = self._exact_order(await self._read_orders(), entry_id)
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                await self._handoff("ENTRY_ORDER_RECONCILIATION_CONFLICT",
                                    position=True, stop=False,
                                    action="Inspect the entry and position; do not submit another buy.")
            elapsed = self.monotonic() - started
            self.machine.entry_observed(
                order=EntryFillObservation(
                    commissioned_account=True, symbol=order.symbol, side=order.side,
                    order_type=order.order_type,
                    requested_quantity=order.requested_quantity,
                    cumulative_quantity=order.cumulative_quantity,
                    remaining_quantity=order.remaining_quantity, state=order.state,
                    average_fill_price=order.average_fill_price,
                ),
                position_quantity=position,
                deadline_exceeded=elapsed >= ENTRY_FILL_DEADLINE_SECONDS,
                detection_seconds=elapsed,
            )
            if self.machine.state is State.ENTRY_FILLED:
                return
            if self.machine.state is State.OPERATOR_REQUIRED:
                await self._handoff(self.machine.evidence.completion_or_failure_reason,
                                    position=True, stop=False,
                                    action="Inspect the entry and position; do not retry placement.")
            if mandatory_risk_off_due(self.now_eastern(), self.time_policy):
                await self._handoff(
                    "MANDATORY_RISK_OFF_ENTRY_UNRESOLVED", position=True, stop=False,
                    action="Take immediate manual control; cancel or flatten safely.",
                )
            if elapsed >= ENTRY_FILL_DEADLINE_SECONDS:
                await self._handoff("ENTRY_FILL_DEADLINE_EXCEEDED", position=True,
                                    stop=False,
                                    action="Inspect and control the entry manually; do not retry.")
            await self.sleep(POLL_SECONDS)

    async def _reconcile_stop(self, stop_id: str) -> bool:
        self._stage = "STOP_PROTECTION"
        started = self.monotonic()
        while True:
            try:
                orders = await self._read_orders()
                order = self._exact_order(orders, stop_id)
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                await self._handoff("STOP_ORDER_RECONCILIATION_CONFLICT",
                                    position=True, stop=True,
                                    action="Inspect the position and protective stop manually.")
            elapsed = self.monotonic() - started
            normalized = order.state.strip().lower()
            classification = classify_order_state(order.state)
            if normalized == "partially_filled" or classification is (
                OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED
            ):
                self.machine.record_stop_reconciliation(
                    "STOP_PARTIALLY_FILLED",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_PARTIALLY_FILLED",
                    position=True,
                    stop=True,
                    action=(
                        "Take manual control; do not cancel or sell until the "
                        "partial stop state and position are reconciled."
                    ),
                )
            if classification is OrderStateClass.CANCELLATION_PENDING:
                self.machine.record_stop_reconciliation(
                    "STOP_CANCEL_PENDING",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_PENDING",
                    position=True,
                    stop=True,
                    action="Reconcile the pending cancellation manually; do not sell.",
                )
            if classification is OrderStateClass.TERMINAL_FILLED:
                if (
                    order.requested_quantity == Decimal("1")
                    and order.cumulative_quantity == Decimal("1")
                    and order.remaining_quantity == Decimal("0")
                    and position == Decimal("0")
                ):
                    self.machine.record_stop_reconciliation(
                        "STOP_ALREADY_FILLED_POSITION_FLAT",
                        normalized_order_state=normalized,
                        attribution_unique=True,
                        candidate_count=1,
                    )
                    self.machine.stop_filled_before_protection(
                        cumulative_quantity=order.cumulative_quantity,
                        remaining_quantity=order.remaining_quantity,
                        position_quantity=position,
                    )
                    return True
                self.machine.record_stop_reconciliation(
                    "STOP_RECONCILIATION_INCONSISTENT",
                    normalized_order_state=normalized,
                    attribution_unique=False,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_RECONCILIATION_INCONSISTENT",
                    position=True,
                    stop=True,
                    action="Reconcile the filled stop and position manually.",
                )
            competitors = sum(
                1 for row in orders
                if row.server_order_id != stop_id and row.symbol == "AAPL"
                and row.side == "sell"
                and not self._is_terminal_order(row)
            )
            result = self.machine.protection_observed(
                order=OrderObservation(
                    commissioned_account=True, symbol=order.symbol, side=order.side,
                    order_type=order.order_type,
                    requested_quantity=order.requested_quantity,
                    cumulative_quantity=order.cumulative_quantity,
                    remaining_quantity=order.remaining_quantity,
                    stop_price=order.stop_price, state=order.state,
                ),
                position_quantity=position, competing_sell_orders=competitors,
                observation_seconds=elapsed, unprotected_seconds=elapsed,
            )
            if result.provisional:
                return False
            if mandatory_risk_off_due(self.now_eastern(), self.time_policy):
                await self._handoff(
                    "MANDATORY_RISK_OFF_PROTECTION_UNRESOLVED",
                    position=True, stop=True,
                    action="Take immediate manual control; reconcile protection and flatten.",
                )
            await self._handoff(self.machine.evidence.completion_or_failure_reason,
                                position=True, stop=True,
                                action="Inspect and establish protection or flatten manually.")

    async def _reconcile_cancel(
        self,
        stop_id: str,
        *,
        baseline_order_ids: frozenset[str],
        baseline_position: Decimal,
    ) -> None:
        self._stage = "CANCEL_RECONCILIATION"
        if stop_id not in baseline_order_ids or baseline_position != Decimal("1"):
            self.machine.record_cancel_reconciliation(
                "STOP_CANCEL_INCONSISTENT",
                attribution_unique=False,
                candidate_count=0,
            )
            await self._handoff(
                "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                action="Inspect the stop and position; do not submit an exit.",
            )
        started = self.monotonic()
        while True:
            self.machine.record_cancel_reconciliation_read("orders")
            try:
                orders = await self._read_orders()
            except CommissioningError:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_INCONSISTENT",
                    attribution_unique=False,
                    candidate_count=0,
                )
                await self._handoff(
                    "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                    action="Inspect stop cancellation and position manually.",
                )

            matches = [
                row for row in orders if row.server_order_id == stop_id
            ]
            new_related_sells = [
                row for row in orders
                if row.server_order_id not in baseline_order_ids
                and row.symbol == "AAPL"
                and row.side == "sell"
                and not self._is_terminal_order(row)
            ]
            if len(matches) > 1 or new_related_sells:
                candidate_count = len(matches) + len(new_related_sells)
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_MULTIPLE_MATCHES",
                    attribution_unique=False,
                    candidate_count=candidate_count,
                )
                await self._handoff(
                    "STOP_CANCEL_MULTIPLE_MATCHES", position=True, stop=True,
                    action="Inspect every related sell order; do not submit an exit.",
                )

            self.machine.record_cancel_reconciliation_read("positions")
            try:
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_INCONSISTENT",
                    attribution_unique=False,
                    candidate_count=len(matches),
                )
                await self._handoff(
                    "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                    action="Inspect stop cancellation and position manually.",
                )

            if not matches:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_NOT_FOUND",
                    attribution_unique=False,
                    candidate_count=0,
                )
                if self.monotonic() - started >= STOP_CANCEL_DEADLINE_SECONDS:
                    self.machine.record_cancel_diagnostic(
                        "cancel_timeout_layer", "reconciliation_deadline"
                    )
                    self.machine.record_cancel_reconciliation(
                        "STOP_CANCEL_TIMEOUT",
                        attribution_unique=False,
                        candidate_count=0,
                    )
                    await self._handoff(
                        "STOP_CANCEL_TIMEOUT", position=True, stop=True,
                        action="Assume the stop may execute; reconcile in the UI.",
                    )
                await self.sleep(POLL_SECONDS)
                continue

            stop = matches[0]
            normalized = stop.state.strip().lower()
            expected_stop_price = self.machine.expected_stop_price
            if (
                stop.symbol != "AAPL"
                or stop.side != "sell"
                or stop.order_type not in {"stop", "stop_market"}
                or stop.requested_quantity != Decimal("1")
                or stop.stop_price != expected_stop_price
                or stop.time_in_force != "gtc"
                or stop.market_hours != "regular_hours"
            ):
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_INCONSISTENT",
                    normalized_order_state=normalized,
                    attribution_unique=False,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                    action="Inspect the conflicting stop record; do not submit an exit.",
                )
            if normalized in {"partially_filled", "partially_filled_rest_cancelled"}:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_PARTIAL_FILL",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_PARTIAL_FILL", position=True, stop=True,
                    action="Reconcile the partial fill manually; do not submit another sell.",
                )

            category = self.machine.stop_cancel_observed(
                stop_state=stop.state,
                cumulative_quantity=stop.cumulative_quantity,
                remaining_quantity=stop.remaining_quantity,
                position_quantity=position,
            )
            if self.machine.state is State.VERIFIED_FLAT:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_RECONCILED_BY_ORDER",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                return
            if self.machine.stop_cancel_resolved:
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_RECONCILED_BY_ORDER",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                return
            if self.machine.state is State.OPERATOR_REQUIRED:
                await self._handoff(
                    category, position=True, stop=True,
                    action="Inspect stop cancellation and position manually.",
                )
            if position != Decimal("1"):
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_INCONSISTENT",
                    normalized_order_state=normalized,
                    attribution_unique=False,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_INCONSISTENT", position=True, stop=True,
                    action="Inspect the stop and unexpected position; do not sell.",
                )
            await self._require_before_flat_deadline(
                position=True, stop=True,
                action="Take immediate manual control; reconcile cancellation and flatten.",
            )
            if self.monotonic() - started >= STOP_CANCEL_DEADLINE_SECONDS:
                self.machine.record_cancel_diagnostic(
                    "cancel_timeout_layer", "reconciliation_deadline"
                )
                self.machine.record_cancel_reconciliation(
                    "STOP_CANCEL_TIMEOUT",
                    normalized_order_state=normalized,
                    attribution_unique=True,
                    candidate_count=1,
                )
                await self._handoff(
                    "STOP_CANCEL_TIMEOUT", position=True, stop=True,
                    action="Assume the stop may execute; reconcile in the UI.",
                )
            await self.sleep(POLL_SECONDS)

    async def _finish_after_cancel(self, stop_id: str) -> OrchestrationResult:
        self._stage = "FLATTENING"
        if self.machine.state is State.VERIFIED_FLAT:
            return await self._finalize_flat()
        try:
            orders = await self._read_orders()
            stop = self._exact_order(orders, stop_id)
            position = self._aapl_position(await self._read_positions())
        except CommissioningError:
            await self._handoff("PRE_EXIT_RECONCILIATION_CONFLICT", position=True,
                                stop=True,
                                action="Inspect the stop and position; do not submit an exit.")
        stop_class = classify_order_state(stop.state)
        if stop_class not in {OrderStateClass.TERMINAL_CANCELLED,
                              OrderStateClass.TERMINAL_REJECTED_OR_FAILED}:
            await self._handoff("PROTECTIVE_STOP_UNRESOLVED", position=True,
                                stop=True,
                                action="Do not sell while the stop may remain executable.")
        if position == 0:
            self.machine.exit_submitted(WriteDisposition.ACCEPTED,
                                        latest_position_quantity=Decimal("0"))
            return await self._finalize_flat()
        if position != Decimal("1"):
            await self._handoff("PRE_EXIT_POSITION_INVALID", position=True, stop=False,
                                action="Inspect and control the position manually.")
        exit_payload = exit_arguments(self.account_number)
        await self._review("exit", exit_payload)
        exit_result = await self._place("exit", exit_payload)
        await self._reconcile_exit(exit_result.server_order_id)
        return await self._finalize_flat()

    async def _reconcile_exit(self, exit_id: str) -> None:
        self._stage = "FLATTENING"
        started = self.monotonic()
        while True:
            try:
                orders = await self._read_orders()
                order = self._exact_order(orders, exit_id)
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                await self._handoff("EXIT_ORDER_RECONCILIATION_CONFLICT",
                                    position=True, stop=False,
                                    action="Inspect the exit and position; do not submit another sell.")
            classification = classify_order_state(order.state)
            if classification is OrderStateClass.TERMINAL_FILLED:
                if (order.requested_quantity != Decimal("1")
                        or order.cumulative_quantity != Decimal("1")
                        or order.remaining_quantity != Decimal("0")
                        or position != Decimal("0")):
                    await self._handoff("EXIT_FILL_POSITION_MISMATCH", position=True,
                                        stop=False,
                                        action="Inspect the position; do not submit another sell.")
                self.machine.exit_observed(
                    position_quantity=position,
                    related_nonterminal_orders=self._related_nonterminal(orders),
                )
                return
            if classification not in {OrderStateClass.ACTIVE_OR_WORKING,
                                       OrderStateClass.CANCELLATION_PENDING}:
                await self._handoff("EXIT_STATE_UNEXPECTED", position=True, stop=False,
                                    action="Inspect the exit and position; do not retry.")
            if position == 0:
                await self._handoff("EXIT_POSITION_ORDER_MISMATCH", position=False,
                                    stop=False,
                                    action="Verify flatness and remaining orders manually.")
            await self._require_before_flat_deadline(
                position=True, stop=False,
                action="Take immediate manual control; verify the exit and flatness.",
            )
            if self.monotonic() - started >= EXIT_FILL_DEADLINE_SECONDS:
                await self._handoff("EXIT_FILL_DEADLINE_EXCEEDED", position=True,
                                    stop=False,
                                    action="Take manual control; do not submit another exit.")
            await self.sleep(POLL_SECONDS)

    async def _finalize_flat(self) -> OrchestrationResult:
        self._stage = "FINAL_FLAT_VERIFICATION"
        started = self.monotonic()
        while True:
            try:
                orders = await self._read_orders()
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                await self._handoff("FINAL_FLAT_RECONCILIATION_CONFLICT",
                                    position=True, stop=True,
                                    action="Verify position and all AAPL orders manually.")
            related = self._related_nonterminal(orders)
            if position == 0 and related == 0:
                if self.machine.state is not State.VERIFIED_FLAT:
                    await self._handoff("FINAL_STATE_NOT_VERIFIED_FLAT", position=False,
                                        stop=False,
                                        action="Verify flatness manually.")
                path = self.machine.complete(related_nonterminal_orders=0)
                return OrchestrationResult(State.COMPLETE, path)
            await self._require_before_flat_deadline(
                position=position != 0, stop=related != 0,
                action="Take immediate manual Robinhood control and verify flatness.",
            )
            if self.monotonic() - started >= FINAL_FLAT_DEADLINE_SECONDS:
                await self._handoff("FINAL_FLAT_DEADLINE_EXCEEDED",
                                    position=position != 0, stop=related != 0,
                                    action="Verify flatness and cancel remaining orders manually.")
            await self.sleep(POLL_SECONDS)
