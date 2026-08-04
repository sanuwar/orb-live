from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.durable_runner_liveness import (
    LivenessDecision,
    LivenessReason,
    LivenessRecord,
    LivenessState,
    LivenessSuspensionRequest,
    StoredRunnerOutcome,
)
from src.durable_scheduler import RunState, ScheduledRun, SchedulerAction
from src.durable_suspension_controller import (
    ControllerState,
    EvidenceClass,
    SuspensionCause,
    SuspensionDecision,
)
from src.offline_cycle_driver import DriverOutcome, DriverReason, DriverResult, DriverState
from src.offline_safety_orchestrator import CycleOutcome, CycleReason, CycleResult
from src.offline_shadow_runner import RunnerOutcome, RunnerReason, RunnerResult, RunnerState
from src.operational_status_report import (
    REPORT_KEYS,
    CountBucket,
    OperationalStatusReport,
    OperationalStatusStore,
    OverallStatus,
    ReportFreshnessPolicy,
    StatusContext,
    StatusInputs,
    StatusReason,
    StatusReportError,
    project_status,
)


NOW = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)


def runner(
    state: RunnerState = RunnerState.COMPLETED,
    outcome: RunnerOutcome = RunnerOutcome.COMPLETED_BOUND,
    reason: RunnerReason = RunnerReason.DRIVER_COMPLETE,
) -> RunnerResult:
    return RunnerResult("runner-safe", state, outcome, reason, 1, 1, 7, "driver-safe")


def cycle(outcome: CycleOutcome = CycleOutcome.COMPLETED_NO_ACTION) -> CycleResult:
    reasons = {
        CycleOutcome.COMPLETED_NO_ACTION: CycleReason.OK,
        CycleOutcome.OFFLINE_CHECKS_COMPLETE: CycleReason.OK,
        CycleOutcome.WAIT_FOR_SESSION: CycleReason.MARKET_NOT_IN_SESSION,
        CycleOutcome.REQUIRE_OPERATOR_REVIEW: CycleReason.RECONCILIATION_UNRESOLVED,
        CycleOutcome.SUSPENDED: CycleReason.ACTIVE_SUSPENSION,
        CycleOutcome.FAILED_CLOSED: CycleReason.MALFORMED_DURABLE_STATE,
        CycleOutcome.AMBIGUOUS_RESTART: CycleReason.AMBIGUOUS_RESTART,
    }
    actions = {
        CycleOutcome.COMPLETED_NO_ACTION: SchedulerAction.COMPLETE_NO_ACTION,
        CycleOutcome.OFFLINE_CHECKS_COMPLETE: SchedulerAction.RUN_OFFLINE_CHECKS,
        CycleOutcome.WAIT_FOR_SESSION: SchedulerAction.WAIT,
        CycleOutcome.REQUIRE_OPERATOR_REVIEW: SchedulerAction.REQUIRE_OPERATOR_REVIEW,
        CycleOutcome.SUSPENDED: SchedulerAction.SUSPEND,
        CycleOutcome.FAILED_CLOSED: SchedulerAction.FAIL_CLOSED,
        CycleOutcome.AMBIGUOUS_RESTART: SchedulerAction.FAIL_CLOSED,
    }
    return CycleResult(outcome, reasons[outcome], actions[outcome])


def driver(cycle_result: CycleResult | None = None, *, state: DriverState = DriverState.COMPLETED_NO_ACTION,
           outcome: DriverOutcome = DriverOutcome.COMPLETED_NO_ACTION,
           reason: DriverReason = DriverReason.ORCHESTRATOR_RESULT) -> DriverResult:
    value = cycle_result or cycle()
    return DriverResult(state, outcome, reason, value.outcome, value.reason)


def scheduled(state: RunState = RunState.COMPLETED_NO_ACTION) -> ScheduledRun:
    return ScheduledRun("run-safe", date(2026, 8, 3), state, NOW - timedelta(minutes=5), NOW - timedelta(minutes=1))


def suspension(state: ControllerState = ControllerState.CLEAR, *, review: bool = False) -> SuspensionDecision:
    cause = SuspensionCause.NONE if state is ControllerState.CLEAR else (
        SuspensionCause.INTERNAL_INVARIANT_FAILURE if state is ControllerState.PERMANENT_HALT
        else SuspensionCause.OPERATOR_HALT
    )
    return SuspensionDecision(state, cause, review, SchedulerAction.REQUIRE_OPERATOR_REVIEW, EvidenceClass.NONE)


def liveness(
    state: LivenessState = LivenessState.HEALTHY,
    reason: LivenessReason = LivenessReason.HEARTBEAT_CURRENT,
    *, request: bool = False,
    outcome: StoredRunnerOutcome = StoredRunnerOutcome.COMPLETED_BOUND,
    runner_id: str = "runner-safe",
) -> LivenessDecision:
    cause = SuspensionCause.STALE_DATA if request else SuspensionCause.NONE
    record = LivenessRecord(
        runner_id, 1, NOW - timedelta(minutes=2), NOW - timedelta(minutes=1),
        outcome, state, reason, NOW - timedelta(seconds=30),
        "liveness-transition-safe", 2,
    )
    request_value = LivenessSuspensionRequest(
        runner_id, request, cause, reason, "liveness-transition-safe"
    )
    return LivenessDecision(record, 0, request_value)


def unknown_liveness() -> LivenessDecision:
    record = LivenessRecord(
        "runner-safe", None, NOW - timedelta(minutes=2), None,
        StoredRunnerOutcome.NO_RESULT, LivenessState.UNKNOWN, LivenessReason.NO_HEARTBEAT,
        NOW - timedelta(seconds=30), "liveness-transition-safe", 2,
    )
    return LivenessDecision(
        record,
        0,
        LivenessSuspensionRequest(
            "runner-safe", False, SuspensionCause.NONE, LivenessReason.NO_HEARTBEAT,
            "liveness-transition-safe",
        ),
    )


def inputs(**changes: object) -> StatusInputs:
    c = cycle()
    values = dict(
        runner=runner(), driver=driver(c), orchestrator=c, scheduler=scheduled(),
        suspension=suspension(), liveness=liveness(),
    )
    values.update(changes)
    return StatusInputs(**values)


def context(**changes: object) -> StatusContext:
    values = dict(
        report_id="status-safe", runner_id="runner-safe", scheduler_run_id="run-safe",
        reported_at=NOW, maximum_component_age=timedelta(minutes=5),
    )
    values.update(changes)
    return StatusContext(**values)


def ambiguous_inputs() -> StatusInputs:
    ambiguous_cycle = cycle(CycleOutcome.AMBIGUOUS_RESTART)
    return inputs(
        runner=runner(RunnerState.AMBIGUOUS_RESTART, RunnerOutcome.STOPPED_ON_AMBIGUITY, RunnerReason.DRIVER_AMBIGUOUS),
        driver=driver(ambiguous_cycle, state=DriverState.AMBIGUOUS_RESTART, outcome=DriverOutcome.AMBIGUOUS_RESTART),
        orchestrator=ambiguous_cycle,
        liveness=liveness(
            LivenessState.SUSPENSION_REQUIRED,
            LivenessReason.RUNNER_AMBIGUOUS,
            request=True,
            outcome=StoredRunnerOutcome.STOPPED_ON_AMBIGUITY,
        ),
    )


def test_healthy_offline_is_explicitly_inert() -> None:
    report = project_status(context(), inputs())
    assert report.overall_status is OverallStatus.HEALTHY_OFFLINE
    assert report.reason_codes == (StatusReason.HEALTHY_OFFLINE,)
    assert report.protection_grounded is False
    assert report.activation_approved is False
    assert report.permits_automated_action is False
    assert "LIVE_READY" not in json.dumps(report.to_dict())
    assert "UNATTENDED_READY" not in json.dumps(report.to_dict())


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"runner": None, "driver": None, "orchestrator": None, "liveness": None}, OverallStatus.WAITING),
        ({"scheduler": scheduled(RunState.READY_FOR_SUPERVISED_REVIEW)}, OverallStatus.REVIEW_REQUIRED),
        ({"suspension": suspension(ControllerState.SUSPENDED, review=True)}, OverallStatus.SUSPENDED),
        ({"liveness": liveness(LivenessState.DEGRADED, LivenessReason.UNKNOWN_REASON)}, OverallStatus.DEGRADED),
        ({"runner": runner(RunnerState.FAILED_CLOSED, RunnerOutcome.STOPPED_ON_FAILURE, RunnerReason.DRIVER_FAILED)}, OverallStatus.FAILED_CLOSED),
        ({"runner": ambiguous_inputs().runner, "driver": ambiguous_inputs().driver, "orchestrator": ambiguous_inputs().orchestrator, "liveness": ambiguous_inputs().liveness}, OverallStatus.AMBIGUOUS),
        ({"runner": runner(RunnerState.EXPIRED, RunnerOutcome.EXPIRED, RunnerReason.DRIVER_EXPIRED), "driver": DriverResult(DriverState.EXPIRED, DriverOutcome.FAILED_CLOSED, DriverReason.EXPIRED), "orchestrator": None, "liveness": None}, OverallStatus.EXPIRED),
    ],
)
def test_every_overall_status(changed: dict[str, object], expected: OverallStatus) -> None:
    assert project_status(context(), inputs(**changed)).overall_status is expected


def test_malformed_identity_dominates_permanent_halt_and_failure() -> None:
    bad_liveness = liveness(runner_id="runner-other")
    report = project_status(
        context(),
        inputs(liveness=bad_liveness, suspension=suspension(ControllerState.PERMANENT_HALT),
               runner=runner(RunnerState.FAILED_CLOSED, RunnerOutcome.STOPPED_ON_FAILURE, RunnerReason.DRIVER_FAILED)),
    )
    assert report.overall_status is OverallStatus.FAILED_CLOSED
    assert report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE
    assert StatusReason.PERMANENT_HALT in report.reason_codes
    assert StatusReason.COMPONENT_FAILED in report.reason_codes


def test_permanent_halt_dominates_active_suspension() -> None:
    report = project_status(context(), inputs(suspension=suspension(ControllerState.PERMANENT_HALT)))
    assert report.reason_codes[0] is StatusReason.PERMANENT_HALT


def test_active_suspension_hides_no_favorable_component() -> None:
    report = project_status(context(), inputs(suspension=suspension(ControllerState.SUSPENDED)))
    assert report.overall_status is OverallStatus.SUSPENDED
    assert report.suspension_active is True


@pytest.mark.parametrize(
    "changed",
    [
        {"scheduler": scheduled(RunState.SUSPENDED)},
        {"runner": runner(RunnerState.COMPLETED, RunnerOutcome.STOPPED_ON_SUSPENSION, RunnerReason.DRIVER_SUSPENDED), "driver": None, "orchestrator": None},
        {"runner": runner(RunnerState.COMPLETED, RunnerOutcome.STOPPED_ON_SUSPENSION, RunnerReason.DRIVER_SUSPENDED), "driver": driver(cycle(CycleOutcome.SUSPENDED), outcome=DriverOutcome.SUSPENDED), "orchestrator": cycle(CycleOutcome.SUSPENDED)},
    ],
)
def test_every_public_suspension_source_dominates(changed: dict[str, object]) -> None:
    report = project_status(context(), inputs(**changed))
    assert report.overall_status is OverallStatus.SUSPENDED
    assert report.suspension_active is True


def test_ambiguity_dominates_healthy_liveness() -> None:
    values = ambiguous_inputs()
    report = project_status(context(), replace(values, liveness=liveness()))
    assert report.overall_status is OverallStatus.AMBIGUOUS
    assert report.requires_operator_review is True


def test_liveness_suspension_dominates_failed_component() -> None:
    live = liveness(LivenessState.STALE, LivenessReason.HEARTBEAT_STALE, request=True, outcome=StoredRunnerOutcome.EXPIRED)
    expired_runner = runner(RunnerState.EXPIRED, RunnerOutcome.EXPIRED, RunnerReason.DRIVER_EXPIRED)
    report = project_status(context(), inputs(
        liveness=live,
        runner=expired_runner,
        driver=DriverResult(DriverState.EXPIRED, DriverOutcome.FAILED_CLOSED, DriverReason.EXPIRED),
        orchestrator=None,
        scheduler=scheduled(RunState.FAILED_CLOSED),
    ))
    assert report.overall_status is OverallStatus.SUSPENDED
    assert report.reason_codes[0] is StatusReason.LIVENESS_SUSPENSION_REQUIRED
    assert StatusReason.COMPONENT_FAILED in report.reason_codes


def test_failed_component_dominates_waiting_and_is_not_omitted() -> None:
    failed_cycle = cycle(CycleOutcome.FAILED_CLOSED)
    report = project_status(context(), inputs(
        driver=driver(failed_cycle, state=DriverState.FAILED_CLOSED, outcome=DriverOutcome.FAILED_CLOSED),
        orchestrator=failed_cycle, scheduler=scheduled(RunState.WAITING_FOR_SESSION),
    ))
    assert report.overall_status is OverallStatus.FAILED_CLOSED
    assert StatusReason.COMPONENT_FAILED in report.reason_codes


def test_review_dominates_waiting() -> None:
    report = project_status(context(), inputs(scheduler=scheduled(RunState.SUPERVISED_REVIEW_REQUIRED), liveness=None))
    assert report.overall_status is OverallStatus.REVIEW_REQUIRED


def test_fully_populated_waiting_stack_round_trips() -> None:
    waiting_cycle = cycle(CycleOutcome.WAIT_FOR_SESSION)
    report = project_status(context(), inputs(
        runner=runner(RunnerState.COMPLETED, RunnerOutcome.COMPLETED_BOUND, RunnerReason.DRIVER_WAIT),
        driver=driver(waiting_cycle, outcome=DriverOutcome.WAIT),
        orchestrator=waiting_cycle,
        scheduler=scheduled(RunState.WAITING_FOR_SESSION),
    ))
    assert report.overall_status is OverallStatus.WAITING
    assert OperationalStatusReport.from_dict(report.to_dict()) == report


def test_unknown_liveness_is_waiting_even_when_stack_is_populated() -> None:
    report = project_status(context(), inputs(liveness=unknown_liveness()))
    assert report.overall_status is OverallStatus.WAITING
    assert OperationalStatusReport.from_dict(report.to_dict()) == report


def test_conflicting_orchestrator_result_fails_closed() -> None:
    report = project_status(context(), inputs(orchestrator=cycle(CycleOutcome.WAIT_FOR_SESSION)))
    assert report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE


def test_conflicting_runner_driver_outcome_fails_closed() -> None:
    failed_cycle = cycle(CycleOutcome.FAILED_CLOSED)
    report = project_status(context(), inputs(
        driver=driver(failed_cycle, state=DriverState.FAILED_CLOSED, outcome=DriverOutcome.FAILED_CLOSED),
        orchestrator=failed_cycle,
    ))
    assert report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE


def test_missing_component_cannot_project_healthy() -> None:
    assert project_status(context(), inputs(suspension=None)).overall_status is OverallStatus.WAITING


def test_malformed_controller_state_has_top_dominance() -> None:
    malformed = SuspensionDecision(
        ControllerState.MALFORMED_STATE,
        SuspensionCause.UNKNOWN_CAUSE,
        True,
        SchedulerAction.SUSPEND,
    )
    report = project_status(context(), inputs(suspension=malformed))
    assert report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE


def test_future_component_timestamp_fails_closed() -> None:
    with pytest.raises(StatusReportError, match="future-dated"):
        project_status(context(), inputs(scheduler=replace(scheduled(), updated_at=NOW + timedelta(microseconds=1))))


def test_stale_component_timestamp_cannot_be_redated_healthy() -> None:
    old = replace(scheduled(), updated_at=NOW - timedelta(minutes=5, microseconds=1))
    report = project_status(context(), inputs(scheduler=old))
    assert report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE


@pytest.mark.parametrize(
    "changes",
    [
        {"runner_state": RunnerState.FAILED_CLOSED},
        {"suspension_state": ControllerState.PERMANENT_HALT, "suspension_active": True, "requires_operator_review": True},
        {"liveness_state": LivenessState.FAILED_CLOSED, "suspension_active": True, "requires_operator_review": True},
    ],
)
def test_favorable_headline_cannot_hide_adverse_fields(changes: dict[str, object]) -> None:
    with pytest.raises(StatusReportError, match="hides"):
        replace(project_status(context(), inputs()), **changes)


def test_secondary_adverse_reason_cannot_be_removed() -> None:
    failed_cycle = cycle(CycleOutcome.FAILED_CLOSED)
    failed_runner = runner(RunnerState.FAILED_CLOSED, RunnerOutcome.STOPPED_ON_FAILURE, RunnerReason.DRIVER_FAILED)
    report = project_status(context(), inputs(
        runner=failed_runner,
        driver=driver(failed_cycle, state=DriverState.FAILED_CLOSED, outcome=DriverOutcome.FAILED_CLOSED),
        orchestrator=failed_cycle,
        suspension=suspension(ControllerState.SUSPENDED),
    ))
    assert StatusReason.COMPONENT_FAILED in report.reason_codes
    with pytest.raises(StatusReportError, match="reasons omit"):
        replace(report, reason_codes=(StatusReason.ACTIVE_SUSPENSION, StatusReason.OPERATOR_REVIEW_REQUIRED))


@pytest.mark.parametrize(
    "changes",
    [
        {"scheduler_updated_at": None},
        {"liveness_evaluated_at": None},
        {"liveness_transition_id": None},
        {"runner_state": None, "runner_outcome": None, "completed_tick_bucket": CountBucket.MANY},
        {"liveness_state": None, "liveness_transition_id": None, "liveness_evaluated_at": None, "missed_cycle_bucket": CountBucket.MANY},
    ],
)
def test_component_correlations_and_counts_are_presence_bound(changes: dict[str, object]) -> None:
    with pytest.raises(StatusReportError):
        replace(project_status(context(), inputs()), **changes)


def test_buckets_are_fixed_not_raw_counts() -> None:
    report = project_status(context(), inputs())
    assert report.completed_tick_bucket is CountBucket.ONE
    assert report.missed_cycle_bucket is CountBucket.ZERO


def test_exact_serialized_allowlist_and_round_trip() -> None:
    report = project_status(context(), inputs())
    raw = report.to_dict()
    assert set(raw) == REPORT_KEYS
    assert OperationalStatusReport.from_dict(raw) == report
    assert json.dumps(raw, sort_keys=True, separators=(",", ":")) == json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("field", ["account_number", "symbol", "exception_text", "manifest", "username", "price", "quantity"])
def test_unknown_or_prohibited_serialized_field_is_refused(field: str) -> None:
    raw = project_status(context(), inputs()).to_dict()
    raw[field] = "private-value"
    with pytest.raises(StatusReportError, match="fields"):
        OperationalStatusReport.from_dict(raw)


def test_malformed_enum_and_permission_escalation_are_refused() -> None:
    raw = project_status(context(), inputs()).to_dict()
    raw["overall_status"] = "LIVE_READY"
    with pytest.raises(StatusReportError):
        OperationalStatusReport.from_dict(raw)
    raw = project_status(context(), inputs()).to_dict()
    raw["permits_automated_action"] = True
    with pytest.raises(StatusReportError, match="never authorizes"):
        OperationalStatusReport.from_dict(raw)


def test_untrusted_parse_text_is_not_chained_or_leaked() -> None:
    raw = project_status(context(), inputs()).to_dict()
    raw["overall_status"] = "PRIVATE-account-secret"
    with pytest.raises(StatusReportError) as caught:
        OperationalStatusReport.from_dict(raw)
    assert caught.value.__cause__ is None
    assert "PRIVATE" not in str(caught.value)


def test_arbitrary_component_dictionary_is_refused() -> None:
    with pytest.raises(StatusReportError, match="public decided result"):
        StatusInputs({}, None, None, None, None, None)  # type: ignore[arg-type]


def test_private_identifier_is_refused() -> None:
    with pytest.raises(StatusReportError):
        context(report_id="status-account-123")


def test_restart_safe_store_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    policy = ReportFreshnessPolicy(timedelta(minutes=5))
    report = project_status(context(), inputs())
    OperationalStatusStore("status.sqlite", policy).persist(report, stored_at=NOW)
    reopened = OperationalStatusStore("status.sqlite", policy)
    assert reopened.load("status-safe", evaluation_time=NOW + timedelta(minutes=5)) == report
    reopened.persist(report, stored_at=NOW)


def test_restart_cannot_relax_durable_freshness_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    policy = ReportFreshnessPolicy(timedelta(minutes=5))
    report = project_status(context(), inputs())
    OperationalStatusStore("status.sqlite", policy).persist(report, stored_at=NOW)
    with pytest.raises(StatusReportError, match="policy conflicts"):
        OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(days=1)))


def test_conflicting_duplicate_report_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(minutes=5)))
    report = project_status(context(), inputs())
    store.persist(report, stored_at=NOW)
    later = project_status(context(reported_at=NOW + timedelta(seconds=1)), inputs())
    with pytest.raises(StatusReportError, match="already bound"):
        store.persist(later, stored_at=NOW + timedelta(seconds=1))


def test_stale_and_future_reports_are_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(minutes=5)))
    store.persist(project_status(context(), inputs()), stored_at=NOW)
    with pytest.raises(StatusReportError, match="stale"):
        store.load("status-safe", evaluation_time=NOW + timedelta(minutes=5, microseconds=1))
    with pytest.raises(StatusReportError, match="time"):
        store.load("status-safe", evaluation_time=NOW - timedelta(microseconds=1))


def test_tampered_stored_projection_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(minutes=5)))
    store.persist(project_status(context(), inputs()), stored_at=NOW)
    with sqlite3.connect("status.sqlite") as connection:
        connection.execute("UPDATE status_reports SET payload_json = ?", ('{"overall_status":"LIVE_READY"}',))
    with pytest.raises(StatusReportError, match="integrity"):
        store.load("status-safe", evaluation_time=NOW)


def test_rehashed_semantic_tamper_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(minutes=5)))
    store.persist(project_status(context(), inputs()), stored_at=NOW)
    with sqlite3.connect("status.sqlite") as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM status_reports").fetchone()[0])
        payload["runner_state"] = RunnerState.FAILED_CLOSED.value
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection.execute("UPDATE status_reports SET payload_json = ?, payload_sha256 = ?", (encoded, digest))
    with pytest.raises(StatusReportError, match="hides"):
        store.load("status-safe", evaluation_time=NOW)


def test_rehashed_stale_component_tamper_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = OperationalStatusStore("status.sqlite", ReportFreshnessPolicy(timedelta(minutes=5)))
    store.persist(project_status(context(), inputs()), stored_at=NOW)
    with sqlite3.connect("status.sqlite") as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM status_reports").fetchone()[0])
        payload["liveness_evaluated_at"] = (NOW - timedelta(minutes=5, microseconds=1)).isoformat()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection.execute("UPDATE status_reports SET payload_json = ?, payload_sha256 = ?", (encoded, digest))
    with pytest.raises(StatusReportError, match="component evidence is stale"):
        store.load("status-safe", evaluation_time=NOW)


def test_database_path_refuses_memory_absolute_and_repository_data(tmp_path: Path) -> None:
    policy = ReportFreshnessPolicy(timedelta(minutes=5))
    for value in (":memory:", tmp_path / "absolute.sqlite", "data/status.sqlite"):
        with pytest.raises(StatusReportError):
            OperationalStatusStore(value, policy)


def test_source_has_no_clock_broker_network_manifest_or_private_collaborator_access() -> None:
    source = Path("src/operational_status_report.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"requests", "httpx", "socket", "subprocess", "robin_stocks"})
    lowered = source.lower()
    for forbidden in ("datetime.now", "datetime.utcnow", "time.time", "time.monotonic", "activation_approvals", "authorization_manifest", "local.yaml", "_connection", "._conn", "clear_suspension"):
        assert forbidden not in lowered


def test_output_vocabulary_has_no_live_or_unattended_status() -> None:
    assert {value.value for value in OverallStatus}.isdisjoint({"LIVE_READY", "UNATTENDED_READY"})
