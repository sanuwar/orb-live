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
    AtomicEvidenceStore, CommissioningDiagnosticError, CommissioningError,
    CommissioningMachine, EntryWindowError,
    EASTERN, ENTRY_FILL_DEADLINE_SECONDS, EntryFillObservation, OrderObservation,
    RuntimeAuthorization, State, STOP_OBSERVATION_DEADLINE_SECONDS,
    WriteDisposition,
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
        if reason in {"AMBIGUOUS_ENTRY_SUBMISSION", "AMBIGUOUS_ENTRY_PLACE"}:
            return "ENTRY_PLACE_AMBIGUOUS"
        mapping = {
            "ENTRY_AUTHORIZATION_INPUT": "AUTHORIZATION_INPUT_FAILED",
            "ENTRY_AUTHORIZATION_CONSUMPTION": "AUTHORIZATION_INPUT_FAILED",
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
            disposition = WriteDisposition.AMBIGUOUS
            if role == "entry":
                self.machine.entry_submitted(disposition)
            elif role == "stop":
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

    async def _cancel(self, stop_id: str) -> None:
        await self._require_before_flat_deadline(
            position=True, stop=True,
            action="Take immediate manual Robinhood control; reconcile the stop and position.",
        )
        self.machine.record_tool_call("cancel_equity_order")
        self.machine.persist_write_boundary("cancel", "before")
        self._last_write = "cancel"
        try:
            result = await self.adapter.cancel_equity_order({"order_id": stop_id})
        except AmbiguousWriteError:
            self.machine.stop_cancel_requested(WriteDisposition.AMBIGUOUS)
            await self._handoff("AMBIGUOUS_STOP_CANCEL", position=True, stop=True,
                                action="Inspect the stop and position in the Robinhood UI; do not retry cancel.")
        except BrokerRejectedError:
            self.machine.stop_cancel_requested(WriteDisposition.REJECTED)
            await self._handoff("STOP_CANCEL_REJECTED", position=True, stop=True,
                                action="Retain manual control; inspect the executable stop.")
        self.machine.persist_write_boundary("cancel", "after")
        disposition = (WriteDisposition.ACCEPTED if result.request_was_accepted
                       else WriteDisposition.REJECTED)
        self.machine.stop_cancel_requested(disposition)
        if disposition is WriteDisposition.REJECTED:
            await self._handoff("STOP_CANCEL_REJECTED", position=True, stop=True,
                                action="Retain manual control; inspect the executable stop.")

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
            entry_result = await self._place("entry", entry_payload)
            self._stage = "ENTRY_RECONCILIATION"
            await self._reconcile_entry(entry_result.server_order_id)
            assert self.machine.expected_stop_price is not None
            stop_payload = stop_arguments(self.account_number,
                                          self.machine.expected_stop_price)

            self._stage = "STOP_PROTECTION"
            stop_review_started = self.monotonic()
            await self._review("stop", stop_payload)
            self.machine.stop_reviewed(
                accepted=True,
                construction_seconds=self.monotonic() - stop_review_started,
            )
            stop_result = await self._place("stop", stop_payload)
            await self._reconcile_stop(stop_result.server_order_id)
            self._stage = "FLATTENING"
            self.machine.authorize_flatten(await self._authorization("flatten"),
                                           now=self.now_eastern())
            self._stage = "CANCEL_RECONCILIATION"
            await self._cancel(stop_result.server_order_id)
            await self._reconcile_cancel(stop_result.server_order_id)
            self._stage = "FLATTENING"
            return await self._finish_after_cancel(stop_result.server_order_id)
        except OperatorRequired:
            return OrchestrationResult(State.OPERATOR_REQUIRED,
                self.evidence_store.partial / "commissioning_evidence.json")
        except Exception as error:
            position = self._last_write is not None
            stop = self._last_write in {"stop", "cancel", "exit"}
            category = (
                "ENTRY_WINDOW_REJECTED"
                if isinstance(error, EntryWindowError)
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

    async def _reconcile_stop(self, stop_id: str) -> None:
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
            competitors = sum(
                1 for row in orders
                if row.server_order_id != stop_id and row.symbol == "AAPL"
                and row.side == "sell"
                and classify_order_state(row.state) not in {
                    OrderStateClass.TERMINAL_CANCELLED, OrderStateClass.TERMINAL_FILLED,
                    OrderStateClass.TERMINAL_REJECTED_OR_FAILED,
                    OrderStateClass.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
                }
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
                return
            if mandatory_risk_off_due(self.now_eastern(), self.time_policy):
                await self._handoff(
                    "MANDATORY_RISK_OFF_PROTECTION_UNRESOLVED",
                    position=True, stop=True,
                    action="Take immediate manual control; reconcile protection and flatten.",
                )
            await self._handoff(self.machine.evidence.completion_or_failure_reason,
                                position=True, stop=True,
                                action="Inspect and establish protection or flatten manually.")

    async def _reconcile_cancel(self, stop_id: str) -> None:
        self._stage = "CANCEL_RECONCILIATION"
        started = self.monotonic()
        while True:
            try:
                stop = self._exact_order(await self._read_orders(), stop_id)
                position = self._aapl_position(await self._read_positions())
            except CommissioningError:
                await self._handoff("CANCEL_ORDER_RECONCILIATION_CONFLICT",
                                    position=True, stop=True,
                                    action="Inspect stop cancellation and position manually.")
            self.machine.stop_cancel_observed(
                stop_state=stop.state,
                cumulative_quantity=stop.cumulative_quantity,
                remaining_quantity=stop.remaining_quantity,
                position_quantity=position,
            )
            if self.machine.state is State.VERIFIED_FLAT or self.machine.stop_cancel_resolved:
                return
            if self.machine.state is State.OPERATOR_REQUIRED:
                await self._handoff(self.machine.evidence.completion_or_failure_reason,
                                    position=True, stop=True,
                                    action="Inspect stop cancellation and position manually.")
            await self._require_before_flat_deadline(
                position=True, stop=True,
                action="Take immediate manual control; reconcile cancellation and flatten.",
            )
            if self.monotonic() - started >= STOP_CANCEL_DEADLINE_SECONDS:
                await self._handoff("STOP_CANCEL_DEADLINE_EXCEEDED", position=True,
                                    stop=True,
                                    action="Assume the stop may execute; reconcile in the UI.")
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
