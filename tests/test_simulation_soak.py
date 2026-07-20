from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from src.broker_saga_orchestrator import (
    OrchestrationCrashPoint,
)
from src.simulated_broker_transport import CrashPoint
from src.simulation_runner import (
    CRASH_ROTATION,
    RunStatus,
    SimulationRunner,
    VirtualClock,
    build_scenarios,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SOAK_SCENARIOS = 204
REPLAY_SCENARIOS = 51


@pytest.fixture(scope="module")
def soak(tmp_path_factory):
    """
    One bounded soak run, shared by every assertion in
    this module so the cycles are executed once.
    """
    directory = tmp_path_factory.mktemp("soak")
    store = TradeStateStore(directory / "soak.sqlite")
    runner = SimulationRunner(
        store,
        clock=VirtualClock(),
        max_cycles=100_000,
    )
    report = runner.run(
        build_scenarios(SOAK_SCENARIOS),
        report_path=directory / "soak-report.json",
    )
    yield runner, report
    runner.close()


# ---------------------------------------------------------------
# Scale and completion
# ---------------------------------------------------------------


def test_soak_completes_thousands_of_cycles(soak):
    _runner, report = soak
    assert (
        report["status"] == RunStatus.COMPLETED.value
    ), report["termination_detail"]
    assert report["cycles_executed"] >= 2000
    assert (
        report["scenarios_executed"]
        == SOAK_SCENARIOS
    )
    assert (
        report["scenarios_matched"]
        == SOAK_SCENARIOS
    )
    assert (
        report["cycles_executed"]
        < report["max_cycles"]
    )


def test_soak_exercises_every_scripted_crash_point(soak):
    _runner, report = soak
    scripted = {
        point.value
        for point in CRASH_ROTATION
        if point is not None
    }
    assert (
        set(report["crashes_injected"]) == scripted
    )
    assert report["crash_total"] >= len(scripted)
    assert report["restarts"] == report["crash_total"]
    assert any(
        isinstance(point, CrashPoint)
        for point in CRASH_ROTATION
        if point is not None
    )
    assert any(
        isinstance(point, OrchestrationCrashPoint)
        for point in CRASH_ROTATION
        if point is not None
    )


def test_soak_reports_no_stall_or_deadlock(soak):
    _runner, report = soak
    assert report["stalled_detections"] == []
    assert report["deadlock_detections"] == []


# ---------------------------------------------------------------
# Exactly-once financial state
# ---------------------------------------------------------------


def counts(runner) -> dict[str, int]:
    return runner.table_counts()


def test_no_duplicate_broker_submissions(soak):
    """
    Every submitted ref_id maps to at most one fake
    order, and no order carries a duplicate ref_id —
    across every crash and restart in the run.
    """
    runner, _report = soak
    seen: dict[str, int] = {}
    for order in runner.transport.state.orders.values():
        if order.ref_id is None:
            continue
        seen[order.ref_id] = (
            seen.get(order.ref_id, 0) + 1
        )
    assert seen
    duplicates = {
        ref: count
        for ref, count in seen.items()
        if count > 1
    }
    assert duplicates == {}


def test_no_duplicate_attempt_numbers(soak):
    runner, _report = soak
    rows = runner._reader().execute(
        """
        SELECT action_id, attempt_number, COUNT(*)
        FROM broker_saga_action_attempts
        GROUP BY action_id, attempt_number
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert rows == []


def test_no_duplicate_trade_closures(soak):
    runner, _report = soak
    rows = runner._reader().execute(
        """
        SELECT trade_id, COUNT(*) AS closes
        FROM trade_events
        WHERE new_status = 'closed'
        GROUP BY trade_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert rows == []


def test_no_duplicate_lifecycle_observations(soak):
    runner, _report = soak
    for table in (
        "entry_recovery_events",
        "protective_stop_recovery_events",
        "exit_recovery_events",
    ):
        rows = runner._reader().execute(
            f"""
            SELECT trade_id, observation_key, COUNT(*)
            FROM {table}
            GROUP BY trade_id, observation_key
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        assert rows == [], table


def test_no_duplicate_routing_idempotency_keys(soak):
    runner, _report = soak
    rows = runner._reader().execute(
        """
        SELECT idempotency_key, COUNT(*)
        FROM broker_saga_routings
        GROUP BY idempotency_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert rows == []


def test_realized_pnl_never_exceeds_expectation(soak):
    """
    Closed trades realize exactly the scripted profit.
    A crash may prevent a close, but nothing may ever
    realize more than the scenario intended.
    """
    runner, report = soak
    rows = runner._reader().execute(
        """
        SELECT status, cumulative_realized_pnl
        FROM trades
        """
    ).fetchall()
    assert rows
    for status, pnl in rows:
        value = Decimal(str(pnl))
        assert value <= Decimal("20"), status
        if status == TradeStatus.CLOSED.value:
            assert value == Decimal("20")


def test_every_scenario_reconciled_or_halted(soak):
    _runner, report = soak
    for scenario in report["scenarios"]:
        assert scenario["matched"], (
            scenario["kind"],
            scenario["mismatches"],
        )


def test_no_unexpired_claims_survive_the_run(soak):
    runner, _report = soak
    moment = runner.clock.now.isoformat()
    rows = runner._reader().execute(
        """
        SELECT action_id
        FROM broker_saga_actions
        WHERE status = 'claimed'
          AND claim_expires_at > ?
        """,
        (moment,),
    ).fetchall()
    assert rows == []


# ---------------------------------------------------------------
# Growth and runtime
# ---------------------------------------------------------------


def test_database_growth_stays_within_budget(soak):
    """
    Durable growth must scale with scenarios, not with
    cycles, crashes, or restarts. Replays and recovery
    must not add rows.
    """
    _runner, report = soak
    growth = report["database"]["row_growth"]
    assert growth["trades"] == SOAK_SCENARIOS

    budgets = {
        "broker_saga_actions": 3,
        "broker_saga_routings": 4,
        "broker_saga_action_attempts": 4,
        "trade_events": 14,
        "entry_recovery_events": 2,
        "protective_stop_recovery_events": 2,
        "exit_recovery_events": 2,
        "operator_resolutions": 1,
    }
    for table, per_scenario in budgets.items():
        assert (
            growth[table]
            <= per_scenario * SOAK_SCENARIOS
        ), (table, growth[table])


def test_soak_runtime_is_reported(soak):
    _runner, report = soak
    assert report["runtime_seconds"] > 0
    assert report["database"]["bytes_after"] >= (
        report["database"]["bytes_before"]
    )


def test_report_records_safety_confirmations(soak):
    _runner, report = soak
    assert report["safety"] == {
        "live_broker_transport": False,
        "network_access": False,
        "credential_access": False,
        "production_database_access": False,
        "wall_clock_decisions": False,
    }
    assert (
        runner_transport_is_simulated_only(_runner)
    )


def runner_transport_is_simulated_only(runner) -> bool:
    return (
        getattr(
            runner.transport, "simulated_only", False
        )
        is True
        and "robinhood"
        not in type(runner.transport).__module__.lower()
    )


# ---------------------------------------------------------------
# Deterministic replay at scale
# ---------------------------------------------------------------


def test_soak_replays_deterministically(tmp_path):
    schedule = build_scenarios(REPLAY_SCENARIOS)

    reports = []
    for name in ("first", "second"):
        store = TradeStateStore(
            tmp_path / f"{name}.sqlite"
        )
        runner = SimulationRunner(
            store,
            clock=VirtualClock(),
            max_cycles=100_000,
        )
        reports.append(runner.run(schedule))
        runner.close()

    first, second = reports
    assert (
        first["status"] == RunStatus.COMPLETED.value
    )
    assert (
        first["content_digest"]
        == second["content_digest"]
    )
    for key in (
        "scenarios_executed",
        "scenarios_matched",
        "cycles_executed",
        "crash_total",
        "crashes_injected",
        "restarts",
        "transport",
        "final_virtual_time",
        "clock_advances",
    ):
        assert first[key] == second[key], key
    assert (
        first["database"]["rows_after"]
        == second["database"]["rows_after"]
    )


def test_repository_data_guard_attempts_remain_zero(
    repository_data_database_guard,
):
    assert (
        repository_data_database_guard.attempts == []
    )
