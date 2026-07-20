from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.broker_action_saga import (
    BrokerActionSagaStore,
    SagaActionStatus,
    SagaActionType,
)
from src.broker_saga_orchestrator import (
    OrchestrationCrashPoint,
)
from src.config import load_config
from src.simulated_broker_transport import CrashPoint
from src.simulation_runner import (
    CRASH_ROTATION,
    SCENARIO_ROTATION,
    SIMULATION_BANNER,
    RunStatus,
    ScenarioKind,
    SimulationError,
    SimulationRunner,
    SimulationScenario,
    VirtualClock,
    build_scenarios,
    simulated_symbol,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


def make_runner(
    tmp_path,
    *,
    max_cycles: int = 50_000,
    stall_threshold: int = 25,
    name: str = "sim.sqlite",
):
    store = TradeStateStore(tmp_path / name)
    return SimulationRunner(
        store,
        clock=VirtualClock(),
        max_cycles=max_cycles,
        stall_threshold=stall_threshold,
    )


def run_kinds(
    tmp_path,
    kinds,
    *,
    name: str = "sim.sqlite",
    crash_point=None,
):
    """Runs one scenario per requested kind, in order."""
    runner = make_runner(tmp_path, name=name)
    report = runner.run(
        scenarios_for(kinds, crash_point=crash_point)
    )
    return runner, report


def scenarios_for(kinds, *, crash_point=None):
    return tuple(
        SimulationScenario(
            index=index,
            kind=kind,
            strategy=f"sim_strategy_{index:05d}",
            symbol=simulated_symbol(index),
            session_date=(
                date(2026, 3, 2)
                + timedelta(days=index)
            ),
            crash_point=crash_point,
        )
        for index, kind in enumerate(kinds)
    )


# ---------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------


def test_runner_requires_supplied_store(tmp_path):
    with pytest.raises(
        SimulationError,
        match="TradeStateStore is required",
    ):
        SimulationRunner(
            str(tmp_path / "sim.sqlite"),
            clock=VirtualClock(),
            max_cycles=10,
        )


def test_runner_requires_virtual_clock(tmp_path):
    store = TradeStateStore(tmp_path / "sim.sqlite")
    with pytest.raises(
        SimulationError,
        match="VirtualClock is required",
    ):
        SimulationRunner(
            store, clock=None, max_cycles=10
        )


def test_runner_requires_positive_bounds(tmp_path):
    store = TradeStateStore(tmp_path / "sim.sqlite")
    with pytest.raises(
        SimulationError, match="max_cycles"
    ):
        SimulationRunner(
            store,
            clock=VirtualClock(),
            max_cycles=0,
        )
    with pytest.raises(
        SimulationError, match="stall_threshold"
    ):
        SimulationRunner(
            store,
            clock=VirtualClock(),
            max_cycles=10,
            stall_threshold=0,
        )


def test_runner_refuses_repository_data_database(
    tmp_path,
    monkeypatch,
):
    import src.simulation_runner as module

    store = TradeStateStore(tmp_path / "sim.sqlite")
    monkeypatch.setattr(
        module,
        "_REPOSITORY_DATA_DIRECTORY",
        tmp_path.resolve(),
    )
    with pytest.raises(
        SimulationError,
        match="repository data directory",
    ):
        SimulationRunner(
            store,
            clock=VirtualClock(),
            max_cycles=10,
        )


def test_report_refuses_repository_data_path(
    tmp_path,
    monkeypatch,
):
    import src.simulation_runner as module

    runner = make_runner(tmp_path)
    scenarios = build_scenarios(
        1, include_crashes=False
    )
    monkeypatch.setattr(
        module,
        "_REPOSITORY_DATA_DIRECTORY",
        tmp_path.resolve(),
    )
    with pytest.raises(
        SimulationError,
        match="repository data directory",
    ):
        runner.run(
            scenarios,
            report_path=tmp_path / "report.json",
        )


# ---------------------------------------------------------------
# Virtual clock
# ---------------------------------------------------------------


def test_virtual_clock_only_moves_forward():
    clock = VirtualClock()
    start = clock.now
    clock.advance(timedelta(seconds=5))
    assert clock.now == start + timedelta(seconds=5)
    with pytest.raises(
        SimulationError, match="only moves forward"
    ):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(
        SimulationError, match="only moves forward"
    ):
        clock.advance(timedelta(0))


def test_virtual_clock_requires_timezone():
    from datetime import datetime

    with pytest.raises(
        SimulationError, match="timezone-aware"
    ):
        VirtualClock(datetime(2026, 3, 2, 14, 30))


def test_scenario_schedule_is_deterministic():
    first = build_scenarios(40)
    second = build_scenarios(40)
    assert first == second
    assert len({s.strategy for s in first}) == 40
    assert len({s.symbol for s in first}) == 40
    assert len({s.session_date for s in first}) == 40


def test_scenario_rotation_covers_every_kind():
    assert set(SCENARIO_ROTATION) == set(ScenarioKind)


def test_crash_rotation_covers_both_crash_families():
    points = [
        point
        for point in CRASH_ROTATION
        if point is not None
    ]
    assert any(
        isinstance(point, CrashPoint)
        for point in points
    )
    assert any(
        isinstance(point, OrchestrationCrashPoint)
        for point in points
    )


# ---------------------------------------------------------------
# Scenario end states
# ---------------------------------------------------------------


def test_every_scenario_kind_reconciles(tmp_path):
    runner, report = run_kinds(
        tmp_path, tuple(SCENARIO_ROTATION)
    )
    assert report["status"] == RunStatus.COMPLETED.value
    assert report["scenarios_executed"] == len(
        SCENARIO_ROTATION
    )
    assert (
        report["scenarios_matched"]
        == report["scenarios_executed"]
    )
    assert report["banner"] == SIMULATION_BANNER
    assert report["crash_total"] == 0
    assert report["restarts"] == 0


def test_full_exit_closes_once_with_exact_pnl(tmp_path):
    runner, report = run_kinds(
        tmp_path,
        (ScenarioKind.FULL_EXIT_FLAT_PROOF,),
    )
    scenario = report["scenarios"][0]
    assert scenario["matched"]
    assert scenario["actual"] == {
        "trade_status": TradeStatus.CLOSED.value,
        "realized_pnl": "20",
        "close_reason": "profit_target",
    }
    with sqlite3.connect(
        tmp_path / "sim.sqlite"
    ) as connection:
        closes = connection.execute(
            """
            SELECT COUNT(*) FROM trade_events
            WHERE new_status = 'closed'
            """
        ).fetchone()[0]
    assert closes == 1


def test_partial_exit_routes_once_without_duplicate_pnl(
    tmp_path,
):
    runner, report = run_kinds(
        tmp_path, (ScenarioKind.PARTIAL_EXIT,)
    )
    scenario = report["scenarios"][0]
    assert scenario["actual"]["realized_pnl"] == "8"
    assert scenario["actual"]["exited_quantity"] == 4
    with sqlite3.connect(
        tmp_path / "sim.sqlite"
    ) as connection:
        events = connection.execute(
            "SELECT COUNT(*) FROM exit_recovery_events"
        ).fetchone()[0]
    assert events == 1


def test_acknowledgment_never_resolves_operator_state(
    tmp_path,
):
    runner, report = run_kinds(
        tmp_path,
        (
            ScenarioKind.OPERATOR_NO_EFFECT_CONFIRMED,
        ),
    )
    scenario = report["scenarios"][0]
    assert (
        scenario["actual"]["resolution_decision"]
        == "no_effect_confirmed"
    )
    assert (
        scenario["actual"]["action_status"]
        == SagaActionStatus.CONFIRMED_NOT_PERFORMED
        .value
    )
    assert scenario["actual"][
        "broker_action_halted"
    ]


def test_duplicate_ref_id_fails_closed(tmp_path):
    runner, report = run_kinds(
        tmp_path, (ScenarioKind.DUPLICATE_REF_ID,)
    )
    scenario = report["scenarios"][0]
    assert (
        scenario["actual"]["action_status"]
        == SagaActionStatus.TERMINAL_FAILURE.value
    )
    assert scenario["actual"][
        "broker_action_halted"
    ]


def test_replacement_stop_blocked_until_proof(tmp_path):
    runner, report = run_kinds(
        tmp_path,
        (ScenarioKind.STOP_REPLACEMENT_GAP,),
    )
    scenario = report["scenarios"][0]
    assert scenario["matched"]
    assert scenario["actual"][
        "replacement_blocked"
    ]
    assert (
        scenario["actual"]["trade_status"]
        == TradeStatus.STOP_RECOVERY_REQUIRED.value
    )


def test_claim_expiration_recovers_under_virtual_clock(
    tmp_path,
):
    runner, report = run_kinds(
        tmp_path, (ScenarioKind.CLAIM_EXPIRATION,)
    )
    assert report["status"] == RunStatus.COMPLETED.value
    assert (
        report["scenarios"][0]["actual"][
            "trade_status"
        ]
        == TradeStatus.OPEN_PROTECTED.value
    )


# ---------------------------------------------------------------
# Bounds, stalling, and deadlock
# ---------------------------------------------------------------


def test_cycle_limit_terminates_the_run(tmp_path):
    runner = make_runner(tmp_path, max_cycles=5)
    report = runner.run(
        build_scenarios(6, include_crashes=False)
    )
    assert (
        report["status"]
        == RunStatus.TERMINATED_CYCLE_LIMIT.value
    )
    assert report["cycles_executed"] == 6
    assert "cycle limit" in (
        report["termination_detail"]
    )


def test_stalled_work_terminates_fail_closed(tmp_path):
    runner = make_runner(
        tmp_path, stall_threshold=3
    )
    scenario = build_scenarios(
        1, include_crashes=False
    )[0]
    runner._scenario_index = 0
    runner._trade_id = ""
    runner._authorization_id = None
    runner._primary_action_id = None
    # Leave one action READY and then idle: durable
    # state stops changing while work remains.
    runner._protected_stop(scenario)
    with pytest.raises(RuntimeError):
        for _ in range(20):
            runner._cycle("idle")
    assert runner.stalled_detections
    assert (
        runner.status
        == RunStatus.TERMINATED_STALLED
    )
    detection = runner.stalled_detections[0]
    assert detection.kind == "stalled_work"
    assert detection.object_ids


def test_orphaned_claim_is_detected_as_deadlock(
    tmp_path,
):
    runner = make_runner(
        tmp_path, stall_threshold=3
    )
    scenario = build_scenarios(
        1, include_crashes=False
    )[0]
    runner._scenario_index = 0
    action = runner._protected_stop(scenario)
    runner.saga.claim_action(
        action.action_id,
        owner="vanished-owner",
        ttl=timedelta(seconds=5),
        occurred_at=runner.clock.tick(),
    )
    runner.clock.advance(timedelta(minutes=10))
    with pytest.raises(RuntimeError):
        for _ in range(20):
            runner._cycle("idle")
    assert runner.deadlock_detections
    assert (
        runner.deadlock_detections[0].kind
        == "orphaned_claim"
    )


def test_dependency_cycle_is_detected(tmp_path):
    runner = make_runner(
        tmp_path, stall_threshold=2
    )
    scenario = build_scenarios(
        1, include_crashes=False
    )[0]
    first = runner._protected_stop(scenario)
    second, _ = runner.saga.plan_action(
        strategy=scenario.strategy,
        trade_id=runner._trade_id,
        action_type=(
            SagaActionType.CANCEL_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"cycle-b:{runner._trade_id}"
        ),
        payload=runner._stop_payload(scenario),
        reconciliation_id="sim-recon-cycle",
        snapshot_digest="d" * 64,
        account_fingerprint=runner.fingerprint,
        expected_precondition={},
        expected_postcondition={},
        authorization_id=runner._authorization_id,
        source_broker_order_id="fake-order-x",
        depends_on=(first.action_id,),
        occurred_at=runner.clock.tick(),
    )
    # Close the loop directly in the durable graph.
    with sqlite3.connect(
        tmp_path / "sim.sqlite"
    ) as connection:
        connection.execute(
            """
            INSERT INTO
                broker_saga_action_dependencies (
                    action_id,
                    depends_on_action_id,
                    created_at
                )
            VALUES (?, ?, ?)
            """,
            (
                first.action_id,
                second.action_id,
                runner.clock.now.isoformat(),
            ),
        )

    with pytest.raises(RuntimeError):
        for _ in range(20):
            runner._cycle("idle")
    assert runner.deadlock_detections
    assert (
        runner.deadlock_detections[0].kind
        == "dependency_cycle"
    )


def test_reconciliation_mismatch_terminates(
    tmp_path,
    monkeypatch,
):
    runner = make_runner(tmp_path)
    original = runner._scenario_no_trade_day

    def corrupted(scenario):
        expected = original(scenario)
        expected["realized_pnl"] = "999"
        return expected

    monkeypatch.setattr(
        runner, "_scenario_no_trade_day", corrupted
    )
    report = runner.run(
        scenarios_for((ScenarioKind.NO_TRADE_DAY,))
    )
    assert (
        report["status"]
        == RunStatus
        .TERMINATED_RECONCILIATION_MISMATCH.value
    )
    assert not report["scenarios"][0]["matched"]
    assert report["scenarios"][0]["mismatches"]


def test_safety_violation_terminates_fail_closed(
    tmp_path,
    monkeypatch,
):
    runner = make_runner(tmp_path)

    def violated(scenario):
        raise SimulationError(
            "A full exit fill must not close a "
            "trade before flat-position proof."
        )

    monkeypatch.setattr(
        runner, "_scenario_no_trade_day", violated
    )
    report = runner.run(
        scenarios_for((ScenarioKind.NO_TRADE_DAY,))
    )
    assert (
        report["status"]
        == RunStatus.TERMINATED_SAFETY.value
    )
    assert "flat-position proof" in (
        report["termination_detail"]
    )


def test_declined_recovery_is_reported_not_swallowed(
    tmp_path,
):
    """
    Crash recovery may legitimately decline a step the
    durable state has already moved past. Those
    declines must be visible in the report.
    """
    runner = make_runner(tmp_path)
    report = runner.run(build_scenarios(17))
    assert "resume_declines" in report
    for decline in report["resume_declines"]:
        assert decline["label"]
        assert decline["error"]
        assert decline["detail"]


# ---------------------------------------------------------------
# Report shape and determinism
# ---------------------------------------------------------------


def test_report_is_written_as_json(tmp_path):
    runner = make_runner(tmp_path)
    destination = tmp_path / "report.json"
    report = runner.run(
        build_scenarios(3, include_crashes=False),
        report_path=destination,
    )
    written = json.loads(
        destination.read_text(encoding="utf-8")
    )
    assert written["status"] == report["status"]
    assert written["content_digest"] == (
        report["content_digest"]
    )
    for key in (
        "scenarios_executed",
        "cycles_executed",
        "crashes_injected",
        "restarts",
        "stalled_detections",
        "deadlock_detections",
        "database",
        "runtime_seconds",
        "safety",
    ):
        assert key in written


def test_identical_configuration_replays_identically(
    tmp_path,
):
    first_runner = make_runner(
        tmp_path, name="a.sqlite"
    )
    first = first_runner.run(build_scenarios(17))
    second_runner = make_runner(
        tmp_path, name="b.sqlite"
    )
    second = second_runner.run(build_scenarios(17))

    assert (
        first["content_digest"]
        == second["content_digest"]
    )
    for key in (
        "status",
        "scenarios_executed",
        "scenarios_matched",
        "cycles_executed",
        "crashes_injected",
        "crash_total",
        "restarts",
        "transport",
        "final_virtual_time",
    ):
        assert first[key] == second[key], key
    assert (
        first["database"]["rows_after"]
        == second["database"]["rows_after"]
    )


def test_replayed_routing_adds_no_rows(tmp_path):
    """
    Re-deriving and re-applying every proven fact after
    the day is over must be a no-op: routing is
    exactly-once from the application's durable
    perspective.
    """
    runner = make_runner(tmp_path)
    runner.run(
        build_scenarios(8, include_crashes=False)
    )
    before = runner.table_counts()

    replayed = 0
    for trade_id in {
        row[0]
        for row in runner._reader().execute(
            "SELECT trade_id FROM trades"
        )
    }:
        for action in runner.saga.list_actions(
            trade_id
        ):
            runner._trade_id = trade_id
            runner._route_once(action.action_id)
            replayed += 1

    assert replayed > 0
    after = runner.table_counts()
    for table in (
        "trades",
        "trade_events",
        "broker_saga_actions",
        "broker_saga_routings",
        "exit_recovery_events",
        "entry_recovery_events",
        "protective_stop_recovery_events",
    ):
        assert after[table] == before[table], table


# ---------------------------------------------------------------
# Configuration invariants
# ---------------------------------------------------------------


def test_strategy_enabled_remains_false():
    assert load_config()["strategy"]["enabled"] is False


def test_risk_configuration_is_unchanged():
    assert load_config()["risk"] == {
        "risk_per_trade_usd": 15,
        "maximum_position_usd": 3000,
        "maximum_open_positions": 1,
        "maximum_entries_per_day": 2,
        "daily_loss_limit_usd": 45,
        "weekly_loss_limit_usd": 120,
        "total_drawdown_halt_usd": 300,
        "margin_allowed": False,
        "short_selling_allowed": False,
        "averaging_down_allowed": False,
    }


def test_repository_data_guard_attempts_remain_zero(
    repository_data_database_guard,
):
    assert (
        repository_data_database_guard.attempts == []
    )


# ---------------------------------------------------------------
# Static safety guards
# ---------------------------------------------------------------


def runner_source() -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / "simulation_runner.py"
    ).read_text(encoding="utf-8")


def test_runner_has_no_live_or_nondeterministic_capability():
    lowered = runner_source().lower()
    for forbidden in (
        "robinhood",
        "call_tool",
        "review_equity_order",
        "place_equity_order",
        "cancel_equity_order",
        "clientsession",
        "mcp",
        "http",
        "requests",
        "urllib",
        "socket",
        "websocket",
        "aiohttp",
        "random",
        "secrets",
        "uuid",
        "sleep(",
        "datetime.now",
        "utc_now",
        "time.time",
        "os.environ",
        "getenv",
        "rh_tokens",
        "while true",
    ):
        assert forbidden not in lowered, forbidden


def test_runner_declares_simulation_only():
    assert SIMULATION_BANNER in runner_source()


def test_runner_never_reads_wall_clock_for_decisions(
    tmp_path,
):
    """
    Every simulated timestamp comes from the virtual
    clock, so a run advanced only by the script lands
    on an exactly predictable virtual time.
    """
    runner = make_runner(tmp_path)
    report = runner.run(
        build_scenarios(4, include_crashes=False)
    )
    assert report["final_virtual_time"].startswith(
        "2026-03-"
    )
    assert report["clock_advances"] > 0
    replay = make_runner(tmp_path, name="c.sqlite")
    other = replay.run(
        build_scenarios(4, include_crashes=False)
    )
    assert (
        other["final_virtual_time"]
        == report["final_virtual_time"]
    )
