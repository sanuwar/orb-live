"""Deterministic adversarial scenario generation.

The scenario harness proves the modules compose fail-closed across a list of
situations somebody thought to write down. That list is exactly as good as the
imagination of whoever wrote it, and the failures worth worrying about are the
combinations nobody pictured — a stale calendar *and* a replaced order, an
expired attestation *and* a spent key.

This generates those combinations mechanically instead.

## Deterministic, not random

`hypothesis` is not a dependency of this repository and adding one to write
tests would be a poor trade. So the axes below are enumerated rather than
sampled: the same set of scenarios is produced on every run, in the same
order, and a failure names the exact combination that produced it. A random
generator that finds a bug on Tuesday and not on Wednesday is worse than a
fixed list that finds it every time.

## The invariant under test

**Every generated scenario must be blocked, and none may permit action.**
There is currently no combination of inputs that should let anything proceed,
because the gate is inert — so a scenario that came back unblocked would mean
either the generator built something incoherent or a module stopped failing
closed. Both are worth a loud failure.

Single-fault scenarios additionally assert *which* stage blocked them. Without
that, the suite would pass just as happily if every scenario were blocked for
the wrong reason, which is the classic way a combinatorial test becomes
decoration.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterator, Mapping

from src.attestation_authority import (
    AttestationAuthority,
    ProducerIdentity,
)
from src.exchange_calendar import EASTERN
from src.execution_gates import (
    REQUIRED_EVIDENCE,
    REQUIRED_PRECONDITIONS,
    Attestation,
    EvidenceSource,
    GatePreconditions,
)
from src.offline_reconciliation import JournalRecord, OrderRole
from src.offline_scenario_harness import Scenario, ScenarioOutcome, run_scenario
from src.operational_monitor import AlertPolicy
from src.order_lifecycle_model import LifecycleState
from src.risk_limits import (
    AccountRiskState,
    ActionLedger,
    ActionProposal,
    KillSwitchState,
    RiskLimits,
)


class GeneratorError(RuntimeError):
    """A malformed axis definition. Never carries broker or account data."""


# --------------------------------------------------------------------------
# Baseline. Everything an axis does is a deviation from this.
# --------------------------------------------------------------------------

NOW = 1000.0
FRESH = NOW - 10.0            # inside the 300s attestation window
RECENT = NOW - 1.0            # inside risk's tighter 5s data-age bound
OPEN_MOMENT = datetime(2026, 8, 3, 11, 0, tzinfo=EASTERN)   # Monday mid-session
RUN_ID = "generated-run"

#: A fixture key. Never a real one, and never loaded from anywhere.
FIXTURE_KEY = b"deterministic-generator-fixture-key-01!!!"


def _accepted_source(fact: str) -> EvidenceSource:
    return sorted(REQUIRED_EVIDENCE[fact], key=lambda item: item.value)[0]


def baseline_authority() -> AttestationAuthority:
    return AttestationAuthority(
        FIXTURE_KEY,
        {
            "all": ProducerIdentity(
                "all",
                frozenset(REQUIRED_EVIDENCE),
                frozenset({
                    EvidenceSource.OPERATOR_ATTESTED,
                    EvidenceSource.MEASURED,
                    EvidenceSource.TESTED,
                }),
            )
        },
    )


def baseline_preconditions(**overrides: object) -> GatePreconditions:
    values: dict[str, object] = {
        name: Attestation(True, _accepted_source(name), FRESH)
        for name in REQUIRED_PRECONDITIONS
    }
    values.update(overrides)
    return GatePreconditions(**values)


def baseline_scenario(**overrides: object) -> Scenario:
    values: dict[str, object] = {
        "moment": OPEN_MOMENT,
        "proposal": ActionProposal(
            idempotency_key="generated-action",
            quantity=1,
            order_value=Decimal("100"),
            observed_monotonic=RECENT,
        ),
        "limits": RiskLimits(
            maximum_position_quantity=10,
            maximum_order_value=Decimal("1000"),
            maximum_daily_loss=Decimal("45"),
            maximum_open_positions=3,
        ),
        "account": AccountRiskState(
            realized_daily_loss=Decimal("0"), open_position_count=0
        ),
        "kill_switch": KillSwitchState.DISENGAGED,
        "ledger": ActionLedger(),
        "journal": JournalRecord(
            run_id=RUN_ID,
            sequence=1,
            recorded_monotonic=NOW - 20.0,
            role_states={OrderRole.ENTRY.value: LifecycleState.FILLED.value},
        ),
        "observed_roles": {OrderRole.ENTRY: LifecycleState.FILLED},
        "preconditions": baseline_preconditions(),
        "current_monotonic": NOW,
        "expected_run_id": RUN_ID,
        "alert_policy": AlertPolicy(),
    }
    values.update(overrides)
    return Scenario(**values)


# --------------------------------------------------------------------------
# Axes.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Fault:
    """One named deviation, and the stage that should catch it.

    `expected` is required for every fault. A fault whose expected stage was
    optional would quietly become a scenario that passes for any reason at
    all, which is the failure mode this whole module is meant to avoid.
    """

    axis: str
    label: str
    apply: Callable[[Scenario], Scenario]
    expected: ScenarioOutcome

    def __post_init__(self) -> None:
        for name in ("axis", "label"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise GeneratorError(f"a fault {name} is required")
        if not callable(self.apply):
            raise GeneratorError("a fault needs an apply function")
        if not isinstance(self.expected, ScenarioOutcome):
            raise GeneratorError("a fault needs an expected outcome")


def _spent_ledger(key: str = "generated-action", quantity: int = 1) -> ActionLedger:
    book = ActionLedger()
    book.record(key, quantity)
    return book


def _loaded_ledger(quantity: int) -> ActionLedger:
    book = ActionLedger()
    book.record("prior-exposure", quantity)
    return book


def _journal(role_states: Mapping[str, str], **overrides: object) -> JournalRecord:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "sequence": 1,
        "recorded_monotonic": NOW - 20.0,
        "role_states": dict(role_states),
    }
    values.update(overrides)
    return JournalRecord(**values)


def _with_precondition(name: str, attestation: Attestation) -> Callable[[Scenario], Scenario]:
    def apply(scenario: Scenario) -> Scenario:
        return replace(
            scenario, preconditions=baseline_preconditions(**{name: attestation})
        )
    return apply


CALENDAR_FAULTS: tuple[Fault, ...] = (
    Fault("calendar", "weekend",
          lambda s: replace(s, moment=datetime(2026, 8, 1, 11, 0, tzinfo=EASTERN)),
          ScenarioOutcome.BLOCKED_MARKET_CLOSED),
    Fault("calendar", "full_holiday",
          lambda s: replace(s, moment=datetime(2026, 12, 25, 11, 0, tzinfo=EASTERN)),
          ScenarioOutcome.BLOCKED_MARKET_CLOSED),
    Fault("calendar", "after_early_close",
          lambda s: replace(s, moment=datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN)),
          ScenarioOutcome.BLOCKED_MARKET_CLOSED),
    Fault("calendar", "outside_coverage",
          lambda s: replace(s, moment=datetime(2030, 8, 5, 11, 0, tzinfo=EASTERN)),
          ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN),
    Fault("calendar", "naive_timestamp",
          lambda s: replace(s, moment=datetime(2026, 8, 3, 11, 0)),
          ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN),
)

KILL_SWITCH_FAULTS: tuple[Fault, ...] = (
    Fault("kill_switch", "engaged",
          lambda s: replace(s, kill_switch=KillSwitchState.ENGAGED),
          ScenarioOutcome.BLOCKED_KILL_SWITCH),
)

RISK_FAULTS: tuple[Fault, ...] = (
    Fault("risk", "oversized_quantity",
          lambda s: replace(s, proposal=replace(s.proposal, quantity=99)),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "oversized_value",
          lambda s: replace(s, proposal=replace(s.proposal, order_value=Decimal("99999"))),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "daily_loss_reached",
          lambda s: replace(s, account=AccountRiskState(
              realized_daily_loss=Decimal("45"), open_position_count=0)),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "position_count_reached",
          lambda s: replace(s, account=AccountRiskState(
              realized_daily_loss=Decimal("0"), open_position_count=3)),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "stale_observation",
          lambda s: replace(s, proposal=replace(s.proposal, observed_monotonic=NOW - 9999.0)),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "future_observation",
          lambda s: replace(s, proposal=replace(s.proposal, observed_monotonic=NOW + 50.0)),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "duplicate_action",
          lambda s: replace(s, ledger=_spent_ledger()),
          ScenarioOutcome.BLOCKED_RISK),
    Fault("risk", "cumulative_exposure",
          lambda s: replace(s, ledger=_loaded_ledger(10)),
          ScenarioOutcome.BLOCKED_RISK),
)

RECOVERY_FAULTS: tuple[Fault, ...] = (
    Fault("recovery", "absent_journal",
          lambda s: replace(s, journal=None),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "replaced_successor",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.PROTECTIVE_STOP.value: LifecycleState.WORKING.value}),
              observed_roles={OrderRole.PROTECTIVE_STOP: LifecycleState.REPLACED}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "vanished_non_terminal_role",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.PROTECTIVE_STOP.value: LifecycleState.WORKING.value}),
              observed_roles={}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "unknown_observed_state",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.ENTRY.value: LifecycleState.WORKING.value}),
              observed_roles={OrderRole.ENTRY: "wibble"}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "conflicting_terminal_states",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.ENTRY.value: LifecycleState.FILLED.value}),
              observed_roles={OrderRole.ENTRY: LifecycleState.CANCELED}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "partial_fill_then_vanish",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.ENTRY.value: LifecycleState.PARTIALLY_FILLED.value}),
              observed_roles={}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "backward_transition",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.ENTRY.value: LifecycleState.WORKING.value}),
              observed_roles={OrderRole.ENTRY: LifecycleState.ACCEPTED}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "stale_journal",
          lambda s: replace(s, journal=_journal(
              {OrderRole.ENTRY.value: LifecycleState.FILLED.value},
              recorded_monotonic=NOW - 99999.0)),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "unexpected_extra_role",
          lambda s: replace(
              s,
              journal=_journal({OrderRole.ENTRY.value: LifecycleState.FILLED.value}),
              observed_roles={OrderRole.ENTRY: LifecycleState.FILLED,
                              OrderRole.EXIT: LifecycleState.WORKING}),
          ScenarioOutcome.BLOCKED_RECOVERY),
    Fault("recovery", "wrong_run_identity",
          lambda s: replace(s, journal=_journal(
              {OrderRole.ENTRY.value: LifecycleState.FILLED.value}, run_id="other-run")),
          ScenarioOutcome.BLOCKED_RECOVERY),
)

ATTESTATION_FAULTS: tuple[Fault, ...] = tuple(
    Fault("attestation", label,
          _with_precondition(name, attestation),
          ScenarioOutcome.BLOCKED_GATE_CLOSED)
    for label, name, attestation in (
        ("unsatisfied",
         "risk_within_limits",
         Attestation(False, _accepted_source("risk_within_limits"), FRESH)),
        ("stale_evidence",
         "strategy_enabled",
         Attestation(True, _accepted_source("strategy_enabled"), NOW - 100_000.0)),
        ("future_evidence",
         "kill_switch_disengaged",
         Attestation(True, _accepted_source("kill_switch_disengaged"), NOW + 500.0)),
        ("self_declared_source",
         "monitoring_healthy",
         Attestation(True, EvidenceSource.SELF_DECLARED, FRESH)),
        ("wrong_source",
         "protection_grounding_complete",
         Attestation(True, EvidenceSource.MEASURED, FRESH)),
    )
)

MONITORING_FAULTS: tuple[Fault, ...] = (
    Fault("monitoring", "undelivered_prior_alert",
          lambda s: replace(s, alert_policy=_undelivered_policy()),
          ScenarioOutcome.BLOCKED_MONITORING_UNDELIVERED),
)


def _undelivered_policy() -> AlertPolicy:
    """A policy carrying an alert nobody confirmed receiving."""
    policy = AlertPolicy()
    run_scenario(baseline_scenario(
        moment=datetime(2030, 8, 5, 11, 0, tzinfo=EASTERN), alert_policy=policy
    ))
    return policy


ALL_AXES: Mapping[str, tuple[Fault, ...]] = {
    "calendar": CALENDAR_FAULTS,
    "kill_switch": KILL_SWITCH_FAULTS,
    "risk": RISK_FAULTS,
    "recovery": RECOVERY_FAULTS,
    "attestation": ATTESTATION_FAULTS,
    "monitoring": MONITORING_FAULTS,
}


# --------------------------------------------------------------------------
# Generation.
# --------------------------------------------------------------------------


def single_faults() -> Iterator[Fault]:
    """Every fault, one at a time, in a stable order."""
    for axis in sorted(ALL_AXES):
        for fault in ALL_AXES[axis]:
            yield fault


def fault_pairs() -> Iterator[tuple[Fault, Fault]]:
    """Every pair drawn from two different axes, in a stable order.

    Pairs from the *same* axis are excluded because they would overwrite each
    other's mutation rather than combine, producing a scenario that only looks
    like it carries two faults.
    """
    axes = sorted(ALL_AXES)
    for first_axis, second_axis in itertools.combinations(axes, 2):
        for first in ALL_AXES[first_axis]:
            for second in ALL_AXES[second_axis]:
                yield first, second


def apply_faults(faults: tuple[Fault, ...]) -> Scenario:
    """Build a scenario carrying every supplied fault."""
    scenario = baseline_scenario()
    for fault in faults:
        scenario = fault.apply(scenario)
    return scenario


def generated_case_count() -> Mapping[str, int]:
    """How much ground the enumeration actually covers."""
    singles = sum(len(faults) for faults in ALL_AXES.values())
    pairs = sum(1 for _ in fault_pairs())
    return {"axes": len(ALL_AXES), "single_faults": singles, "fault_pairs": pairs}


__all__ = [
    "ALL_AXES",
    "ATTESTATION_FAULTS",
    "CALENDAR_FAULTS",
    "FIXTURE_KEY",
    "KILL_SWITCH_FAULTS",
    "MONITORING_FAULTS",
    "NOW",
    "RECOVERY_FAULTS",
    "RISK_FAULTS",
    "Fault",
    "GeneratorError",
    "apply_faults",
    "baseline_authority",
    "baseline_preconditions",
    "baseline_scenario",
    "fault_pairs",
    "generated_case_count",
    "single_faults",
]
