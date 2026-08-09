"""Tests for the M14 Issue 5 attended-commissioning operational-health
contract.

Two things are under test. First, the fold semantics of
`evaluate_attended_health` on its own terms: BLOCK dominates, UNKNOWN is
denial-equivalent and never silently becomes PASS, a raising or malformed
check resolves to UNKNOWN rather than propagating or vanishing, and an empty
check sequence is a real (not placeholder) PASS. Second, the applicability
claim the module's docstring makes: every signal classified
`RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES` or `UNSAFE_TO_INFER` is
proven, structurally, to be something this contract does not require --
the module's own source never imports or calls it, and its CLASSIFICATION
table -- read directly, not re-typed here -- is exactly what the docstring
claims.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import src.attended_commissioning_operational_health as attended_health
from src.attended_commissioning_operational_health import (
    CLASSIFICATION,
    DENIAL_EQUIVALENT_RESULTS,
    INFORMATIONAL_ONLY,
    RELEVANT_BEFORE_A_COMMISSIONING_WRITE,
    RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES,
    UNSAFE_TO_INFER,
    AttendedHealthAssessment,
    AttendedHealthCheckResult,
    AttendedHealthError,
    AttendedHealthResult,
    blocked,
    evaluate_attended_health,
    passed,
    unknown,
)

MODULE_PATH = pathlib.Path(attended_health.__file__)


# --------------------------------------------------------------------------
# 1. The result type itself: no fourth state, no permissive construction.
# --------------------------------------------------------------------------


def test_result_has_exactly_three_members():
    assert {member.value for member in AttendedHealthResult} == {
        "PASS", "BLOCK", "UNKNOWN",
    }


def test_denial_equivalent_is_block_and_unknown_only():
    assert DENIAL_EQUIVALENT_RESULTS == {
        AttendedHealthResult.BLOCK, AttendedHealthResult.UNKNOWN,
    }
    assert AttendedHealthResult.PASS not in DENIAL_EQUIVALENT_RESULTS


def test_check_result_rejects_wrong_result_type():
    with pytest.raises(AttendedHealthError):
        AttendedHealthCheckResult("x", "PASS", "OK")  # a string, not the enum


def test_check_result_rejects_empty_name_or_reason():
    with pytest.raises(AttendedHealthError):
        AttendedHealthCheckResult("", AttendedHealthResult.PASS, "OK")
    with pytest.raises(AttendedHealthError):
        AttendedHealthCheckResult("x", AttendedHealthResult.PASS, "")


def test_assessment_rejects_non_result_outcome():
    with pytest.raises(AttendedHealthError):
        AttendedHealthAssessment("PASS", ())


def test_assessment_rejects_non_tuple_results():
    with pytest.raises(AttendedHealthError):
        AttendedHealthAssessment(AttendedHealthResult.PASS, [])


def test_helpers_produce_the_expected_result():
    assert passed("x").result is AttendedHealthResult.PASS
    assert blocked("x", "R").result is AttendedHealthResult.BLOCK
    assert unknown("x", "R").result is AttendedHealthResult.UNKNOWN


# --------------------------------------------------------------------------
# 2. Fold semantics.
# --------------------------------------------------------------------------


def test_empty_checks_fold_to_a_real_pass():
    assessment = evaluate_attended_health(())
    assert assessment.outcome is AttendedHealthResult.PASS
    assert assessment.results == ()
    assert assessment.passes is True


def test_all_pass_folds_to_pass():
    assessment = evaluate_attended_health(
        (lambda: passed("a"), lambda: passed("b"))
    )
    assert assessment.outcome is AttendedHealthResult.PASS
    assert assessment.passes is True


def test_any_block_folds_to_block_even_with_other_passes():
    assessment = evaluate_attended_health(
        (lambda: passed("a"), lambda: blocked("b", "DENY_B"), lambda: passed("c"))
    )
    assert assessment.outcome is AttendedHealthResult.BLOCK
    assert assessment.passes is False


def test_unknown_without_block_folds_to_unknown_not_pass():
    assessment = evaluate_attended_health(
        (lambda: passed("a"), lambda: unknown("b", "UNKNOWN_B"))
    )
    assert assessment.outcome is AttendedHealthResult.UNKNOWN
    assert assessment.passes is False


def test_block_dominates_unknown():
    assessment = evaluate_attended_health(
        (lambda: unknown("a", "UNKNOWN_A"), lambda: blocked("b", "DENY_B"))
    )
    assert assessment.outcome is AttendedHealthResult.BLOCK


def test_a_raising_check_resolves_to_unknown_not_an_exception():
    def _explodes() -> AttendedHealthCheckResult:
        raise RuntimeError("simulated read failure")

    assessment = evaluate_attended_health((_explodes,))
    assert assessment.outcome is AttendedHealthResult.UNKNOWN
    assert assessment.results[0].reason_code == "UNKNOWN_HEALTH_CHECK_EXCEPTION"
    assert assessment.passes is False


def test_a_malformed_return_resolves_to_unknown_not_pass():
    def _malformed():
        return "PASS"  # not an AttendedHealthCheckResult

    assessment = evaluate_attended_health((_malformed,))
    assert assessment.outcome is AttendedHealthResult.UNKNOWN
    assert assessment.results[0].reason_code == (
        "UNKNOWN_HEALTH_CHECK_RESULT_MALFORMED"
    )


def test_unknown_is_never_silently_treated_as_pass_by_a_consuming_harness():
    """A minimal stand-in for a real call-site: gate a write on `.passes`,
    never on `outcome is not BLOCK`. Both UNKNOWN-producing shapes above,
    and an explicit UNKNOWN check, must all refuse the write."""

    def write_is_permitted(assessment: AttendedHealthAssessment) -> bool:
        return assessment.passes

    for checks in (
        (lambda: unknown("a", "R"),),
        (lambda: (_ for _ in ()).throw(RuntimeError("boom")),),
    ):
        assessment = evaluate_attended_health(checks)
        assert assessment.outcome in DENIAL_EQUIVALENT_RESULTS
        assert write_is_permitted(assessment) is False


# --------------------------------------------------------------------------
# 3. Applicability: durable/unattended-only and unsafe-to-infer signals are
#    genuinely not required by this contract -- structurally, not just by
#    docstring claim.
# --------------------------------------------------------------------------

#: Names this module's source must never reference. Each is either a
#: durable/unattended-process concept (heartbeat, liveness, outbox,
#: suspension controller) or a network/broker capability -- exactly what
#: CLASSIFICATION marks as not required for a synchronous one-shot read.
FORBIDDEN_IDENTIFIERS = frozenset({
    "assess_heartbeat",
    "HeartbeatStatus",
    "HeartbeatAssessment",
    "AlertPolicy",
    "DurableRunnerLiveness",
    "LivenessState",
    "DurableAlertOutbox",
    "DurableSuspensionController",
    "ControllerState",
    "call_tool",
    "robinhood_client",
    "RobinhoodClient",
})

FORBIDDEN_IMPORT_MODULES = frozenset({
    "src.durable_runner_liveness",
    "src.durable_alert_outbox",
    "src.durable_suspension_controller",
    "src.robinhood_client",
    "src.robinhood_commissioning_session",
    "src.robinhood_commissioning_adapter",
    "src.one_share_commissioning_orchestrator",
})


def _module_tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def test_module_imports_nothing_durable_or_network_capable():
    tree = _module_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORT_MODULES, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in FORBIDDEN_IMPORT_MODULES, module


def test_module_never_names_a_durable_or_broker_identifier():
    """Every forbidden identifier is absent from the module's own AST names
    -- not merely absent from its imports. A `Name`/`Attribute` reference
    without an import would still mean the concept leaked in some other
    way (e.g. through a string or a local re-definition); this proves it
    did not, for every identifier this contract's docstring claims it does
    not require."""
    tree = _module_tree()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    offending = referenced & FORBIDDEN_IDENTIFIERS
    assert not offending, offending


def test_operational_monitor_heartbeat_never_started_for_a_fresh_invocation():
    """Documents, by direct demonstration, exactly why heartbeat freshness
    is classified RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES: a one-shot
    invocation has no prior beat, and `assess_heartbeat(None, ...)`
    deterministically returns NEVER_STARTED regardless of actual health --
    it is not a health read for this flow, it is a constant."""
    from src.operational_monitor import HeartbeatStatus, assess_heartbeat

    first = assess_heartbeat(None, current_monotonic=1000.0, interval_seconds=5.0)
    second = assess_heartbeat(None, current_monotonic=999999.0, interval_seconds=5.0)
    assert first.status is HeartbeatStatus.NEVER_STARTED
    assert second.status is HeartbeatStatus.NEVER_STARTED


def test_classification_covers_every_investigated_signal_in_exactly_one_bucket():
    valid_buckets = {
        RELEVANT_BEFORE_A_COMMISSIONING_WRITE,
        RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES,
        INFORMATIONAL_ONLY,
        UNSAFE_TO_INFER,
    }
    assert CLASSIFICATION, "classification table must not be empty"
    for signal, bucket in CLASSIFICATION.items():
        assert isinstance(signal, str) and signal
        assert any(bucket.startswith(valid) for valid in valid_buckets), (
            signal, bucket,
        )


def test_no_signal_is_classified_required_and_available_without_an_adapter():
    """The module docstring claims no signal cleared the bar for a wired
    adapter as of this module's introduction. This is the structural half
    of that claim: nothing in CLASSIFICATION is bucketed as squarely
    RELEVANT_BEFORE_A_COMMISSIONING_WRITE without the accompanying
    already-active/duplicate-avoidance qualifier -- i.e. no entry claims to
    be a *new*, unqualified, required-and-available synchronous signal."""
    for signal, bucket in CLASSIFICATION.items():
        if bucket.startswith(RELEVANT_BEFORE_A_COMMISSIONING_WRITE):
            assert bucket != RELEVANT_BEFORE_A_COMMISSIONING_WRITE, (
                f"{signal} claims to be a new required-and-available signal; "
                "it must document why (already active elsewhere, etc.) or "
                "be wired with a real, tested adapter instead of listed bare"
            )


def test_durable_suspension_controller_state_reclassified_after_m14_issue_7():
    """Was UNSAFE_TO_INFER when this module was written (no commissioning
    suspension identity/durable store existed yet). M14 Issues 3 and 7
    (merged after this module) established that identity/store and wired
    it in directly as execution_safety_governance.
    gate_commissioning_suspension_clear -- so this signal now duplicates an
    already-active gate rather than being unsafe to infer. Never copy the
    historical UNSAFE_TO_INFER value forward once the underlying repository
    state it documented has changed."""
    key = "durable_suspension_controller.ControllerState (stale controller state)"
    assert CLASSIFICATION[key] == (
        RELEVANT_BEFORE_A_COMMISSIONING_WRITE
        + " (already active via gate_commissioning_suspension_clear as of "
          "M14 Issue 7; out of scope to duplicate here)"
    )


def test_heartbeat_and_liveness_signals_are_durable_unattended_only():
    durable_only_keys = (
        "operational_monitor.assess_heartbeat / HeartbeatStatus / HeartbeatAssessment",
        "operational_monitor.AlertPolicy (flap suppression, undelivered_categories)",
        "durable_runner_liveness.DurableRunnerLiveness / LivenessState",
        "durable_alert_outbox.DurableAlertOutbox / AlertState (critical incident state)",
    )
    for key in durable_only_keys:
        assert CLASSIFICATION[key] == RELEVANT_ONLY_TO_DURABLE_UNATTENDED_PROCESSES


# --------------------------------------------------------------------------
# 4. This module is not imported by the live commissioning write path.
# --------------------------------------------------------------------------


def test_not_imported_by_the_live_commissioning_write_path():
    live_modules = (
        pathlib.Path("src/execution_safety_governance.py"),
        pathlib.Path("src/robinhood_commissioning_adapter.py"),
        pathlib.Path("src/one_share_commissioning_orchestrator.py"),
        pathlib.Path("src/robinhood_commissioning_session.py"),
    )
    root = MODULE_PATH.parent.parent
    for relative in live_modules:
        source = (root / relative).read_text(encoding="utf-8-sig")
        assert "attended_commissioning_operational_health" not in source, relative
