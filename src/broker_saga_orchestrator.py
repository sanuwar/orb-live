from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

try:
    from .broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        SagaActionType,
        normalized_decimal_text,
    )
    from .broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
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
except ImportError:
    from broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        SagaActionType,
        normalized_decimal_text,
    )
    from broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
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


DEFAULT_ROUTING_CLAIM_TTL = timedelta(seconds=30)


class OrchestrationError(BrokerActionSagaError):
    """Raised when durable lifecycle routing cannot proceed safely."""


class OrchestrationCrashPoint(str, Enum):
    BEFORE_ROUTING_CREATION = (
        "before_routing_creation"
    )
    AFTER_ROUTING_CREATION = (
        "after_routing_creation"
    )
    AFTER_ROUTING_CLAIM = "after_routing_claim"
    BEFORE_LIFECYCLE_INVOCATION = (
        "before_lifecycle_invocation"
    )
    AFTER_LIFECYCLE_MUTATION = (
        "after_lifecycle_mutation"
    )
    AFTER_EVENT_PERSISTENCE = (
        "after_event_persistence"
    )
    AFTER_ROUTING_COMPLETION_BEFORE_UNLOCK = (
        "after_routing_completion_before_unlock"
    )
    DURING_ACK_PERSISTENCE = (
        "during_ack_persistence"
    )
    AFTER_EVIDENCE_BEFORE_SAGA_TRANSITION = (
        "after_evidence_before_saga_transition"
    )
    AFTER_SAGA_TRANSITION_BEFORE_ROUTING = (
        "after_saga_transition_before_routing"
    )


class RoutingFactType(str, Enum):
    ORDER_VISIBLE = "order_visible"
    ORDER_TERMINAL = "order_terminal"
    ORDER_CANCELLED = "order_cancelled"
    PARTIAL_FILL_CONFIRMED = (
        "partial_fill_confirmed"
    )
    FULL_FILL_CONFIRMED = "full_fill_confirmed"
    POSITION_QUANTITY_CONFIRMED = (
        "position_quantity_confirmed"
    )
    POSITION_FLAT_CONFIRMED = (
        "position_flat_confirmed"
    )
    ACTIVE_PROTECTIVE_STOP_CONFIRMED = (
        "active_protective_stop_confirmed"
    )
    PROTECTIVE_STOP_PARTIAL_FILL_CONFIRMED = (
        "protective_stop_partial_fill_confirmed"
    )
    PROTECTIVE_STOP_TERMINAL_CONFIRMED = (
        "protective_stop_terminal_confirmed"
    )
    NO_BROKER_EFFECT_CONFIRMED = (
        "no_broker_effect_confirmed"
    )
    BROKER_EFFECT_CONFIRMED = (
        "broker_effect_confirmed"
    )


class LifecycleTarget(str, Enum):
    ENTRY = "entry"
    PROTECTIVE_STOP = "protective_stop"
    EXIT = "exit"
    PROTECTION_RECONCILIATION = (
        "protection_reconciliation"
    )
    NONE = "none"


class RoutingStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    CLAIMED = "claimed"
    APPLIED = "applied"
    BLOCKED = "blocked"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    OPERATOR_REQUIRED = "operator_required"


ROUTING_TRANSITIONS = {
    RoutingStatus.PLANNED: {
        RoutingStatus.READY,
        RoutingStatus.BLOCKED,
    },
    RoutingStatus.READY: {
        RoutingStatus.CLAIMED,
        RoutingStatus.BLOCKED,
    },
    RoutingStatus.CLAIMED: {
        RoutingStatus.APPLIED,
        RoutingStatus.READY,
        RoutingStatus.BLOCKED,
        RoutingStatus.RETRYABLE_FAILURE,
        RoutingStatus.TERMINAL_FAILURE,
        RoutingStatus.OPERATOR_REQUIRED,
    },
    RoutingStatus.BLOCKED: {
        RoutingStatus.READY,
        RoutingStatus.TERMINAL_FAILURE,
        RoutingStatus.OPERATOR_REQUIRED,
    },
    RoutingStatus.RETRYABLE_FAILURE: {
        RoutingStatus.READY,
        RoutingStatus.TERMINAL_FAILURE,
        RoutingStatus.OPERATOR_REQUIRED,
    },
    RoutingStatus.APPLIED: set(),
    RoutingStatus.TERMINAL_FAILURE: set(),
    RoutingStatus.OPERATOR_REQUIRED: set(),
}


_ENTRY_ACTION_TYPES = {
    SagaActionType.PLACE_ENTRY,
    SagaActionType.CANCEL_ENTRY_REMAINDER,
}

_STOP_ACTION_TYPES = {
    SagaActionType.PLACE_INITIAL_PROTECTIVE_STOP,
    SagaActionType
    .PLACE_REPLACEMENT_PROTECTIVE_STOP,
    SagaActionType.CANCEL_PROTECTIVE_STOP,
}

_EXIT_ACTION_TYPES = {
    SagaActionType.PLACE_EXIT,
    SagaActionType.CANCEL_EXIT,
    SagaActionType.FLATTEN_POSITION,
}

_ACTIVE_ORDER_STATES = {
    "confirmed",
    "partially_filled",
}

_LIFECYCLE_EVENT_TABLES = {
    LifecycleTarget.ENTRY: (
        "entry_recovery_events"
    ),
    LifecycleTarget.PROTECTIVE_STOP: (
        "protective_stop_recovery_events"
    ),
    LifecycleTarget.EXIT: (
        "exit_recovery_events"
    ),
}


@dataclass(frozen=True)
class ProvenFact:
    fact_type: RoutingFactType
    target: LifecycleTarget
    broker_order_id: str | None
    proven_quantity: Decimal | None
    proven_filled_quantity: Decimal | None
    proven_remaining_quantity: Decimal | None
    proven_protected_quantity: Decimal | None
    close_reason: str | None
    description: str


@dataclass(frozen=True)
class RoutingRecord:
    routing_id: str
    action_id: str
    trade_id: str
    strategy: str
    symbol: str
    action_type: SagaActionType
    action_status_at_routing: SagaActionStatus
    routing_type: RoutingFactType
    source_proof_id: str
    source_observation_id: str | None
    source_snapshot_digest: str
    account_fingerprint: str
    payload_digest: str
    broker_order_id: str | None
    proven_quantity: Decimal | None
    proven_filled_quantity: Decimal | None
    proven_remaining_quantity: Decimal | None
    proven_protected_quantity: Decimal | None
    proven_average_price: Decimal | None
    close_reason: str | None
    target_coordinator: LifecycleTarget
    target_fact: str
    status: RoutingStatus
    idempotency_key: str
    lifecycle_observation_key: str
    created_at: datetime
    claim_owner: str | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    completed_at: datetime | None
    blocked_reason: str | None
    failure_code: str | None
    failure_reason: str | None
    version: int


def _optional_decimal_text(
    value: Decimal | None,
) -> str | None:
    if value is None:
        return None
    return normalized_decimal_text(value)


def _parse_optional_decimal(
    value: Any,
) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def routing_idempotency_key(
    *,
    action_id: str,
    fact: ProvenFact,
    trade_id: str,
) -> str:
    payload = {
        "action_id": action_id,
        "trade_id": trade_id,
        "fact_type": fact.fact_type.value,
        "target": fact.target.value,
        "broker_order_id": fact.broker_order_id,
        "proven_quantity": _optional_decimal_text(
            fact.proven_quantity
        ),
        "proven_filled_quantity": (
            _optional_decimal_text(
                fact.proven_filled_quantity
            )
        ),
        "proven_remaining_quantity": (
            _optional_decimal_text(
                fact.proven_remaining_quantity
            )
        ),
        "proven_protected_quantity": (
            _optional_decimal_text(
                fact.proven_protected_quantity
            )
        ),
        "close_reason": fact.close_reason,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _order_in_snapshot(
    snapshot: BrokerSnapshot,
    order_id: str | None,
):
    if not order_id:
        return None
    for order in (
        snapshot.account_state.open_orders
    ):
        if order.order_id == order_id:
            return order
    return None


def _position_quantity(
    snapshot: BrokerSnapshot,
    symbol: str,
) -> Decimal:
    for position in (
        snapshot.account_state.positions
    ):
        if position.symbol == symbol:
            return Decimal(position.quantity)
    return Decimal("0")


def _conflicting_sell_orders(
    snapshot: BrokerSnapshot,
    symbol: str,
    excluded_order_id: str | None,
) -> int:
    return len(
        [
            order
            for order in (
                snapshot.account_state.open_orders
            )
            if order.symbol == symbol
            and order.side == "sell"
            and order.order_id
            != excluded_order_id
        ]
    )


def derive_routing_facts(
    action: SagaActionRecord,
    *,
    snapshot: BrokerSnapshot,
    symbol: str,
    order_observation=None,
) -> tuple[ProvenFact, ...]:
    """
    Derives routable proven facts from fresh,
    already-validated broker evidence.

    Pure and deterministic. Facts are never inferred
    from acknowledgments: order visibility never
    creates a position, cancellation never proves
    safety, and full fills additionally require the
    snapshot position to support the fill.
    """
    facts: list[ProvenFact] = []
    observed = order_observation
    observed_id = (
        observed.order_id
        if observed is not None
        else None
    )
    relevant_id = (
        action.resulting_broker_order_id
        or action.source_broker_order_id
    )

    if observed is not None:
        if observed_id != relevant_id:
            raise OrchestrationError(
                "The order observation does not "
                "match the saga action's broker "
                "order."
            )
        if observed.symbol != symbol:
            raise OrchestrationError(
                "The order observation symbol does "
                "not match the trade."
            )
        snapshot_order = _order_in_snapshot(
            snapshot, observed_id
        )
        observed_active = (
            str(observed.state)
            in _ACTIVE_ORDER_STATES
        )
        if observed_active and (
            snapshot_order is None
        ):
            raise OrchestrationError(
                "Conflicting evidence: the order "
                "observation is active but absent "
                "from the fresh snapshot."
            )
        if not observed_active and (
            snapshot_order is not None
        ):
            raise OrchestrationError(
                "Conflicting evidence: the order "
                "observation is terminal but still "
                "open in the fresh snapshot."
            )

    position = _position_quantity(
        snapshot, symbol
    )

    if observed is not None:
        quantity = Decimal(observed.quantity)
        cumulative = Decimal(
            observed.cumulative_quantity
        )
        remaining = quantity - cumulative
        state = str(observed.state)
        active = state in _ACTIVE_ORDER_STATES
        is_stop_action = (
            action.action_type
            in _STOP_ACTION_TYPES
        )
        target = (
            LifecycleTarget.PROTECTIVE_STOP
            if is_stop_action
            else LifecycleTarget.ENTRY
            if action.action_type
            in _ENTRY_ACTION_TYPES
            else LifecycleTarget.EXIT
        )

        if active and cumulative == 0:
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .ORDER_VISIBLE
                    ),
                    target=target,
                    broker_order_id=observed_id,
                    proven_quantity=quantity,
                    proven_filled_quantity=(
                        Decimal("0")
                    ),
                    proven_remaining_quantity=(
                        quantity
                    ),
                    proven_protected_quantity=None,
                    close_reason=(
                        action.close_reason
                    ),
                    description=(
                        "The order is visible and "
                        "unfilled; no position "
                        "fact is proven."
                    ),
                )
            )
        elif cumulative > 0 and (
            cumulative < quantity
        ):
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .PROTECTIVE_STOP_PARTIAL_FILL_CONFIRMED
                        if is_stop_action
                        else RoutingFactType
                        .PARTIAL_FILL_CONFIRMED
                    ),
                    target=target,
                    broker_order_id=observed_id,
                    proven_quantity=quantity,
                    proven_filled_quantity=(
                        cumulative
                    ),
                    proven_remaining_quantity=(
                        remaining
                    ),
                    proven_protected_quantity=None,
                    close_reason=(
                        action.close_reason
                    ),
                    description=(
                        "A partial fill is proven "
                        "by the order observation "
                        "and fresh snapshot."
                    ),
                )
            )
        elif cumulative == quantity and (
            state == "filled"
        ):
            if (
                action.action_type
                == SagaActionType.PLACE_ENTRY
                and position < cumulative
            ):
                raise OrchestrationError(
                    "Conflicting evidence: a full "
                    "entry fill is claimed but "
                    "the snapshot position does "
                    "not support it."
                )
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .FULL_FILL_CONFIRMED
                    ),
                    target=target,
                    broker_order_id=observed_id,
                    proven_quantity=quantity,
                    proven_filled_quantity=(
                        cumulative
                    ),
                    proven_remaining_quantity=(
                        Decimal("0")
                    ),
                    proven_protected_quantity=None,
                    close_reason=(
                        action.close_reason
                    ),
                    description=(
                        "A full fill is proven by "
                        "the terminal order "
                        "observation."
                    ),
                )
            )
        elif state in {"cancelled", "rejected"}:
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .PROTECTIVE_STOP_TERMINAL_CONFIRMED
                        if is_stop_action
                        else RoutingFactType
                        .ORDER_CANCELLED
                        if state == "cancelled"
                        else RoutingFactType
                        .ORDER_TERMINAL
                    ),
                    target=target,
                    broker_order_id=observed_id,
                    proven_quantity=quantity,
                    proven_filled_quantity=(
                        cumulative
                    ),
                    proven_remaining_quantity=(
                        Decimal("0")
                    ),
                    proven_protected_quantity=None,
                    close_reason=(
                        action.close_reason
                    ),
                    description=(
                        "The order is terminal; "
                        "cancellation proves only "
                        "order inactivity, never "
                        "position safety."
                    ),
                )
            )

    if action.action_type in _STOP_ACTION_TYPES:
        stop_order = _order_in_snapshot(
            snapshot,
            action.resulting_broker_order_id,
        )
        if (
            stop_order is not None
            and stop_order.side == "sell"
            and stop_order.order_type
            == "stop_market"
            and str(stop_order.state)
            in _ACTIVE_ORDER_STATES
            and Decimal(
                stop_order.cumulative_quantity
            )
            == 0
            and action.requested_quantity
            is not None
            and Decimal(stop_order.quantity)
            == action.requested_quantity
            and (
                action.stop_price is None
                or Decimal(
                    stop_order.stop_price
                )
                == action.stop_price
            )
            and _conflicting_sell_orders(
                snapshot,
                symbol,
                stop_order.order_id,
            )
            == 0
        ):
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .ACTIVE_PROTECTIVE_STOP_CONFIRMED
                    ),
                    target=(
                        LifecycleTarget
                        .PROTECTION_RECONCILIATION
                    ),
                    broker_order_id=(
                        stop_order.order_id
                    ),
                    proven_quantity=Decimal(
                        stop_order.quantity
                    ),
                    proven_filled_quantity=(
                        Decimal("0")
                    ),
                    proven_remaining_quantity=(
                        Decimal(
                            stop_order.quantity
                        )
                    ),
                    proven_protected_quantity=(
                        Decimal(
                            stop_order.quantity
                        )
                    ),
                    close_reason=None,
                    description=(
                        "An exact, conflict-free "
                        "active protective stop "
                        "is proven by the fresh "
                        "snapshot."
                    ),
                )
            )

    if (
        action.action_type
        in _EXIT_ACTION_TYPES
        or action.action_type
        == SagaActionType.RECONCILE_ONLY
    ):
        symbol_orders = [
            order
            for order in (
                snapshot.account_state.open_orders
            )
            if order.symbol == symbol
        ]
        if position == 0 and not symbol_orders:
            facts.append(
                ProvenFact(
                    fact_type=(
                        RoutingFactType
                        .POSITION_FLAT_CONFIRMED
                    ),
                    target=(
                        LifecycleTarget
                        .PROTECTION_RECONCILIATION
                    ),
                    broker_order_id=None,
                    proven_quantity=Decimal("0"),
                    proven_filled_quantity=None,
                    proven_remaining_quantity=(
                        Decimal("0")
                    ),
                    proven_protected_quantity=None,
                    close_reason=(
                        action.close_reason
                    ),
                    description=(
                        "A flat position with no "
                        "open symbol orders is "
                        "proven by the fresh "
                        "snapshot."
                    ),
                )
            )

    if (
        action.action_type
        == SagaActionType.RECONCILE_ONLY
        and position > 0
    ):
        facts.append(
            ProvenFact(
                fact_type=(
                    RoutingFactType
                    .POSITION_QUANTITY_CONFIRMED
                ),
                target=LifecycleTarget.NONE,
                broker_order_id=None,
                proven_quantity=position,
                proven_filled_quantity=None,
                proven_remaining_quantity=(
                    position
                ),
                proven_protected_quantity=None,
                close_reason=None,
                description=(
                    "The current broker position "
                    "quantity is proven; no "
                    "lifecycle transition is "
                    "implied."
                ),
            )
        )

    return tuple(facts)


class SagaOrchestrator:
    """
    Durable, restart-safe saga-to-lifecycle routing.

    The orchestrator is transport-independent: it
    consumes only durable saga state, fresh validated
    broker evidence supplied by its caller, and the
    existing lifecycle coordinators. It has no broker
    client, no transport, no network path, and no
    credential access, and it never fabricates
    broker outcomes.
    """

    def __init__(
        self,
        saga_store: BrokerActionSagaStore,
        *,
        owner: str,
        claim_ttl: timedelta = (
            DEFAULT_ROUTING_CLAIM_TTL
        ),
        crash_injector=None,
    ):
        if not isinstance(
            saga_store, BrokerActionSagaStore
        ):
            raise OrchestrationError(
                "A BrokerActionSagaStore is "
                "required."
            )
        owner = owner.strip()
        if not owner:
            raise OrchestrationError(
                "An orchestration owner is "
                "required."
            )
        if claim_ttl <= timedelta(0):
            raise OrchestrationError(
                "Routing claim TTL must be "
                "positive."
            )
        self.saga = saga_store
        self.trade_store = saga_store.trade_store
        self.db_path = saga_store.db_path
        self.owner = owner
        self.claim_ttl = claim_ttl
        self.crash_injector = crash_injector
        self._initialize()

    def _crash(
        self, point: OrchestrationCrashPoint
    ) -> None:
        if self.crash_injector is not None:
            self.crash_injector.check(point)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS
                    broker_saga_routings (
                        routing_id TEXT PRIMARY KEY,
                        action_id TEXT NOT NULL,
                        trade_id TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        action_status_at_routing
                            TEXT NOT NULL,
                        routing_type TEXT NOT NULL,
                        source_proof_id
                            TEXT NOT NULL,
                        source_observation_id TEXT,
                        source_snapshot_digest
                            TEXT NOT NULL,
                        account_fingerprint
                            TEXT NOT NULL,
                        payload_digest
                            TEXT NOT NULL,
                        broker_order_id TEXT,
                        proven_quantity TEXT,
                        proven_filled_quantity TEXT,
                        proven_remaining_quantity
                            TEXT,
                        proven_protected_quantity
                            TEXT,
                        proven_average_price TEXT,
                        close_reason TEXT,
                        target_coordinator
                            TEXT NOT NULL,
                        target_fact TEXT NOT NULL,
                        status TEXT NOT NULL,
                        idempotency_key TEXT
                            NOT NULL UNIQUE,
                        lifecycle_observation_key
                            TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        claim_owner TEXT,
                        claimed_at TEXT,
                        claim_expires_at TEXT,
                        completed_at TEXT,
                        blocked_reason TEXT,
                        failure_code TEXT,
                        failure_reason TEXT,
                        version INTEGER NOT NULL
                            DEFAULT 1,
                        FOREIGN KEY(action_id)
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            ),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(
                                trade_id
                            )
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_broker_saga_routings_status
                ON broker_saga_routings(
                    trade_id, status
                );

                CREATE TABLE IF NOT EXISTS
                    broker_saga_routing_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        routing_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(routing_id)
                            REFERENCES
                            broker_saga_routings(
                                routing_id
                            )
                    );

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_routing_event_update
                BEFORE UPDATE
                ON broker_saga_routing_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_routing_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_routing_event_delete
                BEFORE DELETE
                ON broker_saga_routing_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_routing_events are append-only'
                    );
                END;
                """
            )

    @staticmethod
    def _row_to_routing(
        row: sqlite3.Row,
    ) -> RoutingRecord:
        return RoutingRecord(
            routing_id=row["routing_id"],
            action_id=row["action_id"],
            trade_id=row["trade_id"],
            strategy=row["strategy"],
            symbol=row["symbol"],
            action_type=SagaActionType(
                row["action_type"]
            ),
            action_status_at_routing=(
                SagaActionStatus(
                    row[
                        "action_status_at_routing"
                    ]
                )
            ),
            routing_type=RoutingFactType(
                row["routing_type"]
            ),
            source_proof_id=(
                row["source_proof_id"]
            ),
            source_observation_id=(
                row["source_observation_id"]
            ),
            source_snapshot_digest=(
                row["source_snapshot_digest"]
            ),
            account_fingerprint=(
                row["account_fingerprint"]
            ),
            payload_digest=row["payload_digest"],
            broker_order_id=(
                row["broker_order_id"]
            ),
            proven_quantity=(
                _parse_optional_decimal(
                    row["proven_quantity"]
                )
            ),
            proven_filled_quantity=(
                _parse_optional_decimal(
                    row["proven_filled_quantity"]
                )
            ),
            proven_remaining_quantity=(
                _parse_optional_decimal(
                    row[
                        "proven_remaining_quantity"
                    ]
                )
            ),
            proven_protected_quantity=(
                _parse_optional_decimal(
                    row[
                        "proven_protected_quantity"
                    ]
                )
            ),
            proven_average_price=(
                _parse_optional_decimal(
                    row["proven_average_price"]
                )
            ),
            close_reason=row["close_reason"],
            target_coordinator=LifecycleTarget(
                row["target_coordinator"]
            ),
            target_fact=row["target_fact"],
            status=RoutingStatus(row["status"]),
            idempotency_key=(
                row["idempotency_key"]
            ),
            lifecycle_observation_key=(
                row["lifecycle_observation_key"]
            ),
            created_at=_parse_datetime(
                row["created_at"]
            ),
            claim_owner=row["claim_owner"],
            claimed_at=_parse_datetime(
                row["claimed_at"]
            ),
            claim_expires_at=_parse_datetime(
                row["claim_expires_at"]
            ),
            completed_at=_parse_datetime(
                row["completed_at"]
            ),
            blocked_reason=row["blocked_reason"],
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
            version=int(row["version"]),
        )

    def _load_routing_row(
        self,
        connection: sqlite3.Connection,
        routing_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM broker_saga_routings
            WHERE routing_id = ?
            """,
            (routing_id,),
        ).fetchone()
        if row is None:
            raise OrchestrationError(
                f"Routing record not found: "
                f"{routing_id}"
            )
        return row

    def _insert_routing_event(
        self,
        connection: sqlite3.Connection,
        *,
        routing_id: str,
        event_type: str,
        old_status: RoutingStatus | None,
        new_status: RoutingStatus | None,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO broker_saga_routing_events (
                routing_id, event_type,
                old_status, new_status,
                occurred_at, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                routing_id,
                event_type,
                (
                    old_status.value
                    if old_status
                    else None
                ),
                (
                    new_status.value
                    if new_status
                    else None
                ),
                occurred_at.isoformat(),
                json.dumps(
                    details or {},
                    sort_keys=True,
                    default=str,
                ),
            ),
        )

    def _transition_routing(
        self,
        connection: sqlite3.Connection,
        *,
        current: RoutingRecord,
        new_status: RoutingStatus,
        event_type: str,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
        set_clauses: dict[str, Any] | None = None,
    ) -> None:
        if new_status not in ROUTING_TRANSITIONS[
            current.status
        ]:
            raise OrchestrationError(
                "Routing cannot move from "
                f"{current.status.value} to "
                f"{new_status.value}."
            )
        assignments = {
            "status": new_status.value,
        }
        assignments.update(set_clauses or {})
        set_sql = ", ".join(
            f"{column} = ?"
            for column in assignments
        )
        parameters = list(assignments.values())
        parameters.extend(
            [
                current.routing_id,
                current.status.value,
                current.version,
            ]
        )
        cursor = connection.execute(
            f"""
            UPDATE broker_saga_routings
            SET {set_sql}, version = version + 1
            WHERE routing_id = ?
              AND status = ?
              AND version = ?
            """,
            parameters,
        )
        if cursor.rowcount != 1:
            raise OrchestrationError(
                "Routing changed concurrently."
            )
        self._insert_routing_event(
            connection,
            routing_id=current.routing_id,
            event_type=event_type,
            old_status=current.status,
            new_status=new_status,
            occurred_at=occurred_at,
            details=details,
        )

    def get_routing(
        self,
        routing_id: str,
    ) -> RoutingRecord:
        with self._connect() as connection:
            row = self._load_routing_row(
                connection, routing_id
            )
        return self._row_to_routing(row)

    def list_routings(
        self,
        trade_id: str,
    ) -> list[RoutingRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM broker_saga_routings
                WHERE trade_id = ?
                ORDER BY created_at, routing_id
                """,
                (trade_id,),
            ).fetchall()
        return [
            self._row_to_routing(row)
            for row in rows
        ]

    def count_routings(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_saga_routings
                """
            ).fetchone()
        return int(row["count_value"])

    def count_routing_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_saga_routing_events
                """
            ).fetchone()
        return int(row["count_value"])

    def _validated_snapshot(
        self,
        action: SagaActionRecord,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
    ):
        validation = validate_snapshot(
            snapshot,
            evaluated_at=evaluated_at,
            maximum_age=maximum_snapshot_age,
        )
        if not validation.valid:
            raise OrchestrationError(
                validation.failure_reason
                or "Broker snapshot evidence is "
                "invalid."
            )
        if (
            snapshot.metadata.account_fingerprint
            != action.account_fingerprint
        ):
            raise OrchestrationError(
                "Broker snapshot evidence does not "
                "match the commissioned account "
                "fingerprint."
            )
        return validation

    def stage_proven_facts(
        self,
        action_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        order_observation=None,
        average_fill_price: Decimal | None = None,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> list[tuple[RoutingRecord, bool]]:
        timestamp = occurred_at or evaluated_at
        action = self.saga.get_action(action_id)
        if (
            action.attempt_count == 0
            and action.action_type
            != SagaActionType.RECONCILE_ONLY
        ):
            raise OrchestrationError(
                "Routing facts require a submitted "
                "saga action or an explicit "
                "reconcile-only action."
            )
        validation = self._validated_snapshot(
            action,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
            maximum_snapshot_age=(
                maximum_snapshot_age
            ),
        )
        trade = self.trade_store.get_trade(
            action.trade_id
        )

        self._crash(
            OrchestrationCrashPoint
            .BEFORE_ROUTING_CREATION
        )

        facts = derive_routing_facts(
            action,
            snapshot=snapshot,
            symbol=trade.symbol,
            order_observation=order_observation,
        )
        results: list[
            tuple[RoutingRecord, bool]
        ] = []
        with self._connect() as connection:
            for fact in facts:
                key = routing_idempotency_key(
                    action_id=action.action_id,
                    fact=fact,
                    trade_id=action.trade_id,
                )
                existing = connection.execute(
                    """
                    SELECT *
                    FROM broker_saga_routings
                    WHERE idempotency_key = ?
                    """,
                    (key,),
                ).fetchone()
                if existing is not None:
                    results.append(
                        (
                            self._row_to_routing(
                                existing
                            ),
                            False,
                        )
                    )
                    continue

                routing_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO
                        broker_saga_routings (
                            routing_id, action_id,
                            trade_id, strategy,
                            symbol, action_type,
                            action_status_at_routing,
                            routing_type,
                            source_proof_id,
                            source_observation_id,
                            source_snapshot_digest,
                            account_fingerprint,
                            payload_digest,
                            broker_order_id,
                            proven_quantity,
                            proven_filled_quantity,
                            proven_remaining_quantity,
                            proven_protected_quantity,
                            proven_average_price,
                            close_reason,
                            target_coordinator,
                            target_fact, status,
                            idempotency_key,
                            lifecycle_observation_key,
                            created_at,
                            claim_owner,
                            claimed_at,
                            claim_expires_at,
                            completed_at,
                            blocked_reason,
                            failure_code,
                            failure_reason,
                            version
                        )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, NULL, NULL, NULL,
                        NULL, NULL, NULL, 1
                    )
                    """,
                    (
                        routing_id,
                        action.action_id,
                        action.trade_id,
                        action.strategy,
                        trade.symbol,
                        action.action_type.value,
                        action.status.value,
                        fact.fact_type.value,
                        snapshot.metadata
                        .reconciliation_id,
                        fact.broker_order_id,
                        validation
                        .snapshot_digest,
                        action
                        .account_fingerprint,
                        action.payload_digest,
                        fact.broker_order_id,
                        _optional_decimal_text(
                            fact.proven_quantity
                        ),
                        _optional_decimal_text(
                            fact
                            .proven_filled_quantity
                        ),
                        _optional_decimal_text(
                            fact
                            .proven_remaining_quantity
                        ),
                        _optional_decimal_text(
                            fact
                            .proven_protected_quantity
                        ),
                        _optional_decimal_text(
                            average_fill_price
                        ),
                        fact.close_reason,
                        fact.target.value,
                        fact.fact_type.value,
                        RoutingStatus.READY.value,
                        key,
                        f"saga-routing:{key}",
                        timestamp.isoformat(),
                    ),
                )
                self._insert_routing_event(
                    connection,
                    routing_id=routing_id,
                    event_type="routing_staged",
                    old_status=None,
                    new_status=(
                        RoutingStatus.READY
                    ),
                    occurred_at=timestamp,
                    details={
                        "fact_type": (
                            fact.fact_type.value
                        ),
                        "idempotency_key": key,
                        "snapshot_digest": (
                            validation
                            .snapshot_digest
                        ),
                        "description": (
                            fact.description
                        ),
                    },
                )
                results.append(
                    (
                        self._row_to_routing(
                            connection.execute(
                                """
                                SELECT *
                                FROM
                                    broker_saga_routings
                                WHERE routing_id = ?
                                """,
                                (routing_id,),
                            ).fetchone()
                        ),
                        True,
                    )
                )

        self._crash(
            OrchestrationCrashPoint
            .AFTER_ROUTING_CREATION
        )
        return results

    def claim_routing(
        self,
        routing_id: str,
        *,
        now: datetime,
    ) -> tuple[RoutingRecord, bool]:
        with self._connect() as connection:
            current = self._row_to_routing(
                self._load_routing_row(
                    connection, routing_id
                )
            )
            if (
                current.status
                == RoutingStatus.CLAIMED
            ):
                expired = (
                    current.claim_expires_at
                    is not None
                    and now
                    >= current.claim_expires_at
                )
                if (
                    not expired
                    and current.claim_owner
                    == self.owner
                ):
                    return current, False
                if not expired:
                    raise OrchestrationError(
                        "An unexpired routing "
                        "claim by another owner "
                        "blocks this routing."
                    )
                raise OrchestrationError(
                    "An expired routing claim "
                    "requires explicit recovery."
                )
            if (
                current.status
                != RoutingStatus.READY
            ):
                raise OrchestrationError(
                    "Only READY routing can be "
                    "claimed."
                )
            self._transition_routing(
                connection,
                current=current,
                new_status=RoutingStatus.CLAIMED,
                event_type="routing_claimed",
                occurred_at=now,
                details={
                    "claim_owner": self.owner
                },
                set_clauses={
                    "claim_owner": self.owner,
                    "claimed_at": (
                        now.isoformat()
                    ),
                    "claim_expires_at": (
                        (
                            now + self.claim_ttl
                        ).isoformat()
                    ),
                },
            )

        self._crash(
            OrchestrationCrashPoint
            .AFTER_ROUTING_CLAIM
        )
        return self.get_routing(routing_id), True

    def _block_routing(
        self,
        routing_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> RoutingRecord:
        with self._connect() as connection:
            current = self._row_to_routing(
                self._load_routing_row(
                    connection, routing_id
                )
            )
            self._transition_routing(
                connection,
                current=current,
                new_status=RoutingStatus.BLOCKED,
                event_type="routing_blocked",
                occurred_at=now,
                details={"reason": reason},
                set_clauses={
                    "blocked_reason": reason,
                    "claim_owner": None,
                    "claimed_at": None,
                    "claim_expires_at": None,
                },
            )
        return self.get_routing(routing_id)

    def _require_claim(
        self,
        current: RoutingRecord,
        now: datetime,
    ) -> None:
        if (
            current.status
            != RoutingStatus.CLAIMED
            or current.claim_owner != self.owner
        ):
            raise OrchestrationError(
                "Routing application requires an "
                "active claim by this "
                "orchestrator."
            )
        if (
            current.claim_expires_at is None
            or now >= current.claim_expires_at
        ):
            raise OrchestrationError(
                "The routing claim has expired; "
                "explicit recovery is required."
            )

    def _revalidate_action(
        self,
        record: RoutingRecord,
    ) -> SagaActionRecord:
        action = self.saga.get_action(
            record.action_id
        )
        if (
            action.strategy != record.strategy
            or action.trade_id != record.trade_id
            or action.payload_digest
            != record.payload_digest
            or action.account_fingerprint
            != record.account_fingerprint
        ):
            raise OrchestrationError(
                "The saga action no longer matches "
                "the routing record."
            )
        return action

    def _apply_order_target(
        self,
        record: RoutingRecord,
        *,
        order_observation,
        now: datetime,
    ):
        if order_observation is None:
            raise OrchestrationError(
                "An order observation is required "
                "for this routing target."
            )
        if (
            order_observation.order_id
            != record.broker_order_id
        ):
            raise OrchestrationError(
                "The supplied order observation "
                "does not match the routing "
                "record."
            )
        observed_filled = Decimal(
            order_observation.cumulative_quantity
        )
        if (
            record.proven_filled_quantity
            is not None
            and observed_filled
            != record.proven_filled_quantity
        ):
            raise OrchestrationError(
                "The order observation no longer "
                "matches the proven fill "
                "quantities."
            )

        if (
            record.target_coordinator
            == LifecycleTarget.ENTRY
        ):
            coordinator = (
                EntryLifecycleCoordinator(
                    self.trade_store
                )
            )
            return coordinator.apply(
                trade_id=record.trade_id,
                order=order_observation,
                average_fill_price=(
                    record.proven_average_price
                ),
                observation_at=now,
                observation_key=(
                    record
                    .lifecycle_observation_key
                ),
            )
        if (
            record.target_coordinator
            == LifecycleTarget.PROTECTIVE_STOP
        ):
            coordinator = (
                ProtectiveStopLifecycleCoordinator(
                    self.trade_store
                )
            )
            return coordinator.apply(
                trade_id=record.trade_id,
                order=order_observation,
                average_fill_price=(
                    record.proven_average_price
                ),
                observation_at=now,
                observation_key=(
                    record
                    .lifecycle_observation_key
                ),
            )
        coordinator = (
            ExitOrderLifecycleCoordinator(
                self.trade_store
            )
        )
        return coordinator.apply(
            trade_id=record.trade_id,
            order=order_observation,
            average_fill_price=(
                record.proven_average_price
            ),
            close_reason=record.close_reason,
            observation_at=now,
            observation_key=(
                record.lifecycle_observation_key
            ),
        )

    def _apply_reconciliation_target(
        self,
        record: RoutingRecord,
        action: SagaActionRecord,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        now: datetime,
    ):
        validation = self._validated_snapshot(
            action,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
        )
        trade = self.trade_store.get_trade(
            record.trade_id
        )
        refreshed_facts = derive_routing_facts(
            action,
            snapshot=snapshot,
            symbol=trade.symbol,
            order_observation=None,
        )
        if not any(
            fact.fact_type == record.routing_type
            for fact in refreshed_facts
        ):
            raise OrchestrationError(
                "The fresh snapshot no longer "
                "proves the routed fact."
            )
        if (
            record.routing_type
            == RoutingFactType
            .ACTIVE_PROTECTIVE_STOP_CONFIRMED
            and trade.protective_stop_order_id
            is None
            and record.broker_order_id
        ):
            self.trade_store.attach_protective_stop(
                record.trade_id,
                broker_order_id=(
                    record.broker_order_id
                ),
                occurred_at=now,
            )
        issues = (
            self.trade_store
            .apply_startup_reconciliation(
                strategy=record.strategy,
                snapshot=snapshot,
                occurred_at=now,
            )
        )
        return {
            "reconciliation_id": (
                snapshot.metadata
                .reconciliation_id
            ),
            "snapshot_digest": (
                validation.snapshot_digest
            ),
            "issues": [
                issue.code for issue in issues
            ],
        }

    def apply_routing(
        self,
        routing_id: str,
        *,
        now: datetime,
        order_observation=None,
        snapshot: BrokerSnapshot | None = None,
        evaluated_at: datetime | None = None,
    ):
        record = self.get_routing(routing_id)
        self._require_claim(record, now)
        action = self._revalidate_action(record)

        self._crash(
            OrchestrationCrashPoint
            .BEFORE_LIFECYCLE_INVOCATION
        )

        lifecycle_result: Any = None
        try:
            if record.target_coordinator in {
                LifecycleTarget.ENTRY,
                LifecycleTarget.PROTECTIVE_STOP,
                LifecycleTarget.EXIT,
            }:
                lifecycle_result = (
                    self._apply_order_target(
                        record,
                        order_observation=(
                            order_observation
                        ),
                        now=now,
                    )
                )
            elif (
                record.target_coordinator
                == LifecycleTarget
                .PROTECTION_RECONCILIATION
            ):
                if (
                    snapshot is None
                    or evaluated_at is None
                ):
                    raise OrchestrationError(
                        "A fresh snapshot is "
                        "required to apply a "
                        "reconciliation routing."
                    )
                lifecycle_result = (
                    self
                    ._apply_reconciliation_target(
                        record,
                        action,
                        snapshot=snapshot,
                        evaluated_at=evaluated_at,
                        now=now,
                    )
                )
        except OrchestrationError as error:
            self._block_routing(
                routing_id,
                reason=str(error),
                now=now,
            )
            raise

        self._crash(
            OrchestrationCrashPoint
            .AFTER_LIFECYCLE_MUTATION
        )

        with self._connect() as connection:
            current = self._row_to_routing(
                self._load_routing_row(
                    connection, routing_id
                )
            )
            self._insert_routing_event(
                connection,
                routing_id=routing_id,
                event_type="routing_applied",
                old_status=current.status,
                new_status=RoutingStatus.APPLIED,
                occurred_at=now,
                details={
                    "target": (
                        record.target_coordinator
                        .value
                    ),
                    "fact_type": (
                        record.routing_type.value
                    ),
                },
            )
            self._crash(
                OrchestrationCrashPoint
                .AFTER_EVENT_PERSISTENCE
            )
            if (
                RoutingStatus.APPLIED
                not in ROUTING_TRANSITIONS[
                    current.status
                ]
            ):
                raise OrchestrationError(
                    "Routing cannot move from "
                    f"{current.status.value} to "
                    "applied."
                )
            cursor = connection.execute(
                """
                UPDATE broker_saga_routings
                SET status = ?, completed_at = ?,
                    version = version + 1
                WHERE routing_id = ?
                  AND status = ?
                  AND version = ?
                """,
                (
                    RoutingStatus.APPLIED.value,
                    now.isoformat(),
                    routing_id,
                    current.status.value,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise OrchestrationError(
                    "Routing changed concurrently."
                )

        self._crash(
            OrchestrationCrashPoint
            .AFTER_ROUTING_COMPLETION_BEFORE_UNLOCK
        )
        self.unlock_dependents(
            record.action_id, now=now
        )
        return (
            self.get_routing(routing_id),
            lifecycle_result,
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

    def _lifecycle_marker_exists(
        self,
        record: RoutingRecord,
    ) -> bool:
        table = _LIFECYCLE_EVENT_TABLES.get(
            record.target_coordinator
        )
        if table is None:
            return False
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT 1
                FROM {table}
                WHERE trade_id = ?
                  AND observation_key = ?
                LIMIT 1
                """,
                (
                    record.trade_id,
                    record
                    .lifecycle_observation_key,
                ),
            ).fetchone()
        return row is not None

    def recover_routing(
        self,
        routing_id: str,
        *,
        now: datetime,
    ) -> RoutingRecord:
        with self._connect() as connection:
            current = self._row_to_routing(
                self._load_routing_row(
                    connection, routing_id
                )
            )
        if (
            current.status
            != RoutingStatus.CLAIMED
        ):
            raise OrchestrationError(
                "Only claimed routing can recover "
                "from claim expiration."
            )
        if (
            current.claim_expires_at is None
            or now < current.claim_expires_at
        ):
            raise OrchestrationError(
                "The routing claim has not "
                "expired."
            )

        marker_applied = (
            self._lifecycle_marker_exists(current)
        )
        with self._connect() as connection:
            refreshed = self._row_to_routing(
                self._load_routing_row(
                    connection, routing_id
                )
            )
            if marker_applied:
                self._transition_routing(
                    connection,
                    current=refreshed,
                    new_status=(
                        RoutingStatus.APPLIED
                    ),
                    event_type=(
                        "routing_recovered_applied"
                    ),
                    occurred_at=now,
                    details={
                        "recovery": (
                            "The durable lifecycle "
                            "event already exists; "
                            "the routing is marked "
                            "applied without "
                            "re-invocation."
                        ),
                    },
                    set_clauses={
                        "completed_at": (
                            now.isoformat()
                        ),
                    },
                )
            else:
                self._transition_routing(
                    connection,
                    current=refreshed,
                    new_status=RoutingStatus.READY,
                    event_type=(
                        "routing_claim_expired_released"
                    ),
                    occurred_at=now,
                    details={
                        "previous_owner": (
                            refreshed.claim_owner
                        ),
                    },
                    set_clauses={
                        "claim_owner": None,
                        "claimed_at": None,
                        "claim_expires_at": None,
                    },
                )
        return self.get_routing(routing_id)

    def run_once(
        self,
        trade_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        now: datetime,
        order_observations: Mapping[
            str, Any
        ] | None = None,
        average_prices: Mapping[
            str, Decimal
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Performs one bounded orchestration step:
        stage facts for eligible saga actions, then
        claim and apply at most one READY routing.
        Never loops and never schedules itself.
        """
        observations = dict(
            order_observations or {}
        )
        prices = dict(average_prices or {})
        staged: list[str] = []
        for action in self.saga.list_actions(
            trade_id
        ):
            if (
                action.attempt_count == 0
                and action.action_type
                != SagaActionType.RECONCILE_ONLY
            ):
                continue
            relevant_id = (
                action.resulting_broker_order_id
                or action.source_broker_order_id
            )
            observation = observations.get(
                relevant_id
            )
            try:
                results = (
                    self.stage_proven_facts(
                        action.action_id,
                        snapshot=snapshot,
                        evaluated_at=evaluated_at,
                        order_observation=(
                            observation
                        ),
                        average_fill_price=(
                            prices.get(
                                relevant_id
                            )
                        ),
                        occurred_at=now,
                    )
                )
            except OrchestrationError:
                continue
            staged.extend(
                record.routing_id
                for record, created in results
                if created
            )

        applied_routing = None
        for record in self.list_routings(
            trade_id
        ):
            if (
                record.status
                != RoutingStatus.READY
            ):
                continue
            self.claim_routing(
                record.routing_id, now=now
            )
            observation = observations.get(
                record.broker_order_id
            )
            applied_routing, _ = (
                self.apply_routing(
                    record.routing_id,
                    now=now,
                    order_observation=observation,
                    snapshot=snapshot,
                    evaluated_at=evaluated_at,
                )
            )
            break

        return {
            "staged_routing_ids": staged,
            "applied_routing_id": (
                applied_routing.routing_id
                if applied_routing is not None
                else None
            ),
        }


def main() -> None:
    print("\nBROKER SAGA ORCHESTRATOR\n")
    print("SIMULATION ONLY")
    print(
        "Durable, exactly-once routing of proven "
        "saga facts into the existing trade "
        "lifecycle coordinators. No transport, no "
        "network, no broker client, no credential "
        "access."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
