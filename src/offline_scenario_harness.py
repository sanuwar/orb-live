"""Offline scenario harness — do the safety modules actually compose?

Six modules now each fail closed on their own, and each is well tested in
isolation. That is not the same as the system failing closed. A composition
can be safe at every step and unsafe overall: the calendar says the market is
shut, the risk check says the order is fine, and nothing asks the calendar
before acting on the risk answer.

This harness wires the merged modules into one decision path and returns a
single outcome, so a scenario can assert what the *whole* arrangement does
rather than what one piece does.

## What it deliberately is not

Not a scheduler, not an executor, and not a step toward either. It takes a
fully described situation as input and returns a verdict. It has no loop, no
timer, no retry, no persistence, and no broker of any kind — a harness that
grew those would have quietly become the runtime it exists to test.

`permits_automated_action` is `False` on every outcome, and the gate is
consulted last precisely so that a scenario which somehow satisfies everything
else still ends closed.

## Evaluation order is the thing under test

Availability of facts, then market session, then kill switch, then risk, then
recovery consistency, then the gate. Each earlier stage can veto, and a later
stage never rescues an earlier refusal. A scenario that reorders these would
be describing a different system, so the order is asserted rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping

from src.exchange_calendar import CalendarSession, classify, is_regular_session
from src.execution_gates import (
    Attestation,
    GateDecision,
    GateMode,
    GatePreconditions,
    GateVerdict,
    open_gate,
)
from src.offline_reconciliation import (
    JournalRecord,
    RecoveryOutcome,
    recover,
)
from src.operational_monitor import (
    Alert,
    AlertCategory,
    AlertPolicy,
    AlertSeverity,
)
from src.risk_limits import (
    AccountRiskState,
    ActionLedger,
    ActionProposal,
    KillSwitchState,
    RiskDecision,
    RiskLimits,
    evaluate,
)


class ScenarioError(RuntimeError):
    """A malformed scenario. Never carries broker or account data."""


class ScenarioOutcome(str, Enum):
    """What the whole arrangement concluded. None of these permits action."""

    #: Every stage passed and the gate was still closed, which is the only
    #: healthy result available today.
    ALL_CHECKS_PASSED_GATE_CLOSED = "ALL_CHECKS_PASSED_GATE_CLOSED"

    BLOCKED_MARKET_CLOSED = "BLOCKED_MARKET_CLOSED"
    BLOCKED_CALENDAR_UNKNOWN = "BLOCKED_CALENDAR_UNKNOWN"
    BLOCKED_KILL_SWITCH = "BLOCKED_KILL_SWITCH"
    BLOCKED_RISK = "BLOCKED_RISK"
    BLOCKED_RECOVERY = "BLOCKED_RECOVERY"
    BLOCKED_MONITORING_UNDELIVERED = "BLOCKED_MONITORING_UNDELIVERED"
    BLOCKED_GATE_CLOSED = "BLOCKED_GATE_CLOSED"


#: The order stages are evaluated in. Asserted by test, because reordering it
#: would describe a different system while still passing every unit test.
EVALUATION_ORDER: tuple[str, ...] = (
    "calendar",
    "kill_switch",
    "risk",
    "recovery",
    "monitoring",
    "gate",
)


@dataclass(frozen=True)
class Scenario:
    """A fully described situation. Nothing is defaulted into existence."""

    moment: datetime
    proposal: ActionProposal
    limits: RiskLimits
    account: AccountRiskState
    kill_switch: KillSwitchState
    ledger: ActionLedger
    journal: JournalRecord | None
    observed_roles: Mapping[object, object]
    preconditions: GatePreconditions
    current_monotonic: float
    #: The run this scenario believes it is resuming. Supplied rather than
    #: read off the journal: deriving it from the record being checked made
    #: the identity comparison compare a value with itself, so a journal for
    #: an entirely different run could never be detected.
    expected_run_id: str
    alert_policy: AlertPolicy = field(default_factory=AlertPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.moment, datetime):
            raise ScenarioError("a timezone-aware moment is required")
        if not isinstance(self.ledger, ActionLedger):
            raise ScenarioError("an action ledger is required")
        if not isinstance(self.preconditions, GatePreconditions):
            raise ScenarioError("gate preconditions are required")
        if not isinstance(self.alert_policy, AlertPolicy):
            raise ScenarioError("an alert policy is required")
        if not isinstance(self.expected_run_id, str) or not self.expected_run_id.strip():
            raise ScenarioError("an expected run identity is required")


@dataclass(frozen=True)
class ScenarioResult:
    outcome: ScenarioOutcome
    gate_decision: GateDecision | None
    alerts_raised: tuple[AlertCategory, ...] = ()
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise ScenarioError("a scenario never authorizes automated action")

    @property
    def blocked(self) -> bool:
        return self.outcome is not ScenarioOutcome.ALL_CHECKS_PASSED_GATE_CLOSED

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "scenario_outcome": self.outcome.value,
            "gate_verdict": (
                self.gate_decision.verdict.value
                if self.gate_decision is not None
                else "NOT_REACHED"
            ),
            "alert_count_bucket": (
                "none" if not self.alerts_raised
                else "one" if len(self.alerts_raised) == 1
                else "many"
            ),
            "permits_automated_action": "false",
        }


def _raise(policy: AlertPolicy, category: AlertCategory, severity: AlertSeverity,
           moment: float, raised: list[AlertCategory]) -> None:
    alert = Alert(category, severity, {"component": "runtime", "condition": "failed"})
    if policy.should_send(alert, current_monotonic=moment):
        raised.append(category)


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Run one situation through every stage and return one verdict.

    Each stage can veto and none can rescue. The gate is consulted last so a
    scenario that satisfies everything else still ends closed — which is the
    result this whole programme is currently supposed to produce.
    """
    if not isinstance(scenario, Scenario):
        raise ScenarioError("a scenario is required")

    raised: list[AlertCategory] = []
    moment = scenario.current_monotonic

    # 1. Calendar. A market that is shut, or that the table cannot classify,
    #    stops everything before any account reasoning happens.
    session = classify(scenario.moment)
    if session.session is CalendarSession.UNKNOWN:
        _raise(scenario.alert_policy, AlertCategory.OPERATOR_ACTION_REQUIRED,
               AlertSeverity.CRITICAL, moment, raised)
        return ScenarioResult(
            ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN, None, tuple(raised)
        )
    if not is_regular_session(scenario.moment):
        return ScenarioResult(
            ScenarioOutcome.BLOCKED_MARKET_CLOSED, None, tuple(raised)
        )

    # 2. Kill switch, before any sizing arithmetic. A suspended system should
    #    not be computing position sizes.
    if scenario.kill_switch is KillSwitchState.ENGAGED:
        _raise(scenario.alert_policy, AlertCategory.KILL_SWITCH_ENGAGED,
               AlertSeverity.CRITICAL, moment, raised)
        return ScenarioResult(
            ScenarioOutcome.BLOCKED_KILL_SWITCH, None, tuple(raised)
        )

    # 3. Risk.
    assessment = evaluate(
        scenario.proposal, scenario.limits, scenario.account,
        kill_switch=scenario.kill_switch, ledger=scenario.ledger,
        current_monotonic=scenario.current_monotonic,
    )
    if assessment.decision is not RiskDecision.WITHIN_LIMITS:
        _raise(scenario.alert_policy, AlertCategory.RISK_LIMIT_BREACHED,
               AlertSeverity.WARNING, moment, raised)
        return ScenarioResult(ScenarioOutcome.BLOCKED_RISK, None, tuple(raised))

    # 4. Recovery. A run whose persisted and observed views disagree may not
    #    proceed even if everything about the *proposed* action is fine.
    recovery = recover(
        scenario.journal, scenario.observed_roles,
        expected_run_id=scenario.expected_run_id,
        current_monotonic=scenario.current_monotonic,
    )
    if recovery.requires_operator:
        _raise(scenario.alert_policy, AlertCategory.RECOVERY_INCONSISTENT,
               AlertSeverity.CRITICAL, moment, raised)
        return ScenarioResult(ScenarioOutcome.BLOCKED_RECOVERY, None, tuple(raised))

    # 5. Monitoring. An alert nobody received is not a delivered warning, and
    #    proceeding while one is outstanding assumes it arrived.
    if not scenario.alert_policy.all_delivered:
        return ScenarioResult(
            ScenarioOutcome.BLOCKED_MONITORING_UNDELIVERED, None, tuple(raised)
        )

    # 6. The gate, last.
    decision = open_gate(
        scenario.preconditions, mode=GateMode.SUPERVISED,
        current_monotonic=scenario.current_monotonic,
    )
    if decision.verdict is not GateVerdict.OPEN:
        return ScenarioResult(
            ScenarioOutcome.BLOCKED_GATE_CLOSED, decision, tuple(raised)
        )
    # Unreachable while ACTIVATION_APPROVALS is empty, and kept rather than
    # asserted away so the composition stays honest if that ever changes.
    return ScenarioResult(
        ScenarioOutcome.ALL_CHECKS_PASSED_GATE_CLOSED, decision, tuple(raised)
    )


__all__ = [
    "EVALUATION_ORDER",
    "Scenario",
    "ScenarioError",
    "ScenarioOutcome",
    "ScenarioResult",
    "run_scenario",
]
