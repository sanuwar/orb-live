from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

try:
    from .broker_action_saga import (
        BROKER_WRITE_ACTION_TYPES,
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        SagaActionType,
        condition_satisfied,
        normalized_decimal_text,
    )
    from .entry_lifecycle import (
        EntryLifecycleCoordinator,
    )
    from .exit_order_lifecycle import (
        ExitOrderLifecycleCoordinator,
    )
    from .protective_stop_lifecycle import (
        ProtectiveStopLifecycleCoordinator,
    )
    from .simulated_broker_transport import (
        CrashInjector,
        CrashPoint,
        FakeTransportError,
        TransportTimeout,
    )
except ImportError:
    from broker_action_saga import (
        BROKER_WRITE_ACTION_TYPES,
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        SagaActionType,
        condition_satisfied,
        normalized_decimal_text,
    )
    from entry_lifecycle import (
        EntryLifecycleCoordinator,
    )
    from exit_order_lifecycle import (
        ExitOrderLifecycleCoordinator,
    )
    from protective_stop_lifecycle import (
        ProtectiveStopLifecycleCoordinator,
    )
    from simulated_broker_transport import (
        CrashInjector,
        CrashPoint,
        FakeTransportError,
        TransportTimeout,
    )


class SagaExecutorError(BrokerActionSagaError):
    """Raised when the simulated executor cannot proceed safely."""


@dataclass(frozen=True)
class ExecutorLimits:
    claim_ttl: timedelta = timedelta(seconds=30)
    max_attempts: int = 3
    max_confirmation_polls: int = 5
    max_concurrent_claims: int = 1


_CANCEL_ACTION_TYPES = {
    SagaActionType.CANCEL_ENTRY_REMAINDER,
    SagaActionType.CANCEL_PROTECTIVE_STOP,
    SagaActionType.CANCEL_EXIT,
}


def derive_observation(
    action: SagaActionRecord,
    snapshot,
) -> dict[str, Any]:
    """
    Builds a machine-readable observation from fake
    read-only broker evidence. Pure and deterministic.
    """
    state = snapshot.account_state
    open_orders = {
        order.order_id: order
        for order in state.open_orders
    }
    payload = json.loads(action.payload_json)
    symbol = str(
        payload.get("symbol", "")
    ).upper()

    resulting_id = (
        action.resulting_broker_order_id
    )
    resulting_order = (
        open_orders.get(resulting_id)
        if resulting_id
        else None
    )

    position_quantity = Decimal("0")
    for position in state.positions:
        if position.symbol == symbol:
            position_quantity = Decimal(
                position.quantity
            )
            break

    active_sells = [
        order
        for order in state.open_orders
        if order.symbol == symbol
        and order.side == "sell"
    ]
    active_stops = [
        order
        for order in active_sells
        if order.order_type == "stop_market"
    ]

    observation: dict[str, Any] = {
        "order_present": (
            resulting_order is not None
        ),
        "position_quantity": (
            normalized_decimal_text(
                position_quantity
            )
        ),
        "active_stop_present": bool(active_stops),
        "no_conflicting_sell_order": (
            len(
                [
                    order
                    for order in active_sells
                    if order.order_id
                    != resulting_id
                ]
            )
            == 0
        ),
        "source_order_inactive": (
            action.source_broker_order_id
            not in open_orders
            if action.source_broker_order_id
            else True
        ),
    }
    if resulting_id:
        observation[
            "resulting_broker_order_id"
        ] = resulting_id
    if resulting_order is not None:
        observation.update(
            {
                "order_state": (
                    resulting_order.state
                ),
                "order_symbol": (
                    resulting_order.symbol
                ),
                "order_side": (
                    resulting_order.side
                ),
                "order_type": (
                    resulting_order.order_type
                ),
                "order_quantity": (
                    normalized_decimal_text(
                        Decimal(
                            resulting_order.quantity
                        )
                    )
                ),
                "order_cumulative_quantity": (
                    normalized_decimal_text(
                        Decimal(
                            resulting_order
                            .cumulative_quantity
                        )
                    )
                ),
            }
        )
    if active_stops:
        observation["stop_quantity"] = (
            normalized_decimal_text(
                Decimal(
                    active_stops[0].quantity
                )
            )
        )
    return observation


class SagaExecutor:
    """
    Transport-independent simulated saga executor.

    The executor consumes only durable saga actions,
    never invents or modifies payloads, and can only
    ever be wired to a self-declared simulated
    transport. It has no broker client, no network
    path, no credential access, and no configuration
    or environment hook that could select a live
    transport.
    """

    def __init__(
        self,
        saga_store: BrokerActionSagaStore,
        transport,
        *,
        owner: str,
        limits: ExecutorLimits | None = None,
        crash_injector: CrashInjector | None = (
            None
        ),
    ):
        self._validate_transport(transport)
        if not isinstance(
            saga_store, BrokerActionSagaStore
        ):
            raise SagaExecutorError(
                "A BrokerActionSagaStore is "
                "required."
            )
        owner = owner.strip()
        if not owner:
            raise SagaExecutorError(
                "An executor owner is required."
            )
        self.saga = saga_store
        self.transport = transport
        self.owner = owner
        self.limits = limits or ExecutorLimits()
        self.crash_injector = crash_injector
        self._held_claims: set[str] = set()

    @staticmethod
    def _validate_transport(transport) -> None:
        transport_type = type(transport)
        identity = (
            f"{transport_type.__module__}."
            f"{transport_type.__qualname__}"
        ).lower()
        live_broker_token = "".join(
            ("robin", "hood")
        )
        if live_broker_token in identity:
            raise SagaExecutorError(
                "The simulated executor refuses "
                "any live broker transport."
            )
        if (
            getattr(
                transport,
                "simulated_only",
                False,
            )
            is not True
        ):
            raise SagaExecutorError(
                "The executor accepts only a "
                "transport that declares itself "
                "simulated_only."
            )

    def _crash(self, point: CrashPoint) -> None:
        if self.crash_injector is not None:
            self.crash_injector.check(point)

    def find_ready_action(
        self,
        trade_id: str,
    ) -> SagaActionRecord | None:
        for action in self.saga.list_actions(
            trade_id
        ):
            if (
                action.status
                == SagaActionStatus.READY
            ):
                return action
        return None

    def execute_ready_action(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> SagaActionRecord:
        if (
            len(self._held_claims)
            >= self.limits.max_concurrent_claims
        ):
            raise SagaExecutorError(
                "The executor already holds its "
                "maximum number of claims."
            )

        self._crash(CrashPoint.BEFORE_CLAIM)
        action, _ = self.saga.claim_action(
            action_id,
            owner=self.owner,
            ttl=self.limits.claim_ttl,
            occurred_at=now,
        )
        self._held_claims.add(action_id)
        try:
            self._crash(CrashPoint.AFTER_CLAIM)

            if (
                action.action_type
                not in BROKER_WRITE_ACTION_TYPES
            ):
                raise SagaExecutorError(
                    "Only broker-write saga "
                    "actions are executable "
                    "through the transport."
                )

            self.saga.verify_action_integrity(
                action_id, occurred_at=now
            )

            action, attempt = (
                self.saga
                .record_submission_intent(
                    action_id,
                    owner=self.owner,
                    occurred_at=now,
                )
            )
            self._crash(
                CrashPoint.AFTER_ATTEMPT_CREATION
            )
            self._crash(
                CrashPoint.AFTER_SUBMISSION_INTENT
            )
            self._crash(
                CrashPoint
                .BEFORE_TRANSPORT_INVOCATION
            )

            payload = json.loads(
                action.payload_json
            )
            try:
                if (
                    action.action_type
                    in _CANCEL_ACTION_TYPES
                ):
                    if not (
                        action
                        .source_broker_order_id
                    ):
                        raise SagaExecutorError(
                            "Cancellation actions "
                            "require a source "
                            "broker order ID."
                        )
                    response = (
                        self.transport
                        .cancel_order(
                            action
                            .source_broker_order_id,
                            ref_id=action.ref_id,
                        )
                    )
                else:
                    response = (
                        self.transport
                        .submit_order(
                            payload,
                            ref_id=action.ref_id,
                        )
                    )
            except TransportTimeout:
                return self.saga.finalize_attempt(
                    action_id,
                    owner=self.owner,
                    classification="timeout",
                    occurred_at=now,
                )
            except FakeTransportError as error:
                finalized = (
                    self.saga.finalize_attempt(
                        action_id,
                        owner=self.owner,
                        classification=(
                            "rejected_terminal"
                        ),
                        failure_code=(
                            "transport_fail_closed"
                        ),
                        failure_reason=str(error),
                        occurred_at=now,
                    )
                )
                self.saga.activate_broker_action_halt(
                    strategy=action.strategy,
                    reason=(
                        "The simulated transport "
                        "failed closed: "
                        + str(error)
                    ),
                    trade_id=action.trade_id,
                    action_id=action.action_id,
                    halt_key=(
                        "saga-transport-integrity:"
                        f"{action.action_id}"
                    ),
                    operator_ack_required=True,
                    occurred_at=now,
                )
                return finalized

            self._crash(
                CrashPoint
                .AFTER_RESPONSE_BEFORE_STATUS
            )
            return self.saga.finalize_attempt(
                action_id,
                owner=self.owner,
                classification=(
                    response.classification
                ),
                resulting_broker_order_id=(
                    response.broker_order_id
                ),
                failure_code=(
                    response.classification
                    if response.classification
                    != "acknowledged"
                    else None
                ),
                failure_reason=response.reason
                if response.classification
                != "acknowledged"
                else None,
                occurred_at=now,
            )
        finally:
            self._held_claims.discard(action_id)

    def try_confirm(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> SagaActionRecord:
        action = self.saga.get_action(action_id)
        if (
            action.status
            != SagaActionStatus
            .AWAITING_BROKER_CONFIRMATION
        ):
            raise SagaExecutorError(
                "Only actions awaiting broker "
                "confirmation can be confirmed."
            )
        self._crash(
            CrashPoint.WHILE_AWAITING_CONFIRMATION
        )
        snapshot = self.transport.snapshot(
            now=now
        )
        observation = derive_observation(
            action, snapshot
        )
        if not condition_satisfied(
            action.expected_postcondition,
            observation,
        ):
            return action
        confirmed = (
            self.saga.confirm_action_succeeded(
                action_id,
                snapshot=snapshot,
                evaluated_at=now,
                observation=observation,
                occurred_at=now,
            )
        )
        self._crash(
            CrashPoint.AFTER_PROOF_BEFORE_UNLOCK
        )
        self.unlock_dependents(
            action_id, now=now
        )
        return confirmed

    def poll_until_confirmed(
        self,
        action_id: str,
        *,
        start: datetime,
        interval: timedelta = timedelta(
            seconds=1
        ),
    ) -> SagaActionRecord:
        moment = start
        for _ in range(
            self.limits.max_confirmation_polls
        ):
            action = self.try_confirm(
                action_id, now=moment
            )
            if (
                action.status
                == SagaActionStatus
                .CONFIRMED_SUCCEEDED
            ):
                return action
            moment = moment + interval
        return self.saga.mark_outcome_unknown(
            action_id,
            reason=(
                "The deterministic confirmation "
                "polling limit was exhausted."
            ),
            occurred_at=moment,
        )

    def unlock_dependents(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> list[SagaActionRecord]:
        completed = self.saga.get_action(
            action_id
        )
        unlocked: list[SagaActionRecord] = []
        for candidate in self.saga.list_actions(
            completed.trade_id
        ):
            if candidate.status not in {
                SagaActionStatus.PLANNED,
                SagaActionStatus.BLOCKED,
            }:
                continue
            if (
                action_id
                not in self.saga.get_dependencies(
                    candidate.action_id
                )
            ):
                continue
            unlocked.append(
                self.saga.evaluate_readiness(
                    candidate.action_id,
                    occurred_at=now,
                )
            )
        return unlocked

    def reconcile_unknown(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> SagaActionRecord:
        action = self.saga.get_action(action_id)
        if (
            action.status
            != SagaActionStatus.OUTCOME_UNKNOWN
        ):
            raise SagaExecutorError(
                "Only OUTCOME_UNKNOWN actions can "
                "be reconciled."
            )

        snapshot = self.transport.snapshot(
            now=now
        )
        lookup = (
            self.transport.lookup_order_by_ref(
                action.ref_id
            )
        )
        observation: dict[str, Any]
        if lookup == "ambiguous":
            observation = {
                "effect_present": None,
            }
        elif lookup is not None:
            observation = {
                "effect_present": True,
                "resulting_broker_order_id": (
                    lookup
                ),
            }
        else:
            observation = {
                "effect_present": False,
            }
        return self.saga.resolve_unknown_outcome(
            action_id,
            snapshot=snapshot,
            evaluated_at=now,
            observation=observation,
            occurred_at=now,
        )

    def recover_claim(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> SagaActionRecord:
        self._crash(
            CrashPoint.DURING_CLAIM_RECOVERY
        )
        return self.saga.recover_expired_claim(
            action_id, occurred_at=now
        )

    def maybe_retry(
        self,
        action_id: str,
        *,
        now: datetime,
    ) -> SagaActionRecord:
        action = self.saga.get_action(action_id)
        if (
            action.attempt_count
            >= self.limits.max_attempts
        ):
            return self.saga.require_operator(
                action_id,
                reason=(
                    "The bounded simulated retry "
                    "budget is exhausted."
                ),
                occurred_at=now,
            )
        return self.saga.approve_retry(
            action_id, occurred_at=now
        )

    def route_lifecycle_observation(
        self,
        action_id: str,
        *,
        order_id: str,
        now: datetime,
        average_fill_price: Any = None,
        fees: Any = None,
    ):
        """
        Routes fake broker evidence through the
        existing lifecycle coordinators. The saga
        executor never bypasses the trade-state
        machines.
        """
        action = self.saga.get_action(action_id)
        order = (
            self.transport.state.as_equity_order(
                order_id
            )
        )
        trade_store = self.saga.trade_store

        if (
            action.action_type
            == SagaActionType.PLACE_ENTRY
            or action.action_type
            == SagaActionType
            .CANCEL_ENTRY_REMAINDER
        ):
            coordinator = (
                EntryLifecycleCoordinator(
                    trade_store
                )
            )
            return coordinator.apply(
                trade_id=action.trade_id,
                order=order,
                average_fill_price=(
                    average_fill_price
                ),
                observation_at=now,
            )
        if action.action_type in {
            SagaActionType
            .PLACE_INITIAL_PROTECTIVE_STOP,
            SagaActionType
            .PLACE_REPLACEMENT_PROTECTIVE_STOP,
            SagaActionType.CANCEL_PROTECTIVE_STOP,
        }:
            coordinator = (
                ProtectiveStopLifecycleCoordinator(
                    trade_store
                )
            )
            return coordinator.apply(
                trade_id=action.trade_id,
                order=order,
                average_fill_price=(
                    average_fill_price
                ),
                observation_at=now,
            )
        if action.action_type in {
            SagaActionType.PLACE_EXIT,
            SagaActionType.CANCEL_EXIT,
            SagaActionType.FLATTEN_POSITION,
        }:
            coordinator = (
                ExitOrderLifecycleCoordinator(
                    trade_store
                )
            )
            return coordinator.apply(
                trade_id=action.trade_id,
                order=order,
                average_fill_price=(
                    average_fill_price
                ),
                close_reason=action.close_reason,
                fees=fees,
                observation_at=now,
            )
        raise SagaExecutorError(
            "The saga action type has no "
            "lifecycle routing."
        )


def main() -> None:
    print("\nSIMULATED SAGA EXECUTOR\n")
    print("SIMULATION ONLY")
    print(
        "A transport-independent executor that "
        "consumes durable saga actions and can only "
        "be wired to a deterministic fake transport. "
        "No live broker path exists."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
