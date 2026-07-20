from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from .broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionStatus,
        SagaActionType,
    )
    from .broker_saga_executor import (
        ExecutorLimits,
        SagaExecutor,
    )
    from .broker_saga_orchestrator import (
        OrchestrationCrashPoint,
        OrchestrationError,
        RoutingFactType,
        RoutingStatus,
        SagaOrchestrator,
    )
    from .operator_resolution import (
        OperatorResolutionStore,
    )
    from .simulated_broker_transport import (
        CrashInjector,
        CrashPoint,
        FakeBrokerState,
        SimulatedBrokerTransport,
        SimulatedCrash,
        TransportBehavior,
    )
    from .trade_state import (
        ExitIntentType,
        TradeStateError,
        TradeStateStore,
        TradeStatus,
    )
except ImportError:
    from broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionStatus,
        SagaActionType,
    )
    from broker_saga_executor import (
        ExecutorLimits,
        SagaExecutor,
    )
    from broker_saga_orchestrator import (
        OrchestrationCrashPoint,
        OrchestrationError,
        RoutingFactType,
        RoutingStatus,
        SagaOrchestrator,
    )
    from operator_resolution import (
        OperatorResolutionStore,
    )
    from simulated_broker_transport import (
        CrashInjector,
        CrashPoint,
        FakeBrokerState,
        SimulatedBrokerTransport,
        SimulatedCrash,
        TransportBehavior,
    )
    from trade_state import (
        ExitIntentType,
        TradeStateError,
        TradeStateStore,
        TradeStatus,
    )


SIMULATION_BANNER = "SIMULATION ONLY"

_REPOSITORY_DATA_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "data"
)

DEFAULT_STALL_THRESHOLD = 25

SIMULATION_EPOCH = datetime(
    2026, 3, 2, 14, 30, tzinfo=timezone.utc
)

_TICK = timedelta(seconds=1)


class SimulationError(RuntimeError):
    """Raised when the bounded simulation cannot proceed safely."""


class RunStatus(str, Enum):
    COMPLETED = "completed"
    TERMINATED_STALLED = "terminated_stalled"
    TERMINATED_CYCLE_LIMIT = "terminated_cycle_limit"
    TERMINATED_RECONCILIATION_MISMATCH = (
        "terminated_reconciliation_mismatch"
    )
    TERMINATED_SAFETY = "terminated_safety"


class ScenarioKind(str, Enum):
    NO_TRADE_DAY = "no_trade_day"
    ENTRY_FULL_FILL = "entry_full_fill"
    ENTRY_PARTIAL_FILL = "entry_partial_fill"
    INITIAL_PROTECTIVE_STOP = "initial_protective_stop"
    STOP_REPLACEMENT_GAP = "stop_replacement_gap"
    STOP_PARTIAL_FILL = "stop_partial_fill"
    PARTIAL_EXIT = "partial_exit"
    FULL_EXIT_FLAT_PROOF = "full_exit_flat_proof"
    TIMEOUT_BEFORE_RESPONSE = "timeout_before_response"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    RETRYABLE_REJECTION = "retryable_rejection"
    TERMINAL_REJECTION = "terminal_rejection"
    DUPLICATE_REF_ID = "duplicate_ref_id"
    CLAIM_EXPIRATION = "claim_expiration"
    OPERATOR_EFFECT_CONFIRMED = "operator_effect_confirmed"
    OPERATOR_NO_EFFECT_CONFIRMED = (
        "operator_no_effect_confirmed"
    )
    FLATTEN_POSITION = "flatten_position"


SCENARIO_ROTATION = (
    ScenarioKind.INITIAL_PROTECTIVE_STOP,
    ScenarioKind.ENTRY_FULL_FILL,
    ScenarioKind.ENTRY_PARTIAL_FILL,
    ScenarioKind.TIMEOUT_AFTER_EFFECT,
    ScenarioKind.PARTIAL_EXIT,
    ScenarioKind.TIMEOUT_BEFORE_RESPONSE,
    ScenarioKind.FULL_EXIT_FLAT_PROOF,
    ScenarioKind.RETRYABLE_REJECTION,
    ScenarioKind.STOP_PARTIAL_FILL,
    ScenarioKind.CLAIM_EXPIRATION,
    ScenarioKind.OPERATOR_EFFECT_CONFIRMED,
    ScenarioKind.STOP_REPLACEMENT_GAP,
    ScenarioKind.TERMINAL_REJECTION,
    ScenarioKind.OPERATOR_NO_EFFECT_CONFIRMED,
    ScenarioKind.FLATTEN_POSITION,
    ScenarioKind.DUPLICATE_REF_ID,
    ScenarioKind.NO_TRADE_DAY,
)

CRASH_ROTATION = (
    None,
    CrashPoint.AFTER_CLAIM,
    OrchestrationCrashPoint.AFTER_LIFECYCLE_MUTATION,
    CrashPoint.AFTER_SUBMISSION_INTENT,
    OrchestrationCrashPoint.BEFORE_LIFECYCLE_INVOCATION,
    CrashPoint.AFTER_RESPONSE_BEFORE_STATUS,
    OrchestrationCrashPoint.AFTER_EVENT_PERSISTENCE,
    None,
    CrashPoint.AFTER_ATTEMPT_CREATION,
    OrchestrationCrashPoint
    .AFTER_ROUTING_COMPLETION_BEFORE_UNLOCK,
)

def simulated_symbol(index: int) -> str:
    """
    One distinct symbol per scenario.

    The fake broker state is shared for the whole run
    (a broker does not forget between client restarts),
    so scenarios must not collide on symbol positions or
    open orders.
    """
    return f"SIM{index:05d}"


def _digest_of(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


class VirtualClock:
    """
    Deterministic monotonic simulation clock.

    The simulation reads time only from this object. It
    never consults the wall clock and never sleeps.
    """

    def __init__(
        self,
        start: datetime = SIMULATION_EPOCH,
    ):
        if start.tzinfo is None:
            raise SimulationError(
                "The virtual clock start must be "
                "timezone-aware."
            )
        self._now = start
        self.advances = 0

    @property
    def now(self) -> datetime:
        return self._now

    def advance(
        self,
        delta: timedelta = _TICK,
    ) -> datetime:
        if delta <= timedelta(0):
            raise SimulationError(
                "The virtual clock only moves forward."
            )
        self._now = self._now + delta
        self.advances += 1
        return self._now

    def tick(self) -> datetime:
        return self.advance(_TICK)


@dataclass(frozen=True)
class SimulationScenario:
    index: int
    kind: ScenarioKind
    strategy: str
    symbol: str
    session_date: date
    crash_point: Any = None


@dataclass
class ScenarioResult:
    index: int
    kind: str
    strategy: str
    symbol: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    matched: bool
    cycles: int
    crashes: int
    restarts: int
    mismatches: list[str] = field(
        default_factory=list
    )


@dataclass
class StalledDetection:
    scenario_index: int
    cycle: int
    kind: str
    detail: str
    object_ids: list[str]


def build_scenarios(
    count: int,
    *,
    include_crashes: bool = True,
    start_session: date = date(2026, 3, 2),
) -> tuple[SimulationScenario, ...]:
    """
    Builds a deterministic scenario schedule.

    The schedule depends only on ``count`` and the
    supplied parameters — never on entropy, wall-clock
    time, or process state.
    """
    if count <= 0:
        raise SimulationError(
            "A simulation needs at least one scenario."
        )

    scenarios = []
    for index in range(count):
        kind = SCENARIO_ROTATION[
            index % len(SCENARIO_ROTATION)
        ]
        crash_point = (
            CRASH_ROTATION[
                index % len(CRASH_ROTATION)
            ]
            if include_crashes
            else None
        )
        scenarios.append(
            SimulationScenario(
                index=index,
                kind=kind,
                strategy=f"sim_strategy_{index:05d}",
                symbol=simulated_symbol(index),
                session_date=(
                    start_session
                    + timedelta(days=index)
                ),
                crash_point=crash_point,
            )
        )
    return tuple(scenarios)


class SimulationRunner:
    """
    SIMULATION ONLY bounded soak harness.

    Drives the durable saga, the simulated executor,
    the lifecycle orchestrator, and the operator
    resolution workflow through deterministic scripted
    trading-day scenarios under a virtual clock, with
    scripted crash and restart injection.

    The runner owns no transport of its own beyond the
    deterministic fake, performs no network access,
    reads no credentials, never touches a production
    database, and terminates after a bounded number of
    cycles.
    """

    def __init__(
        self,
        trade_store: TradeStateStore,
        *,
        clock: VirtualClock,
        max_cycles: int,
        stall_threshold: int = DEFAULT_STALL_THRESHOLD,
        account_number: str = "SIMULATED-SOAK-0001",
    ):
        if not isinstance(
            trade_store, TradeStateStore
        ):
            raise SimulationError(
                "An explicitly supplied "
                "TradeStateStore is required."
            )
        if not isinstance(clock, VirtualClock):
            raise SimulationError(
                "An explicit VirtualClock is required."
            )
        if max_cycles <= 0:
            raise SimulationError(
                "max_cycles must be a positive bound."
            )
        if stall_threshold <= 0:
            raise SimulationError(
                "stall_threshold must be positive."
            )

        db_path = Path(trade_store.db_path).resolve()
        try:
            db_path.relative_to(
                _REPOSITORY_DATA_DIRECTORY
            )
        except ValueError:
            pass
        else:
            raise SimulationError(
                "The simulation refuses any database "
                "inside the repository data directory."
            )

        self.db_path = db_path
        self.clock = clock
        self.max_cycles = max_cycles
        self.stall_threshold = stall_threshold
        self.limits = ExecutorLimits(
            claim_ttl=timedelta(seconds=30),
            max_attempts=4,
            max_confirmation_polls=4,
            max_concurrent_claims=1,
        )

        self.transport = SimulatedBrokerTransport(
            FakeBrokerState(
                account_number=account_number
            )
        )
        self.fingerprint = self.transport.state.fingerprint

        self.cycles = 0
        self.restart_count = 0
        self.crash_counts: dict[str, int] = {}
        self.stalled_detections: list[
            StalledDetection
        ] = []
        self.deadlock_detections: list[
            StalledDetection
        ] = []
        self.resume_declines: list[dict[str, Any]] = []
        self._reader_connection: (
            sqlite3.Connection | None
        ) = None
        self._table_names: tuple[str, ...] | None = None
        self.status = RunStatus.COMPLETED
        self.termination_detail: str | None = None

        self._digest = ""
        self._digest_repeats = 0
        self._scenario_index = -1
        self._scenario_cycles = 0
        self._scenario_crashes = 0
        self._scenario_restarts = 0
        self._trade_id = ""
        self._authorization_id: str | None = None
        self._primary_action_id: str | None = None
        self._resolution_id: str | None = None
        self._replacement_blocked = False

        self._attach_stores(trade_store)

    # -- construction and restart ---------------------------------

    def _attach_stores(
        self,
        trade_store: TradeStateStore,
    ) -> None:
        self.trade_store = trade_store
        self.saga = BrokerActionSagaStore(trade_store)
        self.orchestrator = SagaOrchestrator(
            self.saga, owner="sim-orchestrator"
        )
        self.resolutions = OperatorResolutionStore(
            self.saga
        )
        self.executor = SagaExecutor(
            self.saga,
            self.transport,
            owner="sim-executor",
            limits=self.limits,
        )

    def restart(self) -> None:
        """
        Models a process restart: every in-memory
        component is discarded and rebuilt from the
        same durable database. The fake broker state
        survives, because a broker does not forget when
        a client process dies.
        """
        self.restart_count += 1
        self._scenario_restarts += 1
        self._attach_stores(
            TradeStateStore(self.db_path)
        )

    # -- read-only metrics access ---------------------------------

    def _reader(self) -> sqlite3.Connection:
        """
        One long-lived read-only connection for metrics
        and progress digests. The stores keep their own
        write connections; under WAL this reader always
        observes committed state.
        """
        if self._reader_connection is None:
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            self._reader_connection = connection
        return self._reader_connection

    def _tables(self) -> tuple[str, ...]:
        if self._table_names is None:
            self._table_names = tuple(
                sorted(
                    row[0]
                    for row in self._reader().execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                          AND name NOT LIKE 'sqlite_%'
                        """
                    )
                )
            )
        return self._table_names

    def close(self) -> None:
        if self._reader_connection is not None:
            self._reader_connection.close()
            self._reader_connection = None

    # -- cycle accounting -----------------------------------------

    def _progress_digest(self) -> str:
        connection = self._reader()
        tables = self._tables()
        parts = []
        for table in tables:
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            parts.append(f"{table}={count}")
        for table, column in (
            ("broker_saga_actions", "status"),
            ("broker_saga_routings", "status"),
            ("operator_resolutions", "decision"),
            ("trades", "status"),
        ):
            if table not in tables:
                continue
            rows = connection.execute(
                f"""
                SELECT {column}, COUNT(*)
                FROM {table}
                GROUP BY {column}
                ORDER BY {column}
                """
            ).fetchall()
            parts.extend(
                f"{table}.{row[0]}={row[1]}"
                for row in rows
            )
        versions = connection.execute(
            """
            SELECT COALESCE(SUM(version), 0)
            FROM trades
            """
        ).fetchone()[0]
        parts.append(f"trade_versions={versions}")
        return _digest_of("|".join(parts))

    def _outstanding_work(
        self,
        trade_id: str | None = None,
    ) -> list[str]:
        """
        Durable work that is neither terminal nor
        parked. Scoped to one trade for end-of-day
        reconciliation, global for stall detection.
        """
        clause = ""
        parameters: tuple[str, ...] = ()
        if trade_id is not None:
            clause = "AND trade_id = ?"
            parameters = (trade_id,)

        connection = self._reader()
        active = connection.execute(
            f"""
            SELECT action_id
            FROM broker_saga_actions
            WHERE status IN (
                'ready', 'claimed',
                'submission_intent_recorded',
                'awaiting_broker_confirmation',
                'confirmed_not_performed',
                'retryable_failure'
            )
            {clause}
            ORDER BY action_id
            """,
            parameters,
        ).fetchall()
        routings = connection.execute(
            f"""
            SELECT routing_id
            FROM broker_saga_routings
            WHERE status IN ('ready', 'claimed')
            {clause}
            ORDER BY routing_id
            """,
            parameters,
        ).fetchall()
        return [
            row["action_id"] for row in active
        ] + [
            row["routing_id"] for row in routings
        ]

    def _blocked_claims(self) -> list[str]:
        moment = self.clock.now.isoformat()
        rows = self._reader().execute(
            """
            SELECT action_id
            FROM broker_saga_actions
            WHERE status = 'claimed'
              AND claim_expires_at IS NOT NULL
              AND claim_expires_at <= ?
            ORDER BY action_id
            """,
            (moment,),
        ).fetchall()
        return [row["action_id"] for row in rows]

    def _dependency_cycles(self) -> list[str]:
        edges = self._reader().execute(
            """
            SELECT action_id, depends_on_action_id
            FROM broker_saga_action_dependencies
            ORDER BY action_id,
                     depends_on_action_id
            """
        ).fetchall()
        graph: dict[str, list[str]] = {}
        for child, parent in edges:
            graph.setdefault(child, []).append(parent)

        visiting: set[str] = set()
        done: set[str] = set()
        found: list[str] = []

        def walk(node: str, trail: list[str]) -> None:
            if node in done:
                return
            if node in visiting:
                found.extend(trail + [node])
                return
            visiting.add(node)
            for parent in graph.get(node, ()):
                walk(parent, trail + [node])
            visiting.discard(node)
            done.add(node)

        for node in sorted(graph):
            walk(node, [])
        return sorted(set(found))

    def _cycle(self, label: str) -> None:
        self.cycles += 1
        self._scenario_cycles += 1
        if self.cycles > self.max_cycles:
            self.status = (
                RunStatus.TERMINATED_CYCLE_LIMIT
            )
            self.termination_detail = (
                "The bounded cycle limit of "
                f"{self.max_cycles} was reached at "
                f"{label}."
            )
            raise _RunTerminated(
                self.termination_detail
            )

        digest = self._progress_digest()
        if digest == self._digest:
            self._digest_repeats += 1
        else:
            self._digest = digest
            self._digest_repeats = 0

        if self._digest_repeats < self.stall_threshold:
            return

        cycle_stalls = self._dependency_cycles()
        if cycle_stalls:
            detection = StalledDetection(
                scenario_index=self._scenario_index,
                cycle=self.cycles,
                kind="dependency_cycle",
                detail=(
                    "A circular saga dependency "
                    "prevents any further progress."
                ),
                object_ids=cycle_stalls,
            )
            self.deadlock_detections.append(detection)
            self.status = RunStatus.TERMINATED_STALLED
            self.termination_detail = detection.detail
            raise _RunTerminated(detection.detail)

        orphaned = self._blocked_claims()
        if orphaned:
            detection = StalledDetection(
                scenario_index=self._scenario_index,
                cycle=self.cycles,
                kind="orphaned_claim",
                detail=(
                    "Work is blocked on an expired "
                    "claim that no owner recovered."
                ),
                object_ids=orphaned,
            )
            self.deadlock_detections.append(detection)
            self.status = RunStatus.TERMINATED_STALLED
            self.termination_detail = detection.detail
            raise _RunTerminated(detection.detail)

        outstanding = self._outstanding_work()
        if outstanding:
            detection = StalledDetection(
                scenario_index=self._scenario_index,
                cycle=self.cycles,
                kind="stalled_work",
                detail=(
                    "Durable state stopped changing "
                    "while work remained outstanding."
                ),
                object_ids=outstanding,
            )
            self.stalled_detections.append(detection)
            self.status = RunStatus.TERMINATED_STALLED
            self.termination_detail = detection.detail
            raise _RunTerminated(detection.detail)

        self._digest_repeats = 0

    def _record_decline(
        self,
        label: str,
        error: Exception,
    ) -> None:
        """
        Records a declined recovery or resumed step.

        Recovery legitimately declines when durable
        state has already moved past the step (for
        example a claim another recovery already
        released). Every decline is reported rather
        than silently swallowed.
        """
        self.resume_declines.append(
            {
                "scenario_index": self._scenario_index,
                "label": label,
                "error": type(error).__name__,
                "detail": str(error),
            }
        )

    def _record_crash(self, point: Any) -> None:
        name = getattr(point, "value", str(point))
        self.crash_counts[name] = (
            self.crash_counts.get(name, 0) + 1
        )
        self._scenario_crashes += 1

    # -- scripted helpers -----------------------------------------

    def _arm(self, scenario: SimulationScenario) -> None:
        """Arms the scripted crash point for a scenario."""
        point = scenario.crash_point
        if point is None:
            self.executor.crash_injector = None
            self.orchestrator.crash_injector = None
            return
        injector = CrashInjector((point,))
        if isinstance(point, CrashPoint):
            self.executor.crash_injector = injector
            self.orchestrator.crash_injector = None
        else:
            self.orchestrator.crash_injector = injector
            self.executor.crash_injector = None

    def _guarded(
        self,
        scenario: SimulationScenario,
        label: str,
        operation,
    ):
        """
        Runs one bounded operation. A scripted crash is
        recorded, the process is restarted, the durable
        recovery entry points run, and the operation is
        resumed exactly once.

        A resumed operation may legitimately decline
        because recovery parked the work in a
        fail-closed state. That decline is recorded in
        the report rather than swallowed.
        """
        self._cycle(label)
        try:
            return operation()
        except SimulatedCrash:
            self._record_crash(scenario.crash_point)
            self.executor.crash_injector = None
            self.orchestrator.crash_injector = None
            self.restart()
            self._recover_after_restart(scenario)

        self._cycle(f"{label}:resumed")
        try:
            return operation()
        except TradeStateError as error:
            self._record_decline(
                f"{label}:resumed", error
            )
            return None

    def _recover_after_restart(
        self,
        scenario: SimulationScenario,
    ) -> None:
        moment = self.clock.advance(
            timedelta(seconds=90)
        )
        for action in self.saga.list_actions(
            self._trade_id
        ):
            if action.status in {
                SagaActionStatus.CLAIMED,
                SagaActionStatus
                .SUBMISSION_INTENT_RECORDED,
            }:
                self._cycle("recover_claim")
                try:
                    self.executor.recover_claim(
                        action.action_id, now=moment
                    )
                except BrokerActionSagaError as error:
                    self._record_decline(
                        "recover_claim", error
                    )
            refreshed = self.saga.get_action(
                action.action_id
            )
            if (
                refreshed.status
                == SagaActionStatus.OUTCOME_UNKNOWN
            ):
                self._cycle("reconcile_unknown")
                try:
                    self.executor.reconcile_unknown(
                        action.action_id,
                        now=self.clock.tick(),
                    )
                except BrokerActionSagaError as error:
                    self._record_decline(
                        "reconcile_unknown", error
                    )

        for record in self.orchestrator.list_routings(
            self._trade_id
        ):
            if record.status != RoutingStatus.CLAIMED:
                continue
            self._cycle("recover_routing")
            try:
                self.orchestrator.recover_routing(
                    record.routing_id,
                    now=self.clock.tick(),
                )
            except OrchestrationError as error:
                self._record_decline(
                    "recover_routing", error
                )

    def _authorize(
        self,
        scenario: SimulationScenario,
        purpose: str = "protect_filled_entry",
    ) -> str:
        trade = self.trade_store.get_trade(
            self._trade_id
        )
        moment = self.clock.tick()
        authorization, _ = (
            self.trade_store
            .authorize_broker_action_plan(
                strategy=scenario.strategy,
                trade_id=trade.trade_id,
                purpose=purpose,
                plan_digest=_digest_of(
                    f"plan:{trade.trade_id}"
                ),
                source_trade_version=trade.version,
                reconciliation_id=(
                    f"sim-recon-{scenario.index}"
                ),
                snapshot_digest=_digest_of(
                    f"snapshot:{trade.trade_id}"
                ),
                account_fingerprint=self.fingerprint,
                authorized_at=moment,
                expires_at=moment
                + timedelta(hours=6),
            )
        )
        return authorization.authorization_id

    def _plan(
        self,
        scenario: SimulationScenario,
        *,
        action_type: SagaActionType,
        key: str,
        payload: dict[str, Any],
        postcondition: dict[str, Any],
        **extra: Any,
    ):
        action, _ = self.saga.plan_action(
            strategy=scenario.strategy,
            trade_id=self._trade_id,
            action_type=action_type,
            logical_action_key=(
                f"{key}:{self._trade_id}"
            ),
            payload=payload,
            reconciliation_id=(
                f"sim-recon-{scenario.index}"
            ),
            snapshot_digest=_digest_of(
                f"snapshot:{self._trade_id}"
            ),
            account_fingerprint=self.fingerprint,
            expected_precondition={},
            expected_postcondition=postcondition,
            authorization_id=self._authorization_id,
            occurred_at=self.clock.tick(),
            **extra,
        )
        self._cycle("plan_action")
        if self._primary_action_id is None:
            self._primary_action_id = action.action_id
        return self.saga.evaluate_readiness(
            action.action_id,
            occurred_at=self.clock.tick(),
        )

    def _stop_payload(
        self,
        scenario: SimulationScenario,
        *,
        quantity: str = "10",
        stop_price: str = "99",
    ) -> dict[str, Any]:
        return {
            "symbol": scenario.symbol,
            "side": "sell",
            "type": "stop_market",
            "quantity": quantity,
            "stop_price": stop_price,
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        }

    def _reserve(
        self,
        scenario: SimulationScenario,
        *,
        quantity: int = 10,
    ):
        trade = self.trade_store.reserve_entry(
            strategy=scenario.strategy,
            symbol=scenario.symbol,
            session_date=scenario.session_date,
            quantity=quantity,
            planned_entry=Decimal("100"),
            initial_stop=Decimal("99"),
            target_price=Decimal("102"),
            occurred_at=self.clock.tick(),
        )
        self._trade_id = trade.trade_id
        self._cycle("reserve_entry")
        return trade

    def _fill_entry(
        self,
        scenario: SimulationScenario,
        *,
        quantity: int = 10,
    ):
        order = self.transport.state.add_order(
            symbol=scenario.symbol,
            side="buy",
            order_type="limit",
            quantity=Decimal(quantity),
            limit_price=Decimal("100"),
        )
        self.trade_store.attach_entry_order(
            self._trade_id,
            broker_order_id=order.order_id,
            occurred_at=self.clock.tick(),
        )
        self.transport.state.fill_order(
            order.order_id, Decimal(quantity)
        )
        self.trade_store.mark_entry_filled(
            self._trade_id,
            filled_quantity=quantity,
            average_entry_price=Decimal("100"),
            occurred_at=self.clock.tick(),
        )
        self._cycle("fill_entry")
        return order

    def _execute(
        self,
        scenario: SimulationScenario,
        action_id: str,
    ):
        # Only READY work is executable. After a crash
        # recovery may have parked this action
        # elsewhere; the executor must not be forced.
        if (
            self.saga.get_action(action_id).status
            != SagaActionStatus.READY
        ):
            return None
        return self._guarded(
            scenario,
            "executor_execute",
            lambda: self.executor.execute_ready_action(
                action_id, now=self.clock.tick()
            ),
        )

    def _confirm(
        self,
        scenario: SimulationScenario,
        action_id: str,
    ):
        action = self.saga.get_action(action_id)
        if (
            action.status
            != SagaActionStatus
            .AWAITING_BROKER_CONFIRMATION
        ):
            return action
        return self._guarded(
            scenario,
            "executor_confirm",
            lambda: self.executor.try_confirm(
                action_id, now=self.clock.tick()
            ),
        )

    def _route(
        self,
        scenario: SimulationScenario,
        action_id: str,
        *,
        order_id: str | None = None,
        average_price: Decimal | None = None,
        only: RoutingFactType | None = None,
    ) -> None:
        """
        Stages, claims, and applies routing for one
        action. The whole sequence is the crash-guarded
        unit: staging is idempotent, so a resumed run
        re-derives the same records and skips whatever
        recovery already marked applied.
        """
        self._guarded(
            scenario,
            "orchestrator_route",
            lambda: self._route_once(
                action_id,
                order_id=order_id,
                average_price=average_price,
                only=only,
            ),
        )

    def _route_once(
        self,
        action_id: str,
        *,
        order_id: str | None = None,
        average_price: Decimal | None = None,
        only: RoutingFactType | None = None,
    ) -> None:
        action = self.saga.get_action(action_id)
        if action.attempt_count == 0 and (
            action.action_type
            != SagaActionType.RECONCILE_ONLY
        ):
            return

        moment = self.clock.tick()
        snapshot = self.transport.snapshot(now=moment)
        observation = (
            self.transport.state.as_equity_order(
                order_id
            )
            if order_id
            else None
        )
        self._cycle("orchestrator_stage")
        try:
            staged = (
                self.orchestrator
                .stage_proven_facts(
                    action_id,
                    snapshot=snapshot,
                    evaluated_at=moment,
                    order_observation=observation,
                    average_fill_price=average_price,
                    occurred_at=moment,
                )
            )
        except OrchestrationError:
            return

        for record, _created in staged:
            if (
                only is not None
                and record.routing_type != only
            ):
                continue
            current = self.orchestrator.get_routing(
                record.routing_id
            )
            if current.status != RoutingStatus.READY:
                continue
            self._cycle("orchestrator_claim")
            self.orchestrator.claim_routing(
                record.routing_id,
                now=self.clock.tick(),
            )
            apply_moment = self.clock.tick()
            apply_snapshot = self.transport.snapshot(
                now=apply_moment
            )
            apply_observation = (
                self.transport.state.as_equity_order(
                    current.broker_order_id
                )
                if current.broker_order_id
                and current.broker_order_id
                in self.transport.state.orders
                else None
            )
            self._cycle("orchestrator_apply")
            try:
                self.orchestrator.apply_routing(
                    record.routing_id,
                    now=apply_moment,
                    order_observation=apply_observation,
                    snapshot=apply_snapshot,
                    evaluated_at=apply_moment,
                )
            except OrchestrationError:
                continue

    def _flat_proof(
        self,
        scenario: SimulationScenario,
    ) -> None:
        moment = self.clock.tick()
        snapshot = self.transport.snapshot(now=moment)
        self._cycle("flat_reconciliation")
        self.trade_store.apply_startup_reconciliation(
            strategy=scenario.strategy,
            snapshot=snapshot,
            occurred_at=moment,
        )

    # -- scenarios ------------------------------------------------

    def _run_scenario(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        handler = getattr(
            self, f"_scenario_{scenario.kind.value}"
        )
        return handler(scenario)

    def _scenario_no_trade_day(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        self._reserve(scenario)
        self._cycle("cancel_unfilled")
        self.trade_store.cancel_unfilled_trade(
            self._trade_id,
            reason="No qualifying signal.",
            occurred_at=self.clock.tick(),
        )
        return {
            "trade_status": TradeStatus.CANCELLED.value,
            "realized_pnl": "0",
        }

    def _protected_stop(
        self,
        scenario: SimulationScenario,
        *,
        behavior: TransportBehavior | None = None,
    ):
        self._reserve(scenario)
        self._fill_entry(scenario)
        self._authorization_id = self._authorize(
            scenario
        )
        action = self._plan(
            scenario,
            action_type=(
                SagaActionType
                .PLACE_INITIAL_PROTECTIVE_STOP
            ),
            key="initial-stop",
            payload=self._stop_payload(scenario),
            postcondition={
                "active_stop_present": True,
                "stop_quantity": "10",
                "no_conflicting_sell_order": True,
            },
            side="sell",
            order_type="stop_market",
            trigger_type="stop",
            requested_quantity=10,
            stop_price=Decimal("99"),
            time_in_force="gfd",
            market_hours="regular_hours",
        )
        if behavior is not None:
            self.transport.script_submit(
                action.ref_id, behavior
            )
        return action

    def _scenario_initial_protective_stop(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(scenario)
        self._execute(scenario, action.action_id)
        self._confirm(scenario, action.action_id)
        self._route(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.OPEN_PROTECTED.value
            ),
            "realized_pnl": "0",
        }

    def _scenario_entry_full_fill(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        self._reserve(scenario)
        self._authorization_id = self._authorize(
            scenario, purpose="entry"
        )
        action = self._plan(
            scenario,
            action_type=SagaActionType.PLACE_ENTRY,
            key="entry",
            payload={
                "symbol": scenario.symbol,
                "side": "buy",
                "type": "limit",
                "quantity": "10",
                "limit_price": "100",
                "time_in_force": "gfd",
                "market_hours": "regular_hours",
            },
            postcondition={
                "position_quantity": "10",
            },
            requested_quantity=10,
            limit_price=Decimal("100"),
        )
        result = self._execute(
            scenario, action.action_id
        )
        refreshed = self.saga.get_action(
            action.action_id
        )
        order_id = refreshed.resulting_broker_order_id
        if order_id is None:
            return {
                "trade_status": (
                    TradeStatus.RESERVED.value
                ),
                "realized_pnl": "0",
            }
        self.trade_store.attach_entry_order(
            self._trade_id,
            broker_order_id=order_id,
            occurred_at=self.clock.tick(),
        )
        self.transport.state.fill_order(
            order_id, Decimal("10")
        )
        self._confirm(scenario, action.action_id)
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("100"),
        )
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
        }

    def _scenario_entry_partial_fill(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        self._reserve(scenario)
        self._authorization_id = self._authorize(
            scenario, purpose="entry"
        )
        action = self._plan(
            scenario,
            action_type=SagaActionType.PLACE_ENTRY,
            key="entry",
            payload={
                "symbol": scenario.symbol,
                "side": "buy",
                "type": "limit",
                "quantity": "10",
                "limit_price": "100",
                "time_in_force": "gfd",
                "market_hours": "regular_hours",
            },
            postcondition={
                "position_quantity": "10",
            },
            requested_quantity=10,
            limit_price=Decimal("100"),
        )
        self._execute(scenario, action.action_id)
        refreshed = self.saga.get_action(
            action.action_id
        )
        order_id = refreshed.resulting_broker_order_id
        if order_id is None:
            return {
                "trade_status": (
                    TradeStatus.RESERVED.value
                ),
                "realized_pnl": "0",
            }
        self.trade_store.attach_entry_order(
            self._trade_id,
            broker_order_id=order_id,
            occurred_at=self.clock.tick(),
        )
        self.transport.state.fill_order(
            order_id, Decimal("6")
        )
        self.transport.state.cancel_order_state(
            order_id
        )
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("100"),
        )
        return {
            "trade_status": (
                TradeStatus.RECOVERY_REQUIRED.value
            ),
            "realized_pnl": "0",
            "filled_quantity": 6,
        }

    def _scenario_stop_replacement_gap(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(scenario)
        self._execute(scenario, action.action_id)
        self._confirm(scenario, action.action_id)
        self._route(scenario, action.action_id)

        refreshed = self.saga.get_action(
            action.action_id
        )
        stop_order_id = (
            refreshed.resulting_broker_order_id
        )
        cancel_action = self._plan(
            scenario,
            action_type=(
                SagaActionType.CANCEL_PROTECTIVE_STOP
            ),
            key="cancel-stop",
            payload=self._stop_payload(scenario),
            postcondition={
                "source_order_inactive": True,
            },
            source_broker_order_id=stop_order_id,
        )
        replacement, _ = self.saga.plan_action(
            strategy=scenario.strategy,
            trade_id=self._trade_id,
            action_type=(
                SagaActionType
                .PLACE_REPLACEMENT_PROTECTIVE_STOP
            ),
            logical_action_key=(
                f"replacement-stop:{self._trade_id}"
            ),
            payload=self._stop_payload(
                scenario, stop_price="100"
            ),
            reconciliation_id=(
                f"sim-recon-{scenario.index}"
            ),
            snapshot_digest=_digest_of(
                f"snapshot:{self._trade_id}"
            ),
            account_fingerprint=self.fingerprint,
            expected_precondition={},
            expected_postcondition={
                "active_stop_present": True,
                "stop_quantity": "10",
            },
            authorization_id=self._authorization_id,
            requested_quantity=10,
            stop_price=Decimal("100"),
            depends_on=(cancel_action.action_id,),
            occurred_at=self.clock.tick(),
        )
        self._cycle("evaluate_replacement")
        blocked = self.saga.evaluate_readiness(
            replacement.action_id,
            occurred_at=self.clock.tick(),
        )
        if (
            blocked.status
            != SagaActionStatus.BLOCKED
        ):
            raise SimulationError(
                "A replacement stop must stay "
                "blocked until its cancellation is "
                "proven."
            )
        self._replacement_blocked = True

        # Prove the cancellation, route the loss of
        # protection, then restore exact protection
        # with the replacement the dependency guarded.
        self._execute(
            scenario, cancel_action.action_id
        )
        self._confirm(
            scenario, cancel_action.action_id
        )
        self._route(
            scenario,
            cancel_action.action_id,
            order_id=stop_order_id,
        )
        self._cycle("unblock_replacement")
        unblocked = self.saga.evaluate_readiness(
            replacement.action_id,
            occurred_at=self.clock.tick(),
        )
        if unblocked.status == SagaActionStatus.READY:
            self._execute(
                scenario, replacement.action_id
            )
            self._confirm(
                scenario, replacement.action_id
            )
            self._route(
                scenario, replacement.action_id
            )
        # Protection was genuinely lost when the old
        # stop was cancelled. The lifecycle keeps a
        # durable recovery requirement rather than
        # silently declaring the position protected.
        return {
            "trade_status": (
                TradeStatus.STOP_RECOVERY_REQUIRED
                .value
            ),
            "realized_pnl": "0",
            "replacement_blocked": True,
        }

    def _scenario_stop_partial_fill(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(scenario)
        self._execute(scenario, action.action_id)
        self._confirm(scenario, action.action_id)
        self._route(scenario, action.action_id)

        refreshed = self.saga.get_action(
            action.action_id
        )
        order_id = refreshed.resulting_broker_order_id
        if order_id is None:
            trade = self.trade_store.get_trade(
                self._trade_id
            )
            return {
                "trade_status": trade.status.value,
                "realized_pnl": "0",
            }
        self.transport.state.positions[
            scenario.symbol
        ] = Decimal("10")
        self.transport.state.fill_order(
            order_id, Decimal("4")
        )
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("99"),
        )
        return {
            "trade_status": (
                TradeStatus.STOP_RECOVERY_REQUIRED
                .value
            ),
            "realized_pnl": "0",
        }

    def _exit_setup(
        self,
        scenario: SimulationScenario,
    ):
        self._reserve(scenario)
        self._fill_entry(scenario)
        self._authorization_id = self._authorize(
            scenario, purpose="exit_position"
        )
        self.transport.state.positions[
            scenario.symbol
        ] = Decimal("10")
        action = self._plan(
            scenario,
            action_type=SagaActionType.PLACE_EXIT,
            key="exit",
            payload={
                "symbol": scenario.symbol,
                "side": "sell",
                "type": "limit",
                "quantity": "10",
                "limit_price": "102",
                "time_in_force": "gfd",
                "market_hours": "regular_hours",
            },
            postcondition={"order_present": True},
            close_reason="profit_target",
            requested_quantity=10,
            limit_price=Decimal("102"),
        )
        self._execute(scenario, action.action_id)

        # The exit order is the one the simulated
        # executor actually submitted — link the durable
        # exit intent to that order, never to a
        # separately invented one.
        submitted = self.saga.get_action(
            action.action_id
        ).resulting_broker_order_id
        if submitted is not None:
            self.trade_store.begin_exit(
                self._trade_id,
                broker_order_id=submitted,
                requested_quantity=10,
                close_reason="profit_target",
                intent_type=(
                    ExitIntentType.PROFIT_TARGET
                ),
                intended_price=Decimal("102"),
                occurred_at=self.clock.tick(),
            )
            self._cycle("begin_exit")
        return action, submitted

    def _scenario_partial_exit(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action, order_id = self._exit_setup(scenario)
        if order_id is None:
            return {
                "trade_status": (
                    TradeStatus.PROTECTION_PENDING
                    .value
                ),
                "realized_pnl": "0",
            }
        self.transport.state.fill_order(
            order_id, Decimal("4")
        )
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("102"),
        )
        return {
            "trade_status": (
                TradeStatus.EXIT_PARTIALLY_FILLED
                .value
            ),
            "realized_pnl": "8",
            "exited_quantity": 4,
        }

    def _scenario_full_exit_flat_proof(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action, order_id = self._exit_setup(scenario)
        if order_id is None:
            return {
                "trade_status": (
                    TradeStatus.PROTECTION_PENDING
                    .value
                ),
                "realized_pnl": "0",
            }
        self.transport.state.fill_order(
            order_id, Decimal("10")
        )
        # The fill fact alone must never close the
        # trade — closure requires the separate,
        # independently proven flat-position fact.
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("102"),
            only=RoutingFactType.FULL_FILL_CONFIRMED,
        )
        mid = self.trade_store.get_trade(
            self._trade_id
        )
        if mid.status == TradeStatus.CLOSED:
            raise SimulationError(
                "A full exit fill must not close a "
                "trade before flat-position proof."
            )
        self._route(
            scenario,
            action.action_id,
            order_id=order_id,
            average_price=Decimal("102"),
            only=(
                RoutingFactType.POSITION_FLAT_CONFIRMED
            ),
        )
        return {
            "trade_status": TradeStatus.CLOSED.value,
            "realized_pnl": "20",
            "close_reason": "profit_target",
        }

    def _scenario_timeout_before_response(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(
            scenario,
            behavior=(
                TransportBehavior
                .TIMEOUT_BEFORE_RESPONSE
            ),
        )
        self._execute(scenario, action.action_id)
        current = self.saga.get_action(
            action.action_id
        )
        if (
            current.status
            == SagaActionStatus.OUTCOME_UNKNOWN
        ):
            self._cycle("reconcile_unknown")
            self.executor.reconcile_unknown(
                action.action_id,
                now=self.clock.tick(),
            )
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
            "action_status": (
                SagaActionStatus
                .CONFIRMED_NOT_PERFORMED.value
            ),
            "broker_action_halted": True,
        }

    def _scenario_timeout_after_effect(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(
            scenario,
            behavior=(
                TransportBehavior.TIMEOUT_AFTER_EFFECT
            ),
        )
        self._execute(scenario, action.action_id)
        current = self.saga.get_action(
            action.action_id
        )
        if (
            current.status
            == SagaActionStatus.OUTCOME_UNKNOWN
        ):
            self._cycle("reconcile_unknown")
            self.executor.reconcile_unknown(
                action.action_id,
                now=self.clock.tick(),
            )
        self._confirm(scenario, action.action_id)
        self._route(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.OPEN_PROTECTED.value
            ),
            "realized_pnl": "0",
            "action_status": (
                SagaActionStatus.CONFIRMED_SUCCEEDED
                .value
            ),
        }

    def _scenario_retryable_rejection(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(
            scenario,
            behavior=(
                TransportBehavior.REJECTED_RETRYABLE
            ),
        )
        self._execute(scenario, action.action_id)
        current = self.saga.get_action(
            action.action_id
        )
        if (
            current.status
            == SagaActionStatus.RETRYABLE_FAILURE
        ):
            self.transport.script_submit(
                action.ref_id,
                TransportBehavior.ACKNOWLEDGED,
            )
            self._cycle("maybe_retry")
            self.executor.maybe_retry(
                action.action_id,
                now=self.clock.tick(),
            )
            self._execute(scenario, action.action_id)
            self._confirm(scenario, action.action_id)
            self._route(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.OPEN_PROTECTED.value
            ),
            "realized_pnl": "0",
            "attempt_count": 2,
        }

    def _scenario_terminal_rejection(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(
            scenario,
            behavior=(
                TransportBehavior.REJECTED_TERMINAL
            ),
        )
        self._execute(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
            "action_status": (
                SagaActionStatus.TERMINAL_FAILURE.value
            ),
        }

    def _scenario_duplicate_ref_id(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(scenario)
        self._cycle("preexisting_submission")
        self.transport.submit_order(
            self._stop_payload(
                scenario, stop_price="55"
            ),
            ref_id=action.ref_id,
        )
        self._execute(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
            "action_status": (
                SagaActionStatus.TERMINAL_FAILURE.value
            ),
            "broker_action_halted": True,
        }

    def _scenario_claim_expiration(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        action = self._protected_stop(scenario)
        if (
            self.saga.get_action(
                action.action_id
            ).status
            == SagaActionStatus.READY
        ):
            self._cycle("claim_action")
            self.saga.claim_action(
                action.action_id,
                owner="sim-abandoned-owner",
                ttl=timedelta(seconds=15),
                occurred_at=self.clock.tick(),
            )
            self.clock.advance(timedelta(minutes=5))
            self._cycle("recover_expired_claim")
            self.executor.recover_claim(
                action.action_id,
                now=self.clock.tick(),
            )
        self._execute(scenario, action.action_id)
        self._confirm(scenario, action.action_id)
        self._route(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.OPEN_PROTECTED.value
            ),
            "realized_pnl": "0",
        }

    def _operator_flow(
        self,
        scenario: SimulationScenario,
        *,
        behavior: TransportBehavior,
        effect_present: bool,
    ) -> dict[str, Any]:
        action = self._protected_stop(
            scenario, behavior=behavior
        )
        self._execute(scenario, action.action_id)
        current = self.saga.get_action(
            action.action_id
        )
        if (
            current.status
            != SagaActionStatus.OUTCOME_UNKNOWN
        ):
            trade = self.trade_store.get_trade(
                self._trade_id
            )
            return {
                "trade_status": trade.status.value,
                "realized_pnl": "0",
            }

        self._cycle("require_operator")
        self.saga.require_operator(
            action.action_id,
            reason="Ambiguous simulated outcome.",
            occurred_at=self.clock.tick(),
        )
        self._cycle("open_resolution")
        record, _created = self.resolutions.open_resolution(
            action.action_id,
            required_evidence_type=(
                "fresh_broker_order_observation"
            ),
            occurred_at=self.clock.tick(),
        )
        self._resolution_id = record.resolution_id
        self._cycle("acknowledge_operator")
        self.resolutions.acknowledge(
            record.resolution_id,
            operator_label="sim-operator",
            note="Reviewed during simulation.",
            occurred_at=self.clock.tick(),
        )
        acknowledged = self.saga.get_action(
            action.action_id
        )
        if (
            acknowledged.status
            != SagaActionStatus.OPERATOR_REQUIRED
        ):
            raise SimulationError(
                "Operator acknowledgment must never "
                "change a saga outcome."
            )

        observation: dict[str, Any] = {
            "effect_present": effect_present,
            "ref_id": action.ref_id,
            "symbol": scenario.symbol,
        }
        if effect_present:
            observed = (
                self.transport.lookup_order_by_ref(
                    action.ref_id
                )
            )
            observation.update(
                {
                    "broker_order_id": observed,
                    "side": "sell",
                    "type": "stop_market",
                    "quantity": "10",
                    "stop_price": "99",
                }
            )

        moment = self.clock.tick()
        self._cycle("submit_evidence")
        self.resolutions.submit_evidence(
            record.resolution_id,
            strategy=scenario.strategy,
            snapshot=self.transport.snapshot(
                now=moment
            ),
            evaluated_at=moment,
            observation=observation,
            occurred_at=moment,
        )
        if effect_present:
            self._confirm(scenario, action.action_id)
            self._route(scenario, action.action_id)
            return {
                "trade_status": (
                    TradeStatus.OPEN_PROTECTED.value
                ),
                "realized_pnl": "0",
                "action_status": (
                    SagaActionStatus
                    .CONFIRMED_SUCCEEDED.value
                ),
                "resolution_decision": (
                    "effect_confirmed"
                ),
            }
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
            "action_status": (
                SagaActionStatus
                .CONFIRMED_NOT_PERFORMED.value
            ),
            "resolution_decision": (
                "no_effect_confirmed"
            ),
            "broker_action_halted": True,
        }

    def _scenario_operator_effect_confirmed(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        return self._operator_flow(
            scenario,
            behavior=(
                TransportBehavior.TIMEOUT_AFTER_EFFECT
            ),
            effect_present=True,
        )

    def _scenario_operator_no_effect_confirmed(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        return self._operator_flow(
            scenario,
            behavior=(
                TransportBehavior
                .TIMEOUT_BEFORE_RESPONSE
            ),
            effect_present=False,
        )

    def _scenario_flatten_position(
        self,
        scenario: SimulationScenario,
    ) -> dict[str, Any]:
        self._reserve(scenario)
        self._fill_entry(scenario)
        self._authorization_id = self._authorize(
            scenario, purpose="exit_position"
        )
        self.transport.state.positions[
            scenario.symbol
        ] = Decimal("10")
        action = self._plan(
            scenario,
            action_type=(
                SagaActionType.FLATTEN_POSITION
            ),
            key="flatten",
            payload={
                "symbol": scenario.symbol,
                "side": "sell",
                "type": "limit",
                "quantity": "10",
                "limit_price": "100",
                "time_in_force": "gfd",
                "market_hours": "regular_hours",
            },
            postcondition={"order_present": True},
            close_reason="operator_exit",
            requested_quantity=10,
            limit_price=Decimal("100"),
        )
        self._execute(scenario, action.action_id)
        refreshed = self.saga.get_action(
            action.action_id
        )
        order_id = refreshed.resulting_broker_order_id
        if order_id is not None:
            self.transport.state.fill_order(
                order_id, Decimal("10")
            )
            self._confirm(scenario, action.action_id)
        return {
            "trade_status": (
                TradeStatus.PROTECTION_PENDING.value
            ),
            "realized_pnl": "0",
        }

    # -- end-of-day reconciliation --------------------------------

    def _end_of_day(
        self,
        scenario: SimulationScenario,
        expected: dict[str, Any],
    ) -> ScenarioResult:
        trade = self.trade_store.get_trade(
            self._trade_id
        )
        halted = self.saga.is_broker_action_halted(
            scenario.strategy
        )
        actual: dict[str, Any] = {
            "trade_status": trade.status.value,
            "realized_pnl": str(
                trade.cumulative_realized_pnl
            ),
        }
        if "filled_quantity" in expected:
            actual["filled_quantity"] = (
                trade.filled_quantity
            )
        if "exited_quantity" in expected:
            actual["exited_quantity"] = (
                trade.cumulative_exited_quantity
            )
        if "close_reason" in expected:
            actual["close_reason"] = (
                trade.close_reason
            )
        if "broker_action_halted" in expected:
            actual["broker_action_halted"] = halted

        # Independently observed, never echoed back
        # from the scenario's own expectation.
        if self._primary_action_id is not None:
            observed = self.saga.get_action(
                self._primary_action_id
            )
            if "action_status" in expected:
                actual["action_status"] = (
                    observed.status.value
                )
            if "attempt_count" in expected:
                actual["attempt_count"] = (
                    observed.attempt_count
                )
        if (
            self._resolution_id is not None
            and "resolution_decision" in expected
        ):
            actual["resolution_decision"] = (
                self.resolutions.get_resolution(
                    self._resolution_id
                ).decision.value
            )
        if "replacement_blocked" in expected:
            actual["replacement_blocked"] = (
                self._replacement_blocked
            )

        crashed = self._scenario_crashes > 0
        if not crashed:
            mismatches = [
                f"{key}: expected {expected[key]!r}, "
                f"observed {actual.get(key)!r}"
                for key in sorted(expected)
                if actual.get(key) != expected[key]
            ]
        else:
            # A crash may legitimately leave a scenario
            # short of its clean end state, parked
            # fail-closed. What it must never do is
            # duplicate or overshoot financial state.
            mismatches = []
            expected_pnl = Decimal(
                expected["realized_pnl"]
            )
            observed_pnl = (
                trade.cumulative_realized_pnl
            )
            if observed_pnl > expected_pnl:
                mismatches.append(
                    "realized P&L overshot the clean "
                    f"expectation: {observed_pnl} > "
                    f"{expected_pnl}"
                )
            if observed_pnl not in {
                expected_pnl,
                Decimal("0"),
            }:
                mismatches.append(
                    "realized P&L is neither the "
                    "clean expectation nor zero: "
                    f"{observed_pnl}"
                )
            if (
                actual["trade_status"]
                != expected["trade_status"]
                and not halted
                and not self.trade_store
                .is_entry_halted(scenario.strategy)
            ):
                mismatches.append(
                    "a crash left the trade at "
                    f"{actual['trade_status']!r} "
                    "instead of "
                    f"{expected['trade_status']!r} "
                    "with no durable halt to explain it"
                )

        unexpired = [
            action.action_id
            for action in self.saga.list_actions(
                self._trade_id
            )
            if action.status
            == SagaActionStatus.CLAIMED
            and action.claim_expires_at is not None
            and action.claim_expires_at
            > self.clock.now
        ]
        if unexpired:
            mismatches.append(
                "unexpired claims remain: "
                + ", ".join(sorted(unexpired))
            )

        if (
            trade.status
            not in {
                TradeStatus.CLOSED,
                TradeStatus.CANCELLED,
            }
            and not halted
            and not self.trade_store.is_entry_halted(
                scenario.strategy
            )
            and self._outstanding_work(
                self._trade_id
            )
        ):
            mismatches.append(
                "unfinished work remains without a "
                "durable halt to explain it"
            )

        return ScenarioResult(
            index=scenario.index,
            kind=scenario.kind.value,
            strategy=scenario.strategy,
            symbol=scenario.symbol,
            expected=expected,
            actual=actual,
            matched=not mismatches,
            cycles=self._scenario_cycles,
            crashes=self._scenario_crashes,
            restarts=self._scenario_restarts,
            mismatches=mismatches,
        )

    # -- metrics and report ---------------------------------------

    def table_counts(self) -> dict[str, int]:
        connection = self._reader()
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in self._tables()
        }

    def database_bytes(self) -> int:
        return self.db_path.stat().st_size

    def content_digest(self) -> str:
        """Deterministic digest of durable simulation state."""
        parts: list[str] = []
        connection = self._reader()
        for query in (
            """
            SELECT strategy, symbol, status,
                   quantity, filled_quantity,
                   cumulative_realized_pnl,
                   cumulative_exited_quantity,
                   close_reason, version
            FROM trades
            ORDER BY strategy, symbol, session_date
            """,
            """
            SELECT strategy, action_type, status,
                   payload_digest, attempt_count,
                   requested_quantity, close_reason
            FROM broker_saga_actions
            ORDER BY strategy, action_type,
                     payload_digest, status
            """,
            """
            SELECT strategy, routing_type, status,
                   target_coordinator,
                   proven_filled_quantity
            FROM broker_saga_routings
            ORDER BY strategy, routing_type,
                     target_coordinator, status,
                     proven_filled_quantity
            """,
        ):
            parts.extend(
                "|".join(
                    str(value) for value in tuple(row)
                )
                for row in connection.execute(
                    query
                ).fetchall()
            )
        return _digest_of("\n".join(parts))

    def run(
        self,
        scenarios: tuple[SimulationScenario, ...],
        *,
        report_path: Path | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        counts_before = self.table_counts()
        bytes_before = self.database_bytes()
        results: list[ScenarioResult] = []

        try:
            for scenario in scenarios:
                self._scenario_index = scenario.index
                self._scenario_cycles = 0
                self._scenario_crashes = 0
                self._scenario_restarts = 0
                self._trade_id = ""
                self._authorization_id = None
                self._primary_action_id = None
                self._resolution_id = None
                self._replacement_blocked = False
                self._arm(scenario)

                expected = self._run_scenario(scenario)
                result = self._end_of_day(
                    scenario, expected
                )
                results.append(result)
                if not result.matched:
                    self.status = (
                        RunStatus
                        .TERMINATED_RECONCILIATION_MISMATCH
                    )
                    self.termination_detail = (
                        "Scenario "
                        f"{scenario.index} "
                        f"({scenario.kind.value}) failed "
                        "end-of-day reconciliation: "
                        + "; ".join(result.mismatches)
                    )
                    break
                self.clock.advance(timedelta(hours=18))
        except _RunTerminated:
            pass
        except SimulationError as error:
            # A scenario asserted a safety invariant
            # that the stack violated. Terminate the
            # run fail-closed and report it.
            self.status = RunStatus.TERMINATED_SAFETY
            self.termination_detail = str(error)

        counts_after = self.table_counts()
        report = {
            "banner": SIMULATION_BANNER,
            "status": self.status.value,
            "termination_detail": (
                self.termination_detail
            ),
            "scenarios_planned": len(scenarios),
            "scenarios_executed": len(results),
            "scenarios_matched": sum(
                1
                for result in results
                if result.matched
            ),
            "cycles_executed": self.cycles,
            "max_cycles": self.max_cycles,
            "clock_advances": self.clock.advances,
            "final_virtual_time": (
                self.clock.now.isoformat()
            ),
            "crashes_injected": dict(
                sorted(self.crash_counts.items())
            ),
            "crash_total": sum(
                self.crash_counts.values()
            ),
            "restarts": self.restart_count,
            "stalled_detections": [
                {
                    "scenario_index": (
                        item.scenario_index
                    ),
                    "cycle": item.cycle,
                    "kind": item.kind,
                    "detail": item.detail,
                    "object_ids": item.object_ids,
                }
                for item in self.stalled_detections
            ],
            "deadlock_detections": [
                {
                    "scenario_index": (
                        item.scenario_index
                    ),
                    "cycle": item.cycle,
                    "kind": item.kind,
                    "detail": item.detail,
                    "object_ids": item.object_ids,
                }
                for item in self.deadlock_detections
            ],
            "resume_declines": list(
                self.resume_declines
            ),
            "transport": {
                "submit_calls": (
                    self.transport.submit_calls
                ),
                "cancel_calls": (
                    self.transport.cancel_calls
                ),
                "review_calls": (
                    self.transport.review_calls
                ),
                "snapshot_calls": (
                    self.transport.snapshot_calls
                ),
            },
            "database": {
                "rows_before": counts_before,
                "rows_after": counts_after,
                "row_growth": {
                    table: counts_after.get(table, 0)
                    - counts_before.get(table, 0)
                    for table in sorted(counts_after)
                },
                "bytes_before": bytes_before,
                "bytes_after": self.database_bytes(),
            },
            "content_digest": self.content_digest(),
            "scenarios": [
                {
                    "index": result.index,
                    "kind": result.kind,
                    "strategy": result.strategy,
                    "symbol": result.symbol,
                    "expected": result.expected,
                    "actual": result.actual,
                    "matched": result.matched,
                    "mismatches": result.mismatches,
                    "cycles": result.cycles,
                    "crashes": result.crashes,
                    "restarts": result.restarts,
                }
                for result in results
            ],
            "safety": {
                "live_broker_transport": False,
                "network_access": False,
                "credential_access": False,
                "production_database_access": False,
                "wall_clock_decisions": False,
            },
            "runtime_seconds": round(
                time.monotonic() - started, 3
            ),
        }

        if report_path is not None:
            path = Path(report_path).resolve()
            try:
                path.relative_to(
                    _REPOSITORY_DATA_DIRECTORY
                )
            except ValueError:
                pass
            else:
                raise SimulationError(
                    "The simulation refuses to write a "
                    "report into the repository data "
                    "directory."
                )
            path.write_text(
                json.dumps(
                    report, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )

        return report


class _RunTerminated(RuntimeError):
    """Internal signal used to unwind a terminated run."""


def main() -> None:
    print("\nBOUNDED SIMULATION RUNNER\n")
    print(SIMULATION_BANNER)
    print(
        "Deterministic scenarios, a virtual clock, "
        "scripted crash and restart injection, "
        "stalled-work and deadlock detection, and "
        "end-of-day reconciliation over the simulated "
        "saga stack. No network, no credentials, no "
        "production database, no live transport."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
