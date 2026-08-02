"""Offline scenario harness — do the safety modules compose fail-closed?

Every test is local and offline. No broker, no network, no persistence.

Each module already fails closed alone. This asks the different question: does
the *arrangement*? A composition can be safe at every step and unsafe overall,
so these scenarios drive the whole path and assert one verdict.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime
from decimal import Decimal

import pytest

import src.offline_scenario_harness as harness_module
from src.exchange_calendar import EASTERN
from src.execution_gates import (
    REQUIRED_EVIDENCE,
    REQUIRED_PRECONDITIONS,
    Attestation,
    EvidenceSource,
    GatePreconditions,
    GateVerdict,
)
from src.offline_reconciliation import JournalRecord, OrderRole
from src.offline_scenario_harness import (
    EVALUATION_ORDER,
    Scenario,
    ScenarioError,
    ScenarioOutcome,
    ScenarioResult,
    run_scenario,
)
from src.operational_monitor import AlertCategory, AlertPolicy
from src.order_lifecycle_model import LifecycleState
from src.risk_limits import (
    AccountRiskState,
    ActionLedger,
    ActionProposal,
    KillSwitchState,
    RiskLimits,
)

MODULE_PATH = pathlib.Path(harness_module.__file__)

OPEN_MOMENT = datetime(2026, 8, 3, 11, 0, tzinfo=EASTERN)      # Monday, mid-session
CLOSED_MOMENT = datetime(2026, 8, 1, 11, 0, tzinfo=EASTERN)    # Saturday
HOLIDAY_MOMENT = datetime(2026, 12, 25, 11, 0, tzinfo=EASTERN)
EARLY_CLOSE_AFTERNOON = datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN)
UNCOVERED_MOMENT = datetime(2030, 8, 5, 11, 0, tzinfo=EASTERN)

NOW = 1000.0
FRESH = NOW - 10.0          # inside the 300s attestation window
RECENT = NOW - 1.0          # inside risk's much tighter 5s data-age bound
RUN = "scenario-run"


def accepted_source(fact: str) -> EvidenceSource:
    return sorted(REQUIRED_EVIDENCE[fact], key=lambda s: s.value)[0]


def preconditions(**overrides) -> GatePreconditions:
    values = {
        name: Attestation(True, accepted_source(name), FRESH)
        for name in REQUIRED_PRECONDITIONS
    }
    values.update(overrides)
    return GatePreconditions(**values)


def limits(**overrides) -> RiskLimits:
    values = {
        "maximum_position_quantity": 10,
        "maximum_order_value": Decimal("1000"),
        "maximum_daily_loss": Decimal("45"),
        "maximum_open_positions": 3,
    }
    values.update(overrides)
    return RiskLimits(**values)


def journal(**overrides) -> JournalRecord:
    values = {
        "run_id": RUN,
        "sequence": 1,
        "recorded_monotonic": NOW - 20.0,
        "role_states": {OrderRole.ENTRY.value: LifecycleState.FILLED.value},
    }
    values.update(overrides)
    return JournalRecord(**values)


def scenario(**overrides) -> Scenario:
    values = {
        "moment": OPEN_MOMENT,
        "proposal": ActionProposal(
            idempotency_key="a", quantity=1,
            order_value=Decimal("100"), observed_monotonic=RECENT,
        ),
        "limits": limits(),
        "account": AccountRiskState(
            realized_daily_loss=Decimal("0"), open_position_count=0
        ),
        "kill_switch": KillSwitchState.DISENGAGED,
        "ledger": ActionLedger(),
        "journal": journal(),
        "observed_roles": {OrderRole.ENTRY: LifecycleState.FILLED},
        "preconditions": preconditions(),
        "current_monotonic": NOW,
        "alert_policy": AlertPolicy(),
    }
    values.update(overrides)
    return Scenario(**values)


# --------------------------------------------------------------------------
# 1. The healthy path still ends closed.
# --------------------------------------------------------------------------


def test_a_fully_healthy_scenario_still_ends_at_a_closed_gate():
    """The only healthy result available today."""
    result = run_scenario(scenario())
    assert result.outcome is ScenarioOutcome.BLOCKED_GATE_CLOSED
    assert result.gate_decision.verdict is (
        GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    )
    assert result.permits_automated_action is False


def test_no_scenario_outcome_permits_action():
    for outcome in ScenarioOutcome:
        assert ScenarioResult(outcome, None).permits_automated_action is False


def test_a_permitting_result_cannot_be_constructed():
    with pytest.raises(ScenarioError):
        ScenarioResult(
            ScenarioOutcome.ALL_CHECKS_PASSED_GATE_CLOSED, None,
            permits_automated_action=True,
        )


# --------------------------------------------------------------------------
# 2. Calendar vetoes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment,expected",
    [
        (CLOSED_MOMENT, ScenarioOutcome.BLOCKED_MARKET_CLOSED),
        (HOLIDAY_MOMENT, ScenarioOutcome.BLOCKED_MARKET_CLOSED),
        (EARLY_CLOSE_AFTERNOON, ScenarioOutcome.BLOCKED_MARKET_CLOSED),
        (UNCOVERED_MOMENT, ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN),
    ],
)
def test_the_calendar_stops_everything_first(moment, expected):
    assert run_scenario(scenario(moment=moment)).outcome is expected


def test_a_stale_calendar_escalates_rather_than_guessing():
    """An uncovered date is not "probably fine"; it wakes somebody up."""
    result = run_scenario(scenario(moment=UNCOVERED_MOMENT))
    assert result.outcome is ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN
    assert AlertCategory.OPERATOR_ACTION_REQUIRED in result.alerts_raised


def test_a_closed_market_stops_before_any_account_reasoning():
    """A wildly illegal order on a Saturday reports the Saturday."""
    result = run_scenario(scenario(
        moment=CLOSED_MOMENT,
        proposal=ActionProposal(idempotency_key="a", quantity=9999,
                                order_value=Decimal("999999"),
                                observed_monotonic=RECENT),
        kill_switch=KillSwitchState.ENGAGED,
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_MARKET_CLOSED


# --------------------------------------------------------------------------
# 3. Kill switch.
# --------------------------------------------------------------------------


def test_the_kill_switch_stops_the_scenario():
    result = run_scenario(scenario(kill_switch=KillSwitchState.ENGAGED))
    assert result.outcome is ScenarioOutcome.BLOCKED_KILL_SWITCH
    assert AlertCategory.KILL_SWITCH_ENGAGED in result.alerts_raised


def test_the_kill_switch_beats_a_perfectly_legal_proposal():
    result = run_scenario(scenario(
        kill_switch=KillSwitchState.ENGAGED, preconditions=preconditions()
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_KILL_SWITCH
    assert result.gate_decision is None      # the gate was never consulted


def test_the_kill_switch_is_evaluated_before_sizing():
    """A suspended system should not be computing position sizes."""
    result = run_scenario(scenario(
        kill_switch=KillSwitchState.ENGAGED,
        proposal=ActionProposal(idempotency_key="a", quantity=9999,
                                order_value=Decimal("999999"),
                                observed_monotonic=RECENT),
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_KILL_SWITCH


# --------------------------------------------------------------------------
# 4. Risk vetoes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposal": ActionProposal(idempotency_key="a", quantity=99,
                                    order_value=Decimal("100"),
                                    observed_monotonic=RECENT)},
        {"proposal": ActionProposal(idempotency_key="a", quantity=1,
                                    order_value=Decimal("99999"),
                                    observed_monotonic=RECENT)},
        {"account": AccountRiskState(realized_daily_loss=Decimal("45"),
                                     open_position_count=0)},
        {"account": AccountRiskState(realized_daily_loss=Decimal("0"),
                                     open_position_count=3)},
        {"proposal": ActionProposal(idempotency_key="a", quantity=1,
                                    order_value=Decimal("100"),
                                    observed_monotonic=NOW - 9999.0)},
    ],
)
def test_risk_denial_stops_the_scenario(overrides):
    result = run_scenario(scenario(**overrides))
    assert result.outcome is ScenarioOutcome.BLOCKED_RISK
    assert AlertCategory.RISK_LIMIT_BREACHED in result.alerts_raised


def test_a_duplicate_action_stops_the_scenario():
    spent = ActionLedger()
    spent.record("a", 1)
    assert run_scenario(scenario(ledger=spent)).outcome is ScenarioOutcome.BLOCKED_RISK


def test_cumulative_exposure_stops_the_scenario():
    """An order legal alone but not on top of what was already approved."""
    at_ceiling = ActionLedger()
    at_ceiling.record("prior", 9)
    # 9 + 1 == the 10-unit ceiling exactly, so this one is still allowed.
    assert run_scenario(scenario(ledger=at_ceiling)).outcome is (
        ScenarioOutcome.BLOCKED_GATE_CLOSED
    )

    over = ActionLedger()
    over.record("prior", 10)
    assert run_scenario(scenario(ledger=over)).outcome is ScenarioOutcome.BLOCKED_RISK


# --------------------------------------------------------------------------
# 5. Recovery vetoes.
# --------------------------------------------------------------------------


def test_an_absent_journal_stops_the_scenario():
    result = run_scenario(scenario(journal=None))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY
    assert AlertCategory.RECOVERY_INCONSISTENT in result.alerts_raised


def test_a_replaced_role_stops_the_scenario():
    """The successor order is untracked; "nothing to do" would be wrong."""
    result = run_scenario(scenario(
        journal=journal(role_states={
            OrderRole.PROTECTIVE_STOP.value: LifecycleState.WORKING.value
        }),
        observed_roles={OrderRole.PROTECTIVE_STOP: LifecycleState.REPLACED},
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY


def test_a_vanished_non_terminal_role_stops_the_scenario():
    result = run_scenario(scenario(
        journal=journal(role_states={
            OrderRole.PROTECTIVE_STOP.value: LifecycleState.WORKING.value
        }),
        observed_roles={},
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY


def test_an_unknown_observed_state_stops_the_scenario():
    result = run_scenario(scenario(
        journal=journal(role_states={
            OrderRole.ENTRY.value: LifecycleState.WORKING.value
        }),
        observed_roles={OrderRole.ENTRY: "wibble"},
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY


def test_a_stale_journal_stops_the_scenario():
    result = run_scenario(scenario(
        journal=journal(recorded_monotonic=NOW - 99999.0)
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY


def test_an_impossible_transition_stops_the_scenario():
    result = run_scenario(scenario(
        journal=journal(role_states={
            OrderRole.ENTRY.value: LifecycleState.FILLED.value
        }),
        observed_roles={OrderRole.ENTRY: LifecycleState.CANCELED},
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_RECOVERY


# --------------------------------------------------------------------------
# 6. Monitoring delivery.
# --------------------------------------------------------------------------


def test_an_undelivered_alert_stops_the_scenario():
    """Proceeding while a warning is outstanding assumes it arrived."""
    policy = AlertPolicy()
    result = run_scenario(scenario(
        moment=UNCOVERED_MOMENT, alert_policy=policy
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN
    assert policy.all_delivered is False

    # A prior undelivered alert blocks an otherwise healthy scenario.
    blocked = run_scenario(scenario(alert_policy=policy))
    assert blocked.outcome is ScenarioOutcome.BLOCKED_MONITORING_UNDELIVERED


def test_confirming_delivery_unblocks_monitoring():
    policy = AlertPolicy()
    run_scenario(scenario(moment=UNCOVERED_MOMENT, alert_policy=policy))
    for category in policy.undelivered_categories:
        policy.confirm_delivery(category)
    assert run_scenario(scenario(alert_policy=policy)).outcome is (
        ScenarioOutcome.BLOCKED_GATE_CLOSED
    )


def test_a_delivery_failure_keeps_the_scenario_blocked():
    policy = AlertPolicy()
    run_scenario(scenario(kill_switch=KillSwitchState.ENGAGED, alert_policy=policy))
    policy.record_delivery_failure(AlertCategory.KILL_SWITCH_ENGAGED)
    assert run_scenario(scenario(alert_policy=policy)).outcome is (
        ScenarioOutcome.BLOCKED_MONITORING_UNDELIVERED
    )


# --------------------------------------------------------------------------
# 7. Gate closure, and evaluation order.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_PRECONDITIONS)
def test_any_unmet_precondition_still_ends_at_the_gate(name):
    result = run_scenario(scenario(preconditions=preconditions(
        **{name: Attestation(False, accepted_source(name), FRESH)}
    )))
    assert result.outcome is ScenarioOutcome.BLOCKED_GATE_CLOSED
    assert result.gate_decision.is_open is False


def test_the_gate_is_consulted_last():
    """Every earlier veto reports itself, never the gate."""
    earlier = [
        (scenario(moment=CLOSED_MOMENT), ScenarioOutcome.BLOCKED_MARKET_CLOSED),
        (scenario(kill_switch=KillSwitchState.ENGAGED),
         ScenarioOutcome.BLOCKED_KILL_SWITCH),
        (scenario(account=AccountRiskState(realized_daily_loss=Decimal("45"),
                                           open_position_count=0)),
         ScenarioOutcome.BLOCKED_RISK),
        (scenario(journal=None), ScenarioOutcome.BLOCKED_RECOVERY),
    ]
    for case, expected in earlier:
        result = run_scenario(case)
        assert result.outcome is expected
        assert result.gate_decision is None


def test_the_declared_evaluation_order_matches_the_implementation():
    """Reordering would describe a different system while passing unit tests."""
    assert EVALUATION_ORDER == (
        "calendar", "kill_switch", "risk", "recovery", "monitoring", "gate"
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    body = source.split("def run_scenario", 1)[1]
    positions = [
        body.index(marker) for marker in
        ("classify(scenario.moment)", "KillSwitchState.ENGAGED",
         "evaluate(", "recover(", "all_delivered", "open_gate(")
    ]
    assert positions == sorted(positions), positions


def test_a_later_stage_never_rescues_an_earlier_refusal():
    """Perfect gate evidence does not undo a closed market."""
    result = run_scenario(scenario(
        moment=CLOSED_MOMENT, preconditions=preconditions()
    ))
    assert result.outcome is ScenarioOutcome.BLOCKED_MARKET_CLOSED


# --------------------------------------------------------------------------
# 8. Malformed scenarios.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("moment", "2026-08-03"),
    ("ledger", object()),
    ("preconditions", "fine"),
    ("alert_policy", "policy"),
])
def test_a_malformed_scenario_is_refused(field, value):
    with pytest.raises(ScenarioError):
        scenario(**{field: value})


def test_running_a_non_scenario_is_refused():
    with pytest.raises(ScenarioError):
        run_scenario("everything is fine")


def test_a_naive_moment_is_refused_by_the_calendar_not_assumed_open():
    result = run_scenario(scenario(moment=datetime(2026, 8, 3, 11, 0)))
    assert result.outcome is ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN


# --------------------------------------------------------------------------
# 9. Privacy and independence.
# --------------------------------------------------------------------------

ALLOWED_FACT_VALUES = {
    "scenario_outcome": {o.value for o in ScenarioOutcome},
    "gate_verdict": {v.value for v in GateVerdict} | {"NOT_REACHED"},
    "alert_count_bucket": {"none", "one", "many"},
    "permits_automated_action": {"false"},
}


def test_scenario_facts_are_allowlisted():
    for case in (scenario(), scenario(moment=CLOSED_MOMENT),
                 scenario(kill_switch=KillSwitchState.ENGAGED)):
        facts = run_scenario(case).sanitized_facts()
        assert set(facts) == set(ALLOWED_FACT_VALUES)
        for key, value in facts.items():
            assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_facts_carry_no_quantity_symbol_price_or_key():
    result = run_scenario(scenario(
        proposal=ActionProposal(idempotency_key="SECRET-KEY-7", quantity=7,
                                order_value=Decimal("1234.56"),
                                observed_monotonic=RECENT)
    ))
    rendered = repr(result.sanitized_facts())
    for forbidden in ("SECRET-KEY-7", "1234.56", "AAPL"):
        assert forbidden not in rendered


def test_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    validate_sanitized_evidence(run_scenario(scenario()).sanitized_facts())


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_harness_imports_no_pinned_checkpoint_file():
    for pinned in ("read_only_observation_runtime", "read_only_broker_state",
                   "read_only_authorization_manifest", "live_order_state_model"):
        assert not any(pinned in name for name in _imports(MODULE_PATH)), pinned


def test_the_harness_has_no_loop_timer_retry_or_persistence():
    """A harness that grew those would have become the runtime it tests."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    body = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_scenario"
    )
    for node in ast.walk(body):
        assert not isinstance(node, (ast.While, ast.AsyncFor)), ast.dump(node)
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"time", "asyncio", "threading", "sched", "sqlite3", "socket",
         "http", "urllib", "requests", "subprocess", "os"}
    )


def test_the_harness_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.monotonic(" not in source


def test_the_harness_defines_no_write_shaped_function():
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


def test_no_broker_manifest_checkpoint_or_credential_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "local.yaml", ".rh_tokens", "password", "environ"):
        assert token not in source, token
