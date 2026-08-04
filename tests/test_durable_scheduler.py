"""Durable calendar-driven scheduler — states, transitions, restart, suspension.

Every test is local and offline. Nothing here opens a broker session, reaches
the network, issues or consumes a manifest, executes a checkpoint, or touches a
credential. Every database lives under `tmp_path`.

The properties that matter most:

1. **No result ever permits automated action**, and one claiming to cannot be
   constructed.
2. **A restart never resumes into an action-capable state.**
3. **Suspension survives reopening the database**, and clearing it requires
   operator-reviewed evidence.
4. **A transition identity is global and permanent**, across runs and restarts.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import src.durable_scheduler as scheduler_module
from src.durable_scheduler import (
    ALLOWED_TRANSITIONS,
    BACKWARD_TRANSITIONS,
    CLEARANCE_REASONS,
    RESTART_HALTED_STATES,
    RESTART_REEVALUATION_STATES,
    TERMINAL_STATES,
    DurableScheduler,
    OperatorClearance,
    RunState,
    SchedulerAction,
    SchedulerDecision,
    SchedulerError,
    SchedulerReason,
    UpstreamFacts,
    assess_calendar,
    is_repository_data_path,
    transition_is_allowed,
    transition_is_backward,
)
from src.exchange_calendar import EASTERN, CalendarSession

MODULE_PATH = pathlib.Path(scheduler_module.__file__)

# 2026-08-03 is a Monday inside the supported calendar range.
REGULAR = datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN)
SESSION_DATE = date(2026, 8, 3)
PRE_MARKET = datetime(2026, 8, 3, 5, 0, tzinfo=EASTERN)
AFTER_HOURS = datetime(2026, 8, 3, 18, 0, tzinfo=EASTERN)
OVERNIGHT = datetime(2026, 8, 3, 2, 0, tzinfo=EASTERN)
SATURDAY = datetime(2026, 8, 1, 10, 0, tzinfo=EASTERN)
HOLIDAY = datetime(2026, 9, 7, 10, 0, tzinfo=EASTERN)          # Labor Day
EARLY_CLOSE_BEFORE = datetime(2026, 11, 27, 12, 0, tzinfo=EASTERN)
EARLY_CLOSE_AFTER = datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN)
UNSUPPORTED = datetime(2025, 8, 4, 10, 0, tzinfo=EASTERN)
BEYOND_TABLE = datetime(2028, 8, 7, 10, 0, tzinfo=EASTERN)
NAIVE = datetime(2026, 8, 3, 10, 0)
# US DST ends 2026-11-01; 01:30 local names two different instants.
AMBIGUOUS_DST = datetime(2026, 11, 1, 1, 30, tzinfo=EASTERN)


def clean_facts(**overrides) -> UpstreamFacts:
    """Everything upstream satisfied. The only shape that can reach RUN."""
    fields = dict(
        gate_is_open=True,
        risk_limits_satisfied=True,
        reconciliation_resolved=True,
        monitoring_delivery_confirmed=True,
        attestations_verified=True,
        unresolved_operator_action=False,
        safety_halt_active=False,
    )
    fields.update(overrides)
    return UpstreamFacts(**fields)


def store(tmp_path, **kwargs) -> DurableScheduler:
    return DurableScheduler(tmp_path / "scheduler.sqlite", **kwargs)


def new_run(sched, run_id="run-1", *, session_date=SESSION_DATE, now=REGULAR):
    return sched.create_run(run_id, session_date=session_date, now=now)


def advance(sched, run_id, target, *, tid, now=REGULAR, **kwargs):
    return sched.apply_transition(
        run_id, target=target, transition_id=tid, now=now, **kwargs
    )


# --------------------------------------------------------------------------
# 1. Offline by construction.
# --------------------------------------------------------------------------


def test_the_scheduler_imports_nothing_broker_or_network_capable():
    forbidden = (
        "robinhood", "mcp", "adapter", "client", "session", "socket", "http",
        "urllib", "requests", "commissioning", "manifest", "broker_saga",
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not any(word in name.lower() for word in forbidden), name


def _dotted_call_names(path: pathlib.Path) -> set[str]:
    """Every call target in the module, as a dotted name where resolvable."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        current: ast.expr = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        if parts:
            names.add(".".join(reversed(parts)))
    return names


def _code_identifiers(path: pathlib.Path) -> set[str]:
    """Identifiers and string literals in *code*, excluding docstrings.

    Matching raw file text would flag this module's own prose describing what
    it does not do, which is the opposite of useful.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                found.add(node.value)
    return found


def test_the_scheduler_never_reads_a_clock():
    """Injected time is the only time.

    Asserted against parsed call targets, not raw text, so the module's own
    docstring explaining this rule does not trip it.
    """
    forbidden = {
        "datetime.now", "datetime.utcnow", "datetime.datetime.now",
        "datetime.datetime.utcnow", "time.time", "time.monotonic",
        "time.perf_counter", "date.today", "datetime.date.today",
    }
    assert _dotted_call_names(MODULE_PATH) & forbidden == set()


def test_the_scheduler_imports_no_wall_clock_source():
    imported = set()
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "time" not in imported


def test_the_scheduler_has_no_order_or_authorization_vocabulary():
    identifiers = {item.lower() for item in _code_identifiers(MODULE_PATH)}
    for forbidden in (
        "place_equity_order", "cancel_equity_order", "review_equity_order",
        "call_tool", "activation_approvals", "account_number", "token",
    ):
        assert not any(forbidden in item for item in identifiers), forbidden


# --------------------------------------------------------------------------
# 2. The state and transition model.
# --------------------------------------------------------------------------


def test_every_state_appears_in_the_transition_matrix():
    assert set(ALLOWED_TRANSITIONS) == set(RunState)


def test_terminal_states_have_no_exit():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_the_model_distinguishes_every_required_concept():
    required = {
        "waiting": RunState.WAITING_FOR_SESSION,
        "review required": RunState.SUPERVISED_REVIEW_REQUIRED,
        "offline evaluation": RunState.RUNNING_OFFLINE_EVALUATION,
        "durable suspension": RunState.SUSPENDED,
        "safe completion": RunState.COMPLETED_NO_ACTION,
        "failure": RunState.FAILED_CLOSED,
        "ambiguity": RunState.AMBIGUOUS_RESTART,
        "expiry": RunState.EXPIRED,
    }
    for concept, state in required.items():
        assert state in RunState, concept
    # All distinct: none of these collapses into another.
    assert len(set(required.values())) == len(required)


def test_no_state_name_implies_broker_execution():
    for state in RunState:
        lowered = state.value.lower()
        for forbidden in ("order", "placed", "filled", "executed", "traded", "sent"):
            assert forbidden not in lowered, state


def test_evaluation_can_only_be_entered_from_precondition_check():
    sources = [
        state for state, targets in ALLOWED_TRANSITIONS.items()
        if RunState.RUNNING_OFFLINE_EVALUATION in targets
    ]
    assert sources == [RunState.PRECONDITION_CHECK]


def test_suspension_leaves_only_into_review():
    assert ALLOWED_TRANSITIONS[RunState.SUSPENDED] == frozenset(
        {RunState.SUPERVISED_REVIEW_REQUIRED}
    )


def test_backward_transitions_are_never_also_allowed():
    for previous, current in BACKWARD_TRANSITIONS:
        assert not transition_is_allowed(previous, current), (previous, current)


def test_transition_helpers_refuse_non_states():
    with pytest.raises(SchedulerError):
        transition_is_allowed("CREATED", RunState.EXPIRED)
    assert transition_is_backward(RunState.CREATED, RunState.EXPIRED) is False


@pytest.mark.parametrize(
    "previous,target",
    [
        (previous, target)
        for previous, targets in ALLOWED_TRANSITIONS.items()
        for target in targets
    ],
)
def test_every_allowed_transition_is_accepted(tmp_path, previous, target):
    """Drive each edge on a real record rather than trusting the table."""
    with store(tmp_path) as sched:
        run = new_run(sched)
        # Force the record into `previous` without going through the matrix.
        sched._connection.execute(
            "UPDATE scheduled_runs SET state = ? WHERE run_id = ?",
            (previous.value, run.run_id),
        )
        clearance = None
        if previous is RunState.SUSPENDED:
            clearance = OperatorClearance(
                transition_id="t-edge",
                operator_reference="op-1",
                reason_code="OPERATOR_REVIEWED_AND_RESOLVED",
                reviewed=True,
            )
        moved = advance(
            sched, run.run_id, target, tid="t-edge", clearance=clearance
        )
        assert moved.state is target


@pytest.mark.parametrize(
    "previous,target",
    [
        (previous, target)
        for previous in RunState
        for target in RunState
        if target not in ALLOWED_TRANSITIONS[previous]
    ],
)
def test_every_disallowed_transition_is_refused(tmp_path, previous, target):
    with store(tmp_path) as sched:
        run = new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET state = ? WHERE run_id = ?",
            (previous.value, run.run_id),
        )
        with pytest.raises(SchedulerError):
            advance(sched, run.run_id, target, tid="t-bad")
        assert sched.load_run(run.run_id).state is previous


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_a_terminal_run_refuses_every_exit(tmp_path, state):
    with store(tmp_path) as sched:
        run = new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET state = ? WHERE run_id = ?",
            (state.value, run.run_id),
        )
        for target in RunState:
            with pytest.raises(SchedulerError):
                advance(sched, run.run_id, target, tid=f"t-{target.value}")
        assert sched.load_run(run.run_id).state is state


# --------------------------------------------------------------------------
# 3. Idempotency.
# --------------------------------------------------------------------------


def test_the_same_transition_identity_for_the_same_move_is_idempotent(tmp_path):
    with store(tmp_path) as sched:
        run = new_run(sched)
        first = advance(sched, run.run_id, RunState.WAITING_FOR_SESSION, tid="t-1")
        again = advance(sched, run.run_id, RunState.WAITING_FOR_SESSION, tid="t-1")
        assert first.state is again.state is RunState.WAITING_FOR_SESSION
        assert sched.transition_count == 1


def test_the_same_identity_asking_for_a_different_move_conflicts(tmp_path):
    with store(tmp_path) as sched:
        run = new_run(sched)
        advance(sched, run.run_id, RunState.WAITING_FOR_SESSION, tid="t-1")
        with pytest.raises(SchedulerError):
            advance(sched, run.run_id, RunState.PRECONDITION_CHECK, tid="t-1")
        assert sched.load_run(run.run_id).state is RunState.WAITING_FOR_SESSION


def test_a_different_identity_for_an_already_reached_state_is_not_a_retry(tmp_path):
    """A second identity asking for the current state is a second transition."""
    with store(tmp_path) as sched:
        run = new_run(sched)
        advance(sched, run.run_id, RunState.WAITING_FOR_SESSION, tid="t-1")
        with pytest.raises(SchedulerError):
            advance(sched, run.run_id, RunState.WAITING_FOR_SESSION, tid="t-2")


def test_a_transition_identity_cannot_be_reused_across_runs(tmp_path):
    with store(tmp_path) as sched:
        first = new_run(sched, "run-a")
        new_run(sched, "run-b")
        advance(sched, first.run_id, RunState.WAITING_FOR_SESSION, tid="shared")
        with pytest.raises(SchedulerError):
            advance(sched, "run-b", RunState.WAITING_FOR_SESSION, tid="shared")


def test_a_transition_identity_stays_spent_across_restart(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
    with DurableScheduler(path) as reopened:
        # Same identity, different target: still a conflict after restart.
        with pytest.raises(SchedulerError):
            advance(reopened, "run-1", RunState.PRECONDITION_CHECK, tid="t-1")
        assert reopened.transition_count == 1


def test_reaching_a_terminal_state_does_not_release_its_identities(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched, "run-a")
        advance(sched, "run-a", RunState.FAILED_CLOSED, tid="t-final")
        new_run(sched, "run-b")
        with pytest.raises(SchedulerError):
            advance(sched, "run-b", RunState.FAILED_CLOSED, tid="t-final")


# --------------------------------------------------------------------------
# 4. Run identity.
# --------------------------------------------------------------------------


def test_a_run_identity_cannot_be_reused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        with pytest.raises(SchedulerError):
            new_run(sched)


def test_a_completed_run_identity_cannot_be_reused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        advance(sched, "run-1", RunState.COMPLETED_NO_ACTION, tid="t-2")
        with pytest.raises(SchedulerError):
            new_run(sched)


@pytest.mark.parametrize("bad", ["", "   ", " run ", "x" * 129, None, 7])
def test_a_malformed_run_identity_is_refused(tmp_path, bad):
    with store(tmp_path) as sched:
        with pytest.raises(SchedulerError):
            sched.create_run(bad, session_date=SESSION_DATE, now=REGULAR)


def test_an_unregistered_run_is_refused(tmp_path):
    with store(tmp_path) as sched:
        assert sched.load_run("absent") is None
        with pytest.raises(SchedulerError):
            sched.decide("absent", now=REGULAR, facts=clean_facts())


def test_a_datetime_session_date_is_refused(tmp_path):
    with store(tmp_path) as sched:
        with pytest.raises(SchedulerError):
            sched.create_run("r", session_date=REGULAR, now=REGULAR)


def test_a_naive_creation_time_is_refused(tmp_path):
    with store(tmp_path) as sched:
        with pytest.raises(SchedulerError):
            sched.create_run("r", session_date=SESSION_DATE, now=NAIVE)


# --------------------------------------------------------------------------
# 5. Calendar integration.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment,session,eligible",
    [
        (REGULAR, CalendarSession.REGULAR, True),
        (PRE_MARKET, CalendarSession.PRE_MARKET, False),
        (AFTER_HOURS, CalendarSession.POST_MARKET, False),
        (OVERNIGHT, CalendarSession.CLOSED, False),
        (SATURDAY, CalendarSession.CLOSED, False),
        (HOLIDAY, CalendarSession.CLOSED, False),
        (EARLY_CLOSE_BEFORE, CalendarSession.REGULAR, True),
        (EARLY_CLOSE_AFTER, CalendarSession.CLOSED, False),
        (UNSUPPORTED, CalendarSession.UNKNOWN, False),
        (BEYOND_TABLE, CalendarSession.UNKNOWN, False),
        (NAIVE, CalendarSession.UNKNOWN, False),
        (AMBIGUOUS_DST, CalendarSession.UNKNOWN, False),
    ],
)
def test_calendar_eligibility(moment, session, eligible):
    result = assess_calendar(moment)
    assert result.session is session
    assert result.eligible is eligible


def test_only_a_regular_session_can_be_constructed_eligible():
    from src.durable_scheduler import CalendarEligibility

    with pytest.raises(SchedulerError):
        CalendarEligibility(True, SchedulerReason.MARKET_CLOSED, CalendarSession.CLOSED)


def test_an_expired_calendar_fails_closed():
    """Evaluated against a supplied clock, so expiry is testable."""
    result = assess_calendar(REGULAR, today=date(2028, 1, 1))
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is SchedulerReason.CALENDAR_EXPIRED


def test_an_unsupported_date_reports_coverage_not_a_guess():
    assert assess_calendar(UNSUPPORTED).reason is (
        SchedulerReason.CALENDAR_OUTSIDE_COVERAGE
    )


def test_unknown_is_never_eligible_for_any_probe():
    for moment in (UNSUPPORTED, BEYOND_TABLE, NAIVE, AMBIGUOUS_DST):
        assert assess_calendar(moment).eligible is False


# --------------------------------------------------------------------------
# 6. The decision.
# --------------------------------------------------------------------------


def decide(tmp_path, *, now=REGULAR, facts=None, session_date=SESSION_DATE, **kw):
    sched = store(tmp_path)
    new_run(sched, session_date=session_date, now=REGULAR)
    result = sched.decide("run-1", now=now, facts=facts or clean_facts(), **kw)
    sched.close()
    return result


def test_a_clean_regular_session_may_run_offline_checks(tmp_path):
    result = decide(tmp_path)
    assert result.action is SchedulerAction.RUN_OFFLINE_CHECKS
    assert result.permits_automated_action is False


@pytest.mark.parametrize(
    "moment,reason",
    [
        (PRE_MARKET, SchedulerReason.PRE_MARKET_SESSION),
        (AFTER_HOURS, SchedulerReason.POST_MARKET_SESSION),
        (OVERNIGHT, SchedulerReason.MARKET_CLOSED),
    ],
)
def test_an_ineligible_session_waits(tmp_path, moment, reason):
    result = decide(tmp_path, now=moment)
    assert result.action is SchedulerAction.WAIT
    assert result.reason is reason


def test_an_unknown_session_fails_closed(tmp_path):
    result = decide(
        tmp_path, now=BEYOND_TABLE, session_date=BEYOND_TABLE.date()
    )
    assert result.action is SchedulerAction.FAIL_CLOSED


def test_a_safety_halt_dominates_a_perfectly_good_session(tmp_path):
    result = decide(tmp_path, facts=clean_facts(safety_halt_active=True))
    assert result.action is SchedulerAction.SUSPEND
    assert result.reason is SchedulerReason.SAFETY_HALT_ACTIVE


def test_a_safety_halt_dominates_even_an_unknown_calendar(tmp_path):
    result = decide(
        tmp_path, now=BEYOND_TABLE, session_date=BEYOND_TABLE.date(),
        facts=clean_facts(safety_halt_active=True),
    )
    assert result.action is SchedulerAction.SUSPEND


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"unresolved_operator_action": True},
         SchedulerReason.UNRESOLVED_OPERATOR_ACTION),
        ({"attestations_verified": False},
         SchedulerReason.ATTESTATION_NOT_VERIFIED),
        ({"reconciliation_resolved": False},
         SchedulerReason.RECONCILIATION_UNRESOLVED),
        ({"risk_limits_satisfied": False},
         SchedulerReason.RISK_LIMITS_NOT_SATISFIED),
        ({"monitoring_delivery_confirmed": False},
         SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED),
        ({"gate_is_open": False}, SchedulerReason.GATE_CLOSED),
    ],
)
def test_each_upstream_refusal_requires_review(tmp_path, override, reason):
    result = decide(tmp_path, facts=clean_facts(**override))
    assert result.action is SchedulerAction.REQUIRE_OPERATOR_REVIEW
    assert result.reason is reason


def test_upstream_facts_default_to_the_unsafe_reading():
    """A caller that forgets a fact gets the answer that stops work."""
    blocking = UpstreamFacts().first_blocking_reason()
    assert blocking is SchedulerReason.UNRESOLVED_OPERATOR_ACTION


def test_a_future_dated_record_fails_closed(tmp_path):
    result = decide(tmp_path, session_date=date(2026, 8, 10))
    assert result.action is SchedulerAction.FAIL_CLOSED
    assert result.reason is SchedulerReason.SCHEDULE_RECORD_FUTURE_DATED


def test_a_stale_record_fails_closed(tmp_path):
    result = decide(tmp_path, session_date=date(2026, 7, 20))
    assert result.action is SchedulerAction.FAIL_CLOSED
    assert result.reason is SchedulerReason.SCHEDULE_RECORD_STALE


def test_a_suspended_run_always_reports_suspended(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s",
                suspension_reason="SAFETY_HALT")
        result = sched.decide("run-1", now=REGULAR, facts=clean_facts())
        assert result.action is SchedulerAction.SUSPEND
        assert result.reason is SchedulerReason.RUN_IS_SUSPENDED


def test_a_terminal_run_reports_terminal(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.FAILED_CLOSED, tid="t-f")
        result = sched.decide("run-1", now=REGULAR, facts=clean_facts())
        assert result.action is SchedulerAction.FAIL_CLOSED
        assert result.reason is SchedulerReason.RUN_IS_TERMINAL


def test_a_decision_can_never_permit_automated_action():
    with pytest.raises(SchedulerError):
        SchedulerDecision(
            SchedulerAction.RUN_OFFLINE_CHECKS,
            SchedulerReason.OK,
            permits_automated_action=True,
        )


def test_every_decision_projection_says_false(tmp_path):
    for action in SchedulerAction:
        decision = SchedulerDecision(action, SchedulerReason.OK)
        assert decision.sanitized_facts()["permits_automated_action"] == "false"


# --------------------------------------------------------------------------
# 7. Restart.
# --------------------------------------------------------------------------


def seed(tmp_path, state, *, suspension_reason=None):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET state = ?, suspension_reason = ? "
            "WHERE run_id = ?",
            (state.value, suspension_reason, "run-1"),
        )
    return path


@pytest.mark.parametrize("state", sorted(RESTART_HALTED_STATES, key=lambda s: s.value))
def test_restart_leaves_halted_states_untouched(tmp_path, state):
    reason = "SAFETY_HALT" if state is RunState.SUSPENDED else None
    path = seed(tmp_path, state, suspension_reason=reason)
    with DurableScheduler(path) as reopened:
        recovered = reopened.recover_on_restart(
            "run-1", transition_id="t-restart", now=REGULAR
        )
        assert recovered.state is state


def test_restart_during_offline_evaluation_becomes_ambiguous(tmp_path):
    """A crashed evaluation cannot prove it finished, so it is never resumed."""
    path = seed(tmp_path, RunState.RUNNING_OFFLINE_EVALUATION)
    with DurableScheduler(path) as reopened:
        recovered = reopened.recover_on_restart(
            "run-1", transition_id="t-restart", now=REGULAR
        )
        assert recovered.state is RunState.AMBIGUOUS_RESTART
        assert recovered.is_terminal


def test_restart_from_waiting_resumes_waiting(tmp_path):
    path = seed(tmp_path, RunState.WAITING_FOR_SESSION)
    with DurableScheduler(path) as reopened:
        recovered = reopened.recover_on_restart(
            "run-1", transition_id="t-restart", now=REGULAR
        )
        assert recovered.state is RunState.WAITING_FOR_SESSION
        assert recovered.reevaluation_required is False


@pytest.mark.parametrize(
    "state", sorted(RESTART_REEVALUATION_STATES, key=lambda s: s.value)
)
def test_restart_requires_explicit_reevaluation(tmp_path, state):
    path = seed(tmp_path, state)
    with DurableScheduler(path) as reopened:
        recovered = reopened.recover_on_restart(
            "run-1", transition_id="t-restart", now=REGULAR
        )
        assert recovered.state is state
        assert recovered.reevaluation_required is True

        decision = reopened.decide("run-1", now=REGULAR, facts=clean_facts())
        assert decision.action is SchedulerAction.REQUIRE_OPERATOR_REVIEW
        assert decision.reason is SchedulerReason.REEVALUATION_REQUIRED


def test_a_run_awaiting_reevaluation_cannot_advance(tmp_path):
    path = seed(tmp_path, RunState.PRECONDITION_CHECK)
    with DurableScheduler(path) as reopened:
        reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
        with pytest.raises(SchedulerError):
            advance(
                reopened, "run-1", RunState.RUNNING_OFFLINE_EVALUATION, tid="t-go"
            )


def test_a_run_awaiting_reevaluation_may_still_halt(tmp_path):
    """Stopping is always available; only advancing is blocked."""
    path = seed(tmp_path, RunState.PRECONDITION_CHECK)
    with DurableScheduler(path) as reopened:
        reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
        halted = advance(reopened, "run-1", RunState.FAILED_CLOSED, tid="t-stop")
        assert halted.state is RunState.FAILED_CLOSED


def test_reevaluation_is_cleared_only_by_a_non_refusing_decision(tmp_path):
    path = seed(tmp_path, RunState.PRECONDITION_CHECK)
    with DurableScheduler(path) as reopened:
        reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
        for refusing in (SchedulerAction.FAIL_CLOSED, SchedulerAction.SUSPEND):
            with pytest.raises(SchedulerError):
                reopened.mark_reevaluated(
                    "run-1",
                    decision=SchedulerDecision(refusing, SchedulerReason.OK),
                    now=REGULAR,
                )
        cleared = reopened.mark_reevaluated(
            "run-1",
            decision=SchedulerDecision(
                SchedulerAction.RUN_OFFLINE_CHECKS, SchedulerReason.OK
            ),
            now=REGULAR,
        )
        assert cleared.reevaluation_required is False


def test_marking_reevaluated_without_the_flag_is_refused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        with pytest.raises(SchedulerError):
            sched.mark_reevaluated(
                "run-1",
                decision=SchedulerDecision(
                    SchedulerAction.WAIT, SchedulerReason.OK
                ),
                now=REGULAR,
            )


def test_restart_recovery_refuses_a_naive_clock(tmp_path):
    path = seed(tmp_path, RunState.WAITING_FOR_SESSION)
    with DurableScheduler(path) as reopened:
        with pytest.raises(SchedulerError):
            reopened.recover_on_restart("run-1", transition_id="t", now=NAIVE)


# --------------------------------------------------------------------------
# 8. Durable suspension.
# --------------------------------------------------------------------------


def test_suspension_survives_reopening_the_database(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s",
                suspension_reason="SAFETY_HALT")
    with DurableScheduler(path) as reopened:
        run = reopened.load_run("run-1")
        assert run.state is RunState.SUSPENDED
        assert run.suspension_reason == "SAFETY_HALT"


def test_reopening_is_not_a_way_to_clear_suspension(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
    for _ in range(3):
        with DurableScheduler(path) as reopened:
            reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
            assert reopened.load_run("run-1").state is RunState.SUSPENDED


def test_no_elapsed_time_clears_suspension(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        far_future = REGULAR + timedelta(days=365)
        decision = sched.decide("run-1", now=far_future, facts=clean_facts())
        assert decision.action is SchedulerAction.SUSPEND


def test_clearing_suspension_requires_reviewed_evidence(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        unreviewed = OperatorClearance(
            transition_id="t-clear",
            operator_reference="op-1",
            reason_code="OPERATOR_REVIEWED_AND_RESOLVED",
            reviewed=False,
        )
        with pytest.raises(SchedulerError):
            sched.clear_suspension("run-1", clearance=unreviewed, now=REGULAR)
        assert sched.load_run("run-1").state is RunState.SUSPENDED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reason_code": "BECAUSE_I_SAID_SO"},
        {"reason_code": ""},
        {"operator_reference": ""},
        {"operator_reference": "   "},
        {"operator_reference": "x" * 65},
        {"operator_reference": "op 1"},
        {"operator_reference": "op@example.com"},
        {"transition_id": ""},
    ],
)
def test_a_malformed_clearance_leaves_the_run_suspended(tmp_path, kwargs):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        fields = dict(
            transition_id="t-clear",
            operator_reference="op-1",
            reason_code="OPERATOR_REVIEWED_AND_RESOLVED",
            reviewed=True,
        )
        fields.update(kwargs)
        with pytest.raises(SchedulerError):
            sched.clear_suspension(
                "run-1", clearance=OperatorClearance(**fields), now=REGULAR
            )
        assert sched.load_run("run-1").state is RunState.SUSPENDED


def test_a_valid_clearance_lands_in_review_not_anything_runnable(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        cleared = sched.clear_suspension(
            "run-1",
            clearance=OperatorClearance(
                transition_id="t-clear",
                operator_reference="op-1",
                reason_code="UPSTREAM_CONDITION_CORRECTED",
                reviewed=True,
            ),
            now=REGULAR,
        )
        assert cleared.state is RunState.SUPERVISED_REVIEW_REQUIRED
        assert cleared.suspension_reason is None


def test_a_clearance_naming_a_different_transition_is_refused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        clearance = OperatorClearance(
            transition_id="t-other",
            operator_reference="op-1",
            reason_code="OPERATOR_REVIEWED_AND_RESOLVED",
            reviewed=True,
        )
        with pytest.raises(SchedulerError):
            sched.apply_transition(
                "run-1",
                target=RunState.SUPERVISED_REVIEW_REQUIRED,
                transition_id="t-mismatch",
                now=REGULAR,
                clearance=clearance,
            )


def test_a_suspended_run_cannot_move_without_clearance(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.SUSPENDED, tid="t-s")
        with pytest.raises(SchedulerError):
            advance(sched, "run-1", RunState.SUPERVISED_REVIEW_REQUIRED, tid="t-x")


def test_clearing_a_run_that_is_not_suspended_is_refused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        with pytest.raises(SchedulerError):
            sched.clear_suspension(
                "run-1",
                clearance=OperatorClearance(
                    transition_id="t", operator_reference="op",
                    reason_code="OPERATOR_REVIEWED_AND_RESOLVED", reviewed=True,
                ),
                now=REGULAR,
            )


def test_an_unapproved_suspension_reason_is_refused(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        with pytest.raises(SchedulerError):
            advance(sched, "run-1", RunState.SUSPENDED, tid="t-s",
                    suspension_reason="ACCOUNT-12345")


# --------------------------------------------------------------------------
# 9. Persistence integrity.
# --------------------------------------------------------------------------


def test_a_database_under_repository_data_is_refused():
    from src.durable_scheduler import REPOSITORY_ROOT

    with pytest.raises(SchedulerError):
        DurableScheduler(REPOSITORY_ROOT / "data" / "scheduler.sqlite")
    with pytest.raises(SchedulerError):
        DurableScheduler(REPOSITORY_ROOT / "data" / "nested" / "s.sqlite")


def test_the_repository_data_check_recognizes_the_directory():
    from src.durable_scheduler import REPOSITORY_ROOT

    assert is_repository_data_path(REPOSITORY_ROOT / "data")
    assert is_repository_data_path(REPOSITORY_ROOT / "data" / "x.sqlite")
    assert not is_repository_data_path(REPOSITORY_ROOT / "src")


def test_an_unknown_schema_version_is_refused(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE scheduler_meta SET value = '99' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(SchedulerError):
        DurableScheduler(path)


def test_an_unrecognized_persisted_state_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET state = 'DEFINITELY_FINE' WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_a_malformed_persisted_timestamp_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET created_at = 'yesterday' WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_a_persisted_timestamp_without_a_timezone_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET updated_at = '2026-08-03T10:00:00' "
            "WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_a_missing_required_field_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET session_date = '' WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_an_unexpected_suspension_reason_in_storage_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET suspension_reason = 'ACCT-9' WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_a_malformed_reevaluation_flag_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        # The CHECK constraint blocks the honest path, so go around it the way
        # a corrupted file would.
        sched._connection.execute("PRAGMA ignore_check_constraints=ON")
        sched._connection.execute(
            "UPDATE scheduled_runs SET reevaluation_required = 7 WHERE run_id = ?",
            ("run-1",),
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_the_schema_check_constraint_rejects_a_bad_flag(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        with pytest.raises(sqlite3.IntegrityError):
            sched._connection.execute(
                "UPDATE scheduled_runs SET reevaluation_required = 5 "
                "WHERE run_id = ?",
                ("run-1",),
            )


def test_a_write_failure_reports_its_own_cause_not_the_rollback(tmp_path):
    """A failed rollback must not replace the exception that caused it."""
    with store(tmp_path) as sched:
        new_run(sched)
        real = sched._connection

        class FailingOnUpdate:
            """Proxies the connection but refuses the run-state update."""

            def execute(self, statement, *args, **kwargs):
                if statement.lstrip().upper().startswith("UPDATE SCHEDULED_RUNS"):
                    raise sqlite3.OperationalError("disk is on fire")
                return real.execute(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(real, name)

        sched._connection = FailingOnUpdate()  # type: ignore[assignment]
        with pytest.raises(sqlite3.OperationalError, match="disk is on fire"):
            advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        sched._connection = real  # type: ignore[assignment]
        assert sched.load_run("run-1").state is RunState.CREATED
        assert sched.transition_count == 0


def test_a_failed_transition_leaves_no_partial_write(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        before = sched.transition_count
        with pytest.raises(SchedulerError):
            advance(sched, "run-1", RunState.CREATED, tid="t-2")
        assert sched.transition_count == before
        assert sched.load_run("run-1").state is RunState.WAITING_FOR_SESSION


# --------------------------------------------------------------------------
# 10. Privacy.
# --------------------------------------------------------------------------


def test_the_run_projection_carries_no_identity_or_date(tmp_path):
    with store(tmp_path) as sched:
        run = new_run(sched, "operator-chosen-name-12345678")
        facts = run.sanitized_facts(current_session_date=SESSION_DATE)
        rendered = repr(facts)
        assert "operator-chosen-name-12345678" not in rendered
        assert "2026-08-03" not in rendered
        assert facts["session_date_is_current"] == "true"


def test_projection_values_are_fixed_vocabulary(tmp_path):
    with store(tmp_path) as sched:
        run = new_run(sched)
        for key, value in run.sanitized_facts().items():
            assert isinstance(key, str) and isinstance(value, str)
            assert value in (
                {state.value for state in RunState}
                | {"true", "false", "NONE", "none", "one", "many"}
                | CLEARANCE_REASONS
                | {"SAFETY_HALT", "OPERATOR"}
            ), (key, value)


def test_store_projection_reports_buckets_not_counts(tmp_path):
    with store(tmp_path) as sched:
        assert sched.sanitized_facts()["scheduled_run_bucket"] == "none"
        new_run(sched, "a")
        assert sched.sanitized_facts()["scheduled_run_bucket"] == "one"
        new_run(sched, "b")
        new_run(sched, "c")
        assert sched.sanitized_facts()["scheduled_run_bucket"] == "many"


def test_no_reason_code_is_free_text():
    for reason in SchedulerReason:
        assert reason.value.replace("_", "").isalnum()
        assert reason.value == reason.value.upper()


def test_persisted_columns_cannot_carry_broker_or_private_values(tmp_path):
    """The schema has no column that could hold one."""
    with store(tmp_path) as sched:
        columns = {
            row[1].lower()
            for table in ("scheduled_runs", "scheduler_transitions")
            for row in sched._connection.execute(f"PRAGMA table_info({table})")
        }
        for forbidden in (
            "symbol", "quantity", "price", "balance", "account", "payload",
            "raw", "message", "credential", "token", "path", "manifest",
        ):
            assert not any(forbidden in column for column in columns), forbidden


# --------------------------------------------------------------------------
# 11. Adversarial probes. Each mutates a safety property and must be caught.
# --------------------------------------------------------------------------


def test_probe_closed_cannot_be_relabelled_regular(monkeypatch):
    """If the calendar claimed a weekend was regular, eligibility must still
    refuse it — the load-bearing boolean is checked, not just the name."""
    from src.exchange_calendar import (
        CalendarSession as CS,
        ClassificationReason,
        DayKind,
        SessionClassification,
    )

    monkeypatch.setattr(
        scheduler_module,
        "classify_calendar_session",
        lambda moment, today=None: SessionClassification(
            CS.REGULAR, DayKind.WEEKEND, ClassificationReason.WEEKEND, False
        ),
    )
    result = assess_calendar(SATURDAY)
    assert result.eligible is False
    assert result.session is CalendarSession.UNKNOWN


def test_probe_unknown_treated_as_eligible_is_impossible():
    from src.durable_scheduler import CalendarEligibility

    with pytest.raises(SchedulerError):
        CalendarEligibility(
            True, SchedulerReason.CALENDAR_SESSION_UNKNOWN, CalendarSession.UNKNOWN
        )


def test_probe_suspension_cleared_on_restart_is_caught(tmp_path):
    path = seed(tmp_path, RunState.SUSPENDED, suspension_reason="SAFETY_HALT")
    with DurableScheduler(path) as reopened:
        reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
        assert reopened.load_run("run-1").state is RunState.SUSPENDED
        decision = reopened.decide("run-1", now=REGULAR, facts=clean_facts())
        assert decision.action is SchedulerAction.SUSPEND


def test_probe_in_progress_run_cannot_resume_automatically(tmp_path):
    path = seed(tmp_path, RunState.RUNNING_OFFLINE_EVALUATION)
    with DurableScheduler(path) as reopened:
        reopened.recover_on_restart("run-1", transition_id="t-r", now=REGULAR)
        for target in RunState:
            with pytest.raises(SchedulerError):
                advance(reopened, "run-1", target, tid=f"t-{target.value}")


def test_probe_transition_identity_reused_after_restart_is_caught(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched, "run-a")
        advance(sched, "run-a", RunState.WAITING_FOR_SESSION, tid="t-shared")
    with DurableScheduler(path) as reopened:
        new_run(reopened, "run-b")
        with pytest.raises(SchedulerError):
            advance(reopened, "run-b", RunState.WAITING_FOR_SESSION, tid="t-shared")


def test_probe_terminal_run_cannot_return_to_waiting(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        advance(sched, "run-1", RunState.COMPLETED_NO_ACTION, tid="t-2")
        with pytest.raises(SchedulerError):
            advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-3")
        assert sched.load_run("run-1").state is RunState.COMPLETED_NO_ACTION


def test_probe_omitting_a_persisted_field_is_caught(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        sched._connection.execute(
            "UPDATE scheduled_runs SET created_at = '' WHERE run_id = ?", ("run-1",)
        )
        with pytest.raises(SchedulerError):
            sched.load_run("run-1")


def test_probe_unexpected_persisted_enum_is_caught(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        for injected in ("RUNNING", "running_offline_evaluation", "OPEN", "APPROVED"):
            sched._connection.execute(
                "UPDATE scheduled_runs SET state = ? WHERE run_id = ?",
                (injected, "run-1"),
            )
            with pytest.raises(SchedulerError):
                sched.load_run("run-1")


def test_probe_a_fabricated_permissive_result_is_refused():
    """Every action, not just the permissive one."""
    for action in SchedulerAction:
        with pytest.raises(SchedulerError):
            SchedulerDecision(
                action, SchedulerReason.OK, permits_automated_action=True
            )


def test_probe_a_dropped_monitoring_failure_still_blocks(tmp_path):
    """Unconfirmed delivery is an unknown, not silence."""
    result = decide(tmp_path, facts=clean_facts(monitoring_delivery_confirmed=False))
    assert result.action is SchedulerAction.REQUIRE_OPERATOR_REVIEW
    assert result.reason is SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED


def test_probe_the_scheduler_never_approves_activation_or_protection():
    from src.execution_gates import ACTIVATION_APPROVALS
    from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    from src.read_only_broker_state import approved_active_protection_states

    assert ACTIVATION_APPROVALS == frozenset()
    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()
    assert approved_active_protection_states() == frozenset()
    # The module names the constant only in prose explaining that it never
    # touches it; no code path references it.
    assert "ACTIVATION_APPROVALS" not in _code_identifiers(MODULE_PATH)


def test_probe_the_scheduler_is_not_in_the_milestone_9_pinned_closure():
    """Adding it would move the checkpoint digest, which this PR must not."""
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    assert "src/durable_scheduler.py" not in PINNED_IMPLEMENTATION_CLOSURE


# --------------------------------------------------------------------------
# 12. Public transition inspection.
# --------------------------------------------------------------------------


def test_a_persisted_transition_is_inspectable(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        receipt = sched.inspect_transition("t-1")
        assert receipt is not None
        assert receipt.transition_id == "t-1"
        assert receipt.run_id == "run-1"
        assert receipt.from_state is RunState.CREATED
        assert receipt.to_state is RunState.WAITING_FOR_SESSION
        assert receipt.sequence == 1
        assert receipt.persisted is True


def test_an_unknown_transition_is_not_found(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        assert sched.inspect_transition("never-happened") is None


@pytest.mark.parametrize("bad", ["", "   ", " t ", "x" * 129, None, 7, "t/1"])
def test_a_malformed_transition_identity_is_refused(tmp_path, bad):
    with store(tmp_path) as sched:
        with pytest.raises(SchedulerError):
            sched.inspect_transition(bad)


def test_a_receipt_is_immutable(tmp_path):
    from dataclasses import FrozenInstanceError

    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        receipt = sched.inspect_transition("t-1")
        with pytest.raises(FrozenInstanceError):
            receipt.to_state = RunState.SUSPENDED


def test_a_receipt_exposes_only_allowlisted_fields(tmp_path):
    from dataclasses import fields

    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        receipt = sched.inspect_transition("t-1")
        assert {f.name for f in fields(receipt)} == {
            "transition_id", "run_id", "from_state", "to_state", "sequence",
            "persisted",
        }


def test_a_receipt_carries_no_connection_cursor_path_or_sql(tmp_path):
    import sqlite3 as sqlite_module

    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        receipt = sched.inspect_transition("t-1")
        for value in vars(receipt).values():
            assert not isinstance(
                value, (sqlite_module.Connection, sqlite_module.Cursor, pathlib.Path)
            )
            if isinstance(value, str):
                assert "SELECT" not in value.upper()
                assert ".sqlite" not in value
        assert not hasattr(receipt, "_connection")


def test_a_receipt_cannot_claim_an_unpersisted_transition():
    from src.durable_scheduler import SchedulerTransitionReceipt

    with pytest.raises(SchedulerError):
        SchedulerTransitionReceipt(
            transition_id="t", run_id="r", from_state=RunState.CREATED,
            to_state=RunState.SUSPENDED, sequence=1, persisted=False,
        )


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5])
def test_a_receipt_refuses_a_nonsensical_sequence(sequence):
    from src.durable_scheduler import SchedulerTransitionReceipt

    with pytest.raises(SchedulerError):
        SchedulerTransitionReceipt(
            transition_id="t", run_id="r", from_state=RunState.CREATED,
            to_state=RunState.SUSPENDED, sequence=sequence,
        )


def test_inspection_survives_restart(tmp_path):
    path = tmp_path / "scheduler.sqlite"
    with DurableScheduler(path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
    with DurableScheduler(path) as reopened:
        receipt = reopened.inspect_transition("t-1")
        assert receipt is not None and receipt.run_id == "run-1"


def test_a_terminal_transition_remains_inspectable(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.FAILED_CLOSED, tid="t-final")
        receipt = sched.inspect_transition("t-final")
        assert receipt is not None
        assert receipt.to_state is RunState.FAILED_CLOSED


def test_inspection_does_not_make_an_identity_reusable(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        sched.inspect_transition("t-1")
        with pytest.raises(SchedulerError):
            advance(sched, "run-1", RunState.PRECONDITION_CHECK, tid="t-1")


def test_inspection_is_read_only(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        before = (sched.transition_count, sched.load_run("run-1").state)
        for _ in range(3):
            sched.inspect_transition("t-1")
            sched.inspect_transition("absent")
        assert (sched.transition_count, sched.load_run("run-1").state) == before


def test_a_malformed_persisted_transition_state_fails_closed(tmp_path):
    with store(tmp_path) as sched:
        new_run(sched)
        advance(sched, "run-1", RunState.WAITING_FOR_SESSION, tid="t-1")
        sched._connection.execute(
            "UPDATE scheduler_transitions SET to_state = 'ELSEWHERE' "
            "WHERE transition_id = ?",
            ("t-1",),
        )
        with pytest.raises(SchedulerError):
            sched.inspect_transition("t-1")
