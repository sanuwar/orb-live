from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

try:
    from .account_state import (
        AccountInfo,
        AccountState,
        EquityOrder,
        EquityPosition,
        PortfolioState,
    )
    from .broker_snapshot import (
        BrokerSnapshot,
        BrokerSnapshotMetadata,
        account_fingerprint,
    )
except ImportError:
    from account_state import (
        AccountInfo,
        AccountState,
        EquityOrder,
        EquityPosition,
        PortfolioState,
    )
    from broker_snapshot import (
        BrokerSnapshot,
        BrokerSnapshotMetadata,
        account_fingerprint,
    )


SIMULATED_ACCOUNT_NUMBER = "SIMULATED-0001"


class SimulatedCrash(RuntimeError):
    """A deterministic injected crash used only in simulation."""


class FakeTransportError(RuntimeError):
    """A deterministic fail-closed fake transport error."""


class TransportTimeout(RuntimeError):
    """The simulated transport produced no trustworthy response."""


class CrashPoint(str, Enum):
    BEFORE_CLAIM = "before_claim"
    AFTER_CLAIM = "after_claim"
    AFTER_ATTEMPT_CREATION = "after_attempt_creation"
    AFTER_SUBMISSION_INTENT = (
        "after_submission_intent"
    )
    BEFORE_TRANSPORT_INVOCATION = (
        "before_transport_invocation"
    )
    AFTER_EFFECT_BEFORE_RESPONSE = (
        "after_effect_before_response"
    )
    AFTER_RESPONSE_BEFORE_STATUS = (
        "after_response_before_status"
    )
    WHILE_AWAITING_CONFIRMATION = (
        "while_awaiting_confirmation"
    )
    AFTER_PROOF_BEFORE_UNLOCK = (
        "after_proof_before_unlock"
    )
    DURING_CLAIM_RECOVERY = "during_claim_recovery"


class CrashInjector:
    """
    Deterministically injects a simulated crash at
    explicitly armed points. Each armed point fires
    exactly once so a restarted run can proceed.
    """

    def __init__(self, points=()):
        self.armed = set(points)
        self.fired: list[CrashPoint] = []

    def arm(self, point: CrashPoint) -> None:
        self.armed.add(point)

    def check(self, point: CrashPoint) -> None:
        if point in self.armed:
            self.armed.discard(point)
            self.fired.append(point)
            raise SimulatedCrash(
                f"Simulated crash at {point.value}."
            )


class TransportBehavior(str, Enum):
    REVIEW_ACCEPTED = "review_accepted"
    REVIEW_REJECTED = "review_rejected"
    ACKNOWLEDGED = "acknowledged"
    ACKNOWLEDGED_HIDDEN = "acknowledged_hidden"
    REJECTED_RETRYABLE = "rejected_retryable"
    REJECTED_TERMINAL = "rejected_terminal"
    TIMEOUT_BEFORE_RESPONSE = (
        "timeout_before_response"
    )
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"


@dataclass(frozen=True)
class TransportResponse:
    classification: str
    broker_order_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class ReviewEvidence:
    ref_id: str
    payload_digest: str
    approved: bool
    reason: str


@dataclass
class FakeOrder:
    order_id: str
    ref_id: str | None
    symbol: str
    side: str
    order_type: str
    state: str
    quantity: Decimal
    cumulative_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    trigger: str
    visible: bool = True

    @property
    def active(self) -> bool:
        return self.state in {
            "confirmed",
            "partially_filled",
        }


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _payload_decimal(
    payload: dict[str, Any],
    key: str,
) -> Decimal | None:
    value = payload.get(key)
    if value in {None, ""}:
        return None
    return Decimal(str(value))


class FakeBrokerState:
    """
    Deterministic in-memory fake broker state.

    Contains no network access, credentials, or
    production data. Mutated only by explicit test
    scripting and fake transport calls.
    """

    def __init__(
        self,
        *,
        account_number: str = (
            SIMULATED_ACCOUNT_NUMBER
        ),
    ):
        self.account_number = account_number
        self.orders: dict[str, FakeOrder] = {}
        self.positions: dict[str, Decimal] = {}
        self._order_counter = 0

    @property
    def fingerprint(self) -> str:
        return account_fingerprint(
            self.account_number
        )

    def next_order_id(self) -> str:
        self._order_counter += 1
        return f"fake-order-{self._order_counter}"

    def add_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        state: str = "confirmed",
        ref_id: str | None = None,
        trigger: str | None = None,
        visible: bool = True,
        order_id: str | None = None,
    ) -> FakeOrder:
        identifier = (
            order_id or self.next_order_id()
        )
        order = FakeOrder(
            order_id=identifier,
            ref_id=ref_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            state=state,
            quantity=Decimal(quantity),
            cumulative_quantity=Decimal("0"),
            limit_price=limit_price,
            stop_price=stop_price,
            trigger=(
                trigger
                or (
                    "stop"
                    if order_type == "stop_market"
                    else "immediate"
                )
            ),
            visible=visible,
        )
        self.orders[identifier] = order
        return order

    def fill_order(
        self,
        order_id: str,
        quantity: Decimal,
    ) -> FakeOrder:
        order = self.orders[order_id]
        fill = Decimal(quantity)
        if fill <= 0:
            raise FakeTransportError(
                "Scripted fill quantity must be "
                "positive."
            )
        order.cumulative_quantity += fill
        if (
            order.cumulative_quantity
            > order.quantity
        ):
            raise FakeTransportError(
                "Scripted fill exceeds the order "
                "quantity."
            )
        if (
            order.cumulative_quantity
            == order.quantity
        ):
            order.state = "filled"
        else:
            order.state = "partially_filled"
        signed = (
            fill
            if order.side == "buy"
            else -fill
        )
        self.positions[order.symbol] = (
            self.positions.get(
                order.symbol, Decimal("0")
            )
            + signed
        )
        return order

    def cancel_order_state(
        self,
        order_id: str,
    ) -> FakeOrder:
        order = self.orders[order_id]
        if order.state not in {
            "confirmed",
            "partially_filled",
        }:
            raise FakeTransportError(
                "Only active fake orders can be "
                "cancelled."
            )
        order.state = "cancelled"
        return order

    def reveal_order(self, order_id: str) -> None:
        self.orders[order_id].visible = True

    def visible_open_orders(
        self,
    ) -> tuple[FakeOrder, ...]:
        return tuple(
            order
            for order in sorted(
                self.orders.values(),
                key=lambda item: item.order_id,
            )
            if order.visible and order.active
        )

    def build_account_state(self) -> AccountState:
        return AccountState(
            account=AccountInfo(
                account_number=(
                    self.account_number
                ),
                masked_account_number=(
                    "simulated-account"
                ),
                brokerage_account_type=(
                    "individual"
                ),
                trading_type="cash",
                nickname="SIMULATION ONLY",
                agentic_allowed=True,
                state="active",
            ),
            portfolio=PortfolioState(
                total_value=Decimal("10000"),
                equity_value=Decimal("1000"),
                cash=Decimal("9000"),
                pending_deposits=Decimal("0"),
                buying_power=Decimal("9000"),
                unleveraged_buying_power=(
                    Decimal("9000")
                ),
                intraday_buying_power=None,
                currency="USD",
            ),
            positions=tuple(
                EquityPosition(
                    symbol=symbol,
                    quantity=quantity,
                    intraday_quantity=quantity,
                    average_buy_price=None,
                    shares_available_for_sells=(
                        quantity
                    ),
                    shares_held_for_sells=(
                        Decimal("0")
                    ),
                    position_type="long",
                )
                for symbol, quantity in sorted(
                    self.positions.items()
                )
                if quantity != 0
            ),
            open_orders=tuple(
                EquityOrder(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    state=order.state,
                    quantity=order.quantity,
                    cumulative_quantity=(
                        order.cumulative_quantity
                    ),
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    trigger=order.trigger,
                    placed_agent="agentic",
                )
                for order in (
                    self.visible_open_orders()
                )
            ),
        )

    def as_equity_order(
        self,
        order_id: str,
    ) -> EquityOrder:
        order = self.orders[order_id]
        return EquityOrder(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            state=order.state,
            quantity=order.quantity,
            cumulative_quantity=(
                order.cumulative_quantity
            ),
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            trigger=order.trigger,
            placed_agent="agentic",
        )


class BrokerTransport(Protocol):
    """
    Transport protocol consumed by the saga executor.

    The only concrete implementation permitted during
    simulation milestones is a deterministic fake.
    """

    simulated_only: bool

    def review_order(
        self,
        payload: dict[str, Any],
        *,
        ref_id: str,
    ) -> ReviewEvidence: ...

    def submit_order(
        self,
        payload: dict[str, Any],
        *,
        ref_id: str,
    ) -> TransportResponse: ...

    def cancel_order(
        self,
        order_id: str,
        *,
        ref_id: str,
    ) -> TransportResponse: ...

    def lookup_order_by_ref(
        self,
        ref_id: str,
    ) -> str | None: ...

    def snapshot(
        self,
        *,
        now: datetime,
    ) -> BrokerSnapshot: ...


class SimulatedBrokerTransport:
    """
    SIMULATION ONLY deterministic fake transport.

    Mutates only deterministic in-memory fake broker
    state. Performs no network access, uses no
    credentials, and can never reach a real broker.
    """

    simulated_only = True

    def __init__(
        self,
        state: FakeBrokerState | None = None,
    ):
        self.state = state or FakeBrokerState()
        self._submit_scripts: dict[
            str, TransportBehavior
        ] = {}
        self._cancel_scripts: dict[
            str, TransportBehavior
        ] = {}
        self._review_scripts: dict[
            str, TransportBehavior
        ] = {}
        self._lookup_scripts: dict[
            str, str | None
        ] = {}
        self._seen_submissions: dict[
            str, tuple[str, TransportResponse]
        ] = {}
        self._seen_cancellations: dict[
            str, tuple[str, TransportResponse]
        ] = {}
        self.review_log: list[ReviewEvidence] = []
        self.submit_calls = 0
        self.cancel_calls = 0
        self.review_calls = 0
        self.snapshot_calls = 0
        self._snapshot_counter = 0
        self._next_snapshot_overrides: dict[
            str, Any
        ] = {}

    # -- scripting -------------------------------------------------

    def script_submit(
        self,
        ref_id: str,
        behavior: TransportBehavior,
    ) -> None:
        self._submit_scripts[ref_id] = behavior

    def script_cancel(
        self,
        ref_id: str,
        behavior: TransportBehavior,
    ) -> None:
        self._cancel_scripts[ref_id] = behavior

    def script_review(
        self,
        ref_id: str,
        behavior: TransportBehavior,
    ) -> None:
        self._review_scripts[ref_id] = behavior

    def script_lookup(
        self,
        ref_id: str,
        result: str | None,
    ) -> None:
        self._lookup_scripts[ref_id] = result

    def force_next_snapshot(
        self, **overrides: Any
    ) -> None:
        self._next_snapshot_overrides = overrides

    # -- protocol methods -----------------------------------------

    def review_order(
        self,
        payload: dict[str, Any],
        *,
        ref_id: str,
    ) -> ReviewEvidence:
        self.review_calls += 1
        behavior = self._review_scripts.get(
            ref_id,
            TransportBehavior.REVIEW_ACCEPTED,
        )
        approved = (
            behavior
            == TransportBehavior.REVIEW_ACCEPTED
        )
        evidence = ReviewEvidence(
            ref_id=ref_id,
            payload_digest=_payload_digest(
                payload
            ),
            approved=approved,
            reason=(
                "Simulated review accepted."
                if approved
                else "Simulated review rejected."
            ),
        )
        self.review_log.append(evidence)
        return evidence

    def _materialize_order(
        self,
        payload: dict[str, Any],
        ref_id: str,
        *,
        visible: bool,
    ) -> FakeOrder:
        quantity = _payload_decimal(
            payload, "quantity"
        )
        if quantity is None or quantity <= 0:
            raise FakeTransportError(
                "Simulated submission requires a "
                "positive quantity."
            )
        return self.state.add_order(
            symbol=str(
                payload.get("symbol", "")
            ).upper(),
            side=str(payload.get("side", "")),
            order_type=str(
                payload.get("type", "")
            ),
            quantity=quantity,
            limit_price=_payload_decimal(
                payload, "limit_price"
            ),
            stop_price=_payload_decimal(
                payload, "stop_price"
            ),
            ref_id=ref_id,
            visible=visible,
        )

    def submit_order(
        self,
        payload: dict[str, Any],
        *,
        ref_id: str,
    ) -> TransportResponse:
        self.submit_calls += 1
        digest = _payload_digest(payload)

        if ref_id in self._seen_submissions:
            seen_digest, seen_response = (
                self._seen_submissions[ref_id]
            )
            if seen_digest != digest:
                raise FakeTransportError(
                    "A duplicate ref_id with a "
                    "different payload is "
                    "forbidden."
                )
            return seen_response

        behavior = self._submit_scripts.get(
            ref_id,
            TransportBehavior.ACKNOWLEDGED,
        )

        if (
            behavior
            == TransportBehavior
            .TIMEOUT_BEFORE_RESPONSE
        ):
            raise TransportTimeout(
                "Simulated timeout before any "
                "broker-side effect."
            )
        if (
            behavior
            == TransportBehavior
            .TIMEOUT_AFTER_EFFECT
        ):
            self._materialize_order(
                payload, ref_id, visible=True
            )
            raise TransportTimeout(
                "Simulated timeout after a "
                "broker-side effect."
            )
        if behavior in {
            TransportBehavior.REJECTED_RETRYABLE,
            TransportBehavior.REJECTED_TERMINAL,
        }:
            return TransportResponse(
                classification=behavior.value,
                broker_order_id=None,
                reason=(
                    "Simulated placement "
                    "rejection produced no "
                    "broker-side effect."
                ),
            )

        order = self._materialize_order(
            payload,
            ref_id,
            visible=(
                behavior
                != TransportBehavior
                .ACKNOWLEDGED_HIDDEN
            ),
        )
        response = TransportResponse(
            classification="acknowledged",
            broker_order_id=order.order_id,
            reason=(
                "Simulated placement "
                "acknowledged."
            ),
        )
        self._seen_submissions[ref_id] = (
            digest,
            response,
        )
        return response

    def cancel_order(
        self,
        order_id: str,
        *,
        ref_id: str,
    ) -> TransportResponse:
        self.cancel_calls += 1
        digest = _payload_digest(
            {"order_id": order_id}
        )

        if ref_id in self._seen_cancellations:
            seen_digest, seen_response = (
                self._seen_cancellations[ref_id]
            )
            if seen_digest != digest:
                raise FakeTransportError(
                    "A duplicate ref_id with a "
                    "different payload is "
                    "forbidden."
                )
            return seen_response

        behavior = self._cancel_scripts.get(
            ref_id,
            TransportBehavior.ACKNOWLEDGED,
        )

        if (
            behavior
            == TransportBehavior
            .TIMEOUT_BEFORE_RESPONSE
        ):
            raise TransportTimeout(
                "Simulated cancellation timeout."
            )
        if (
            behavior
            == TransportBehavior
            .TIMEOUT_AFTER_EFFECT
        ):
            self.state.cancel_order_state(
                order_id
            )
            raise TransportTimeout(
                "Simulated cancellation timeout "
                "after effect."
            )
        if behavior in {
            TransportBehavior.REJECTED_RETRYABLE,
            TransportBehavior.REJECTED_TERMINAL,
        }:
            return TransportResponse(
                classification=behavior.value,
                broker_order_id=order_id,
                reason=(
                    "Simulated cancellation "
                    "rejection produced no "
                    "broker-side effect."
                ),
            )

        if order_id not in self.state.orders:
            raise FakeTransportError(
                "Simulated cancellation targets a "
                "missing order."
            )
        self.state.cancel_order_state(order_id)
        response = TransportResponse(
            classification="acknowledged",
            broker_order_id=order_id,
            reason=(
                "Simulated cancellation "
                "acknowledged."
            ),
        )
        self._seen_cancellations[ref_id] = (
            digest,
            response,
        )
        return response

    def lookup_order_by_ref(
        self,
        ref_id: str,
    ) -> str | None:
        if ref_id in self._lookup_scripts:
            return self._lookup_scripts[ref_id]
        for order in self.state.orders.values():
            if (
                order.ref_id == ref_id
                and order.visible
            ):
                return order.order_id
        return None

    def snapshot(
        self,
        *,
        now: datetime,
    ) -> BrokerSnapshot:
        self.snapshot_calls += 1
        self._snapshot_counter += 1
        overrides = self._next_snapshot_overrides
        self._next_snapshot_overrides = {}

        reconciliation_id = overrides.get(
            "reconciliation_id",
            "fake-recon-"
            f"{self._snapshot_counter}",
        )
        completed_at = now - overrides.get(
            "age", timedelta(milliseconds=1)
        )
        started_at = completed_at - timedelta(
            milliseconds=3
        )
        positions_read_at = (
            started_at + timedelta(milliseconds=1)
        )
        orders_read_at = started_at + timedelta(
            milliseconds=2
        )
        fingerprint = overrides.get(
            "account_fingerprint_value",
            self.state.fingerprint,
        )
        return BrokerSnapshot(
            account_state=(
                self.state.build_account_state()
            ),
            metadata=BrokerSnapshotMetadata(
                reconciliation_id=(
                    reconciliation_id
                ),
                account_fingerprint=fingerprint,
                started_at=started_at,
                positions_read_at=(
                    positions_read_at
                ),
                orders_read_at=orders_read_at,
                completed_at=completed_at,
                positions_cycle_id=(
                    reconciliation_id
                ),
                orders_cycle_id=reconciliation_id,
                positions_complete=overrides.get(
                    "positions_complete", True
                ),
                orders_complete=overrides.get(
                    "orders_complete", True
                ),
            ),
        )


def main() -> None:
    print("\nSIMULATED BROKER TRANSPORT\n")
    print("SIMULATION ONLY")
    print(
        "A deterministic scripted fake transport "
        "mutating only in-memory fake broker state. "
        "No network, no credentials, no real broker."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
