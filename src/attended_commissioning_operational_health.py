"""Fail-closed operational-health contract for attended commissioning (M14
Issue 5).

`src/operational_monitor.py` was built for a **durable, unattended process**:
`assess_heartbeat` turns "no beat was ever recorded" into `NEVER_STARTED`
rather than healthy, and `AlertPolicy` suppresses repeats *across a running
session*. Both of those are correct for the thing they were built for and
dishonest for the thing this module is about: one synchronous, attended,
one-shot commissioning command that starts, evaluates exactly one proposed
broker write, and exits. That command has never sent a prior heartbeat --
calling `assess_heartbeat(None, ...)` for it does not detect an unhealthy
process, it deterministically returns `NEVER_STARTED` every single time,
which would make wiring `execution_safety_governance_deferred_gates.
make_operational_health_gate` into the live commissioning path an
always-DENY gate dressed up as a health check. The same problem reaches
every other durable-process health concept in this repository --
`durable_runner_liveness.DurableRunnerLiveness` (keyed by heartbeat sequence
and expected-cycle cadence), `durable_alert_outbox.DurableAlertOutbox`
(claims/delivery evidence for a *running* alert pipeline), and
`durable_runner_liveness.DurableRunnerLiveness` and `durable_alert_outbox.
DurableAlertOutbox` above: neither can be read honestly by a process that
has no history to read. `durable_suspension_controller.
DurableSuspensionController` was in the same position when this module was
first written -- attended commissioning had no registered controller
identity or durable store convention yet (see `execution_safety_governance_
deferred_gates.make_suspension_gate`'s own docstring at the time). M14
Issues 3 and 7 (merged after this module) since established that identity/
store and wired it in directly as `execution_safety_governance.
gate_commissioning_suspension_clear` -- so as of those merges this signal is
no longer `UNSAFE_TO_INFER`; it reclassifies to duplicating an already-active
gate, exactly like repository/runtime integrity below. See `CLASSIFICATION`.

This module does not try to make any of that apply anyway. It defines the
narrow three-state result a synchronous, one-shot health read is honestly
capable of producing -- `PASS`, `BLOCK`, or `UNKNOWN` -- and a fail-closed
fold over zero or more such reads, where `UNKNOWN` is denial-equivalent and
can never silently become `PASS`. `AttendedHealthCheck` is the contract a
future, genuinely-synchronous signal would implement. Every signal this
module classifies either duplicates an already-active gate (repository/
runtime integrity via `gate_source_hash_verified`; durable commissioning
suspension via `gate_commissioning_suspension_clear`, both already wired
directly into `execution_safety_governance.py`, not through this module) or
requires evidence attended commissioning still does not honestly have (see
`CLASSIFICATION` below for the full reasoning per signal). Consequently no
adapter is registered here; `evaluate_attended_health(())` -- zero checks --
is itself a real, tested case (vacuous PASS), not a placeholder.

Never imported by `src/execution_safety_governance.py`,
`src/robinhood_commissioning_adapter.py`,
`src/one_share_commissioning_orchestrator.py`, or
`src/robinhood_commissioning_session.py`. Wiring a future check into the live
commissioning gate list is a separate, later milestone's explicit decision,
made once a genuinely synchronous signal exists -- not a side effect of this
module existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable


class AttendedHealthError(RuntimeError):
    """This contract's own construction contract was violated.

    Distinct from a BLOCK/UNKNOWN result: raised only when a caller passes a
    structurally invalid value to a constructor this module owns.
    `evaluate_attended_health` itself never raises for a misbehaving check --
    a check that raises resolves to UNKNOWN, not to an exception escaping.
    """


class AttendedHealthResult(str, Enum):
    """The only three answers a synchronous, one-shot health read may give.

    There is deliberately no fourth state. `PASS` means the check ran and
    found the thing it looked at healthy. `BLOCK` means it ran and found it
    unhealthy. `UNKNOWN` means it could not be established either way --
    the read failed, was malformed, or the check raised. `UNKNOWN` is never
    a synonym for `PASS`: "could not tell" is not "fine".
    """

    PASS = "PASS"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


#: Results a fail-closed consumer must treat exactly like a denial. `BLOCK`
#: is an explicit finding; `UNKNOWN` is the absence of one -- both stop a
#: write, and a caller must not special-case either back into proceeding.
DENIAL_EQUIVALENT_RESULTS: frozenset[AttendedHealthResult] = frozenset({
    AttendedHealthResult.BLOCK,
    AttendedHealthResult.UNKNOWN,
})


@dataclass(frozen=True)
class AttendedHealthCheckResult:
    """One named check's answer plus a fixed reason code. No free-text
    field: same discipline `operational_monitor.Alert` uses for the same
    reason -- a health read is exactly the kind of value that ends up in a
    log, and a log is exactly where an unreviewed string leaks something."""

    name: str
    result: AttendedHealthResult
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise AttendedHealthError("check name is required")
        if not isinstance(self.result, AttendedHealthResult):
            raise AttendedHealthError("check result is invalid")
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise AttendedHealthError("reason code is required")


@runtime_checkable
class AttendedHealthCheck(Protocol):
    """The contract a future genuinely-synchronous signal must implement.

    Called with no arguments and expected to return promptly: a check that
    itself blocks on I/O, a network call, or a durable-process history read
    is not what this Protocol is for (see the module docstring). Never
    raises by contract, though `evaluate_attended_health` tolerates one that
    does by resolving it to UNKNOWN rather than propagating.
    """

    def __call__(self) -> AttendedHealthCheckResult: ...


def passed(name: str) -> AttendedHealthCheckResult:
    return AttendedHealthCheckResult(name, AttendedHealthResult.PASS, "OK")


def blocked(name: str, reason_code: str) -> AttendedHealthCheckResult:
    return AttendedHealthCheckResult(name, AttendedHealthResult.BLOCK, reason_code)


def unknown(name: str, reason_code: str) -> AttendedHealthCheckResult:
    return AttendedHealthCheckResult(name, AttendedHealthResult.UNKNOWN, reason_code)


@dataclass(frozen=True)
class AttendedHealthAssessment:
    """The folded verdict over every check that ran, plus every individual
    result -- so a caller denied by this can report which check denied it
    without re-running anything."""

    outcome: AttendedHealthResult
    results: tuple[AttendedHealthCheckResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AttendedHealthResult):
            raise AttendedHealthError("assessment outcome is invalid")
        if not isinstance(self.results, tuple) or not all(
            isinstance(item, AttendedHealthCheckResult) for item in self.results
        ):
            raise AttendedHealthError("assessment results are invalid")

    @property
    def passes(self) -> bool:
        """True only for PASS. A caller must gate a write on this property,
        never on `outcome != AttendedHealthResult.BLOCK` -- that phrasing
        would let UNKNOWN slip through as if it were PASS."""
        return self.outcome is AttendedHealthResult.PASS


def evaluate_attended_health(
    checks: Sequence[AttendedHealthCheck],
) -> AttendedHealthAssessment:
    """Run every supplied check and fold the results into one fail-closed
    verdict.

    Mirrors `execution_safety_governance.evaluate_execution_safety`'s own
    discipline: a check that raises, and a check that returns something
    other than an `AttendedHealthCheckResult`, both resolve to UNKNOWN
    rather than propagating or being silently skipped -- this function's own
    contract is fail-closed, not merely the checks it runs.

    Folding: BLOCK if any check reports BLOCK (an explicit finding always
    wins); otherwise UNKNOWN if any check reports UNKNOWN (an unresolved
    read is never overridden by an unrelated PASS); otherwise PASS. An empty
    `checks` sequence folds to PASS -- there is nothing to be unhealthy, and
    that is the honest answer, not a stand-in for "not yet implemented".
    """
    results: list[AttendedHealthCheckResult] = []
    for check in checks:
        name = getattr(check, "__name__", None) or type(check).__name__
        try:
            outcome = check()
        except Exception:
            results.append(unknown(name, "UNKNOWN_HEALTH_CHECK_EXCEPTION"))
            continue
        if not isinstance(outcome, AttendedHealthCheckResult):
            results.append(unknown(name, "UNKNOWN_HEALTH_CHECK_RESULT_MALFORMED"))
            continue
        results.append(outcome)

    results_t = tuple(results)
    if any(item.result is AttendedHealthResult.BLOCK for item in results_t):
        outcome = AttendedHealthResult.BLOCK
    elif any(item.result is AttendedHealthResult.UNKNOWN for item in results_t):
        outcome = AttendedHealthResult.UNKNOWN
    else:
        outcome = AttendedHealthResult.PASS
    return AttendedHealthAssessment(outcome, results_t)


# --------------------------------------------------------------------------
# Classification (M14 Issue 5). Reasoning lives here, in code, rather than
# only in a PR description, so it survives as long as the module does.
# --------------------------------------------------------------------------

#: signal -> bucket. Buckets are exactly the four the M14 Issue 5 objective
#: defines. Nothing here is enforced by this dict at runtime -- its purpose
#: is to keep the classification next to the contract it justifies, and to
#: give tests one place to assert the reasoning did not silently drift.
RELEVANT_BEFORE_A_COMMISSIONING_WRITE = "RELEVANT_BEFORE_A_COMMISSIONING_WRITE"
RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES = (
    "RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES"
)
INFORMATIONAL_ONLY = "INFORMATIONAL_ONLY"
UNSAFE_TO_INFER = "UNSAFE_TO_INFER"

CLASSIFICATION: dict[str, str] = {
    # operational_monitor.py
    "operational_monitor.assess_heartbeat / HeartbeatStatus / HeartbeatAssessment": (
        RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES
    ),
    "operational_monitor.AlertPolicy (flap suppression, undelivered_categories)": (
        RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES
    ),
    "operational_monitor.Alert / AlertCategory / AlertSeverity / render": (
        INFORMATIONAL_ONLY
    ),
    # durable_runner_liveness.py -- the bounded offline runner's own
    # heartbeat-sequence-keyed liveness, not attended commissioning's.
    "durable_runner_liveness.DurableRunnerLiveness / LivenessState": (
        RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES
    ),
    # durable_alert_outbox.py -- real and durable, but wired to the offline
    # safety orchestrator's status/liveness/suspension projections; nothing
    # in the commissioning write path creates or claims an alert through it.
    "durable_alert_outbox.DurableAlertOutbox / AlertState (critical incident state)": (
        RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES
    ),
    # durable_suspension_controller.py -- a synchronous read of already-
    # durable state was, in principle, exactly the kind of check a one-shot
    # invocation could honestly make (no heartbeat history required), and
    # was UNSAFE_TO_INFER only because attended commissioning had no
    # registered controller identity or durable store convention when this
    # module was written. M14 Issues 3 and 7 (merged after this module)
    # established that identity/store and wired it in directly as
    # execution_safety_governance.gate_commissioning_suspension_clear --
    # NOT through this operational-health module. Reclassified accordingly:
    # duplicating an already-active gate, exactly like repository/runtime
    # integrity below.
    "durable_suspension_controller.ControllerState (stale controller state)": (
        RELEVANT_BEFORE_A_COMMISSIONING_WRITE
        + " (already active via gate_commissioning_suspension_clear as of "
          "M14 Issue 7; out of scope to duplicate here)"
    ),
    # Repository/runtime integrity is already an active, wired signal:
    # execution_safety_governance.gate_source_hash_verified, backed by
    # CommissioningGovernanceEvidence.source_hash_verified. Duplicating it
    # here would create two independent judgments of the same fact with no
    # defined precedence, which is a hazard, not a redundancy.
    "repository/runtime integrity (source hash / implementation digest)": (
        RELEVANT_BEFORE_A_COMMISSIONING_WRITE
        + " (already active via gate_source_hash_verified; out of scope to duplicate)"
    ),
    # No durable local store exists anywhere in the commissioning write path
    # today (robinhood_commissioning_adapter.py, one_share_commissioning_
    # orchestrator.py, robinhood_commissioning_session.py -- none imports
    # sqlite3 or a ledger module). one_share_commissioning.AtomicEvidenceStore
    # is the one local persistence the flow does use, and it already fails
    # closed on its own (EVIDENCE_PERSISTENCE_FAILED) without help from this
    # module. There is nothing else to health-check.
    "local persistence health (a durable store's read/write health)": UNSAFE_TO_INFER,
    # A synchronous process is alive for as long as it is executing the
    # check that would ask the question -- "is this process alive" is
    # tautologically true from inside its own execution and carries zero
    # discriminating information.
    "process liveness (is this invocation's own process alive)": INFORMATIONAL_ONLY,
    # External telemetry (broker connectivity, market-data feed, etc.)
    # requires a network call. This module -- and the commissioning write
    # boundary generally -- never makes one; CLAUDE.md and the M14 Issue 1
    # governance module both prohibit it outright for this flow.
    "external telemetry availability (broker/network reachability)": (
        UNSAFE_TO_INFER
    ),
}


__all__ = [
    "AttendedHealthError",
    "AttendedHealthResult",
    "DENIAL_EQUIVALENT_RESULTS",
    "AttendedHealthCheckResult",
    "AttendedHealthCheck",
    "AttendedHealthAssessment",
    "passed",
    "blocked",
    "unknown",
    "evaluate_attended_health",
    "RELEVANT_BEFORE_A_COMMISSIONING_WRITE",
    "RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES",
    "INFORMATIONAL_ONLY",
    "UNSAFE_TO_INFER",
    "CLASSIFICATION",
]
