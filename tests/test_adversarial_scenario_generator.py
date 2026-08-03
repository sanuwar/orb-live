"""Deterministic adversarial scenario generation.

Every test is local and offline. No new dependency: `hypothesis` is not
available here, so the axes are enumerated rather than sampled.

Two properties matter most:

1. **Every generated scenario is blocked**, across every single fault and
   every cross-axis pair.
2. **Single faults are blocked by the stage that should catch them.** Without
   that, the suite would pass just as happily if everything were blocked for
   the wrong reason.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace

import pytest

import src.adversarial_scenario_generator as generator_module
from src.adversarial_scenario_generator import (
    ALL_AXES,
    FIXTURE_KEY,
    Fault,
    GeneratorError,
    apply_faults,
    baseline_authority,
    baseline_preconditions,
    baseline_scenario,
    fault_pairs,
    generated_case_count,
    single_faults,
)
from src.offline_scenario_harness import (
    Scenario,
    ScenarioError,
    ScenarioOutcome,
    run_scenario,
)

MODULE_PATH = pathlib.Path(generator_module.__file__)

SINGLES = tuple(single_faults())
PAIRS = tuple(fault_pairs())


# --------------------------------------------------------------------------
# 1. The baseline is healthy, so a fault is what makes a scenario fail.
# --------------------------------------------------------------------------


def test_the_baseline_reaches_the_gate_and_stops_there():
    """If the baseline were already blocked, every fault would prove nothing."""
    result = run_scenario(baseline_scenario())
    assert result.outcome is ScenarioOutcome.BLOCKED_GATE_CLOSED
    assert result.gate_decision is not None
    assert result.gate_decision.is_open is False
    assert result.permits_automated_action is False


def test_the_baseline_preconditions_are_all_satisfied():
    from src.execution_gates import REQUIRED_PRECONDITIONS

    record = baseline_preconditions()
    assert record.unmet(current_monotonic=generator_module.NOW) == ()
    assert len(REQUIRED_PRECONDITIONS) == 9


# --------------------------------------------------------------------------
# 2. Single faults are caught by the right stage.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fault", SINGLES, ids=lambda f: f"{f.axis}.{f.label}")
def test_each_single_fault_is_caught_by_its_expected_stage(fault):
    result = run_scenario(apply_faults((fault,)))
    assert result.outcome is fault.expected
    assert result.blocked is True
    assert result.permits_automated_action is False


def test_every_axis_contributes_at_least_one_fault():
    for axis, faults in ALL_AXES.items():
        assert faults, axis
        assert all(f.axis == axis for f in faults), axis


def test_the_enumeration_covers_the_expected_ground():
    counts = generated_case_count()
    assert counts["axes"] == 6
    assert counts["single_faults"] == 30
    assert counts["fault_pairs"] == 342
    assert len(SINGLES) == counts["single_faults"]
    assert len(PAIRS) == counts["fault_pairs"]


def test_the_required_adversarial_conditions_are_all_present():
    """Named individually so dropping one is a visible diff, not a silent gap."""
    labels = {f"{fault.axis}.{fault.label}" for fault in SINGLES}
    for required in (
        "attestation.stale_evidence",
        "attestation.future_evidence",
        "attestation.self_declared_source",
        "attestation.wrong_source",
        "risk.duplicate_action",
        "risk.cumulative_exposure",
        "risk.stale_observation",
        "risk.future_observation",
        "recovery.partial_fill_then_vanish",
        "recovery.replaced_successor",
        "recovery.conflicting_terminal_states",
        "recovery.backward_transition",
        "recovery.stale_journal",
        "recovery.wrong_run_identity",
        "calendar.outside_coverage",
        "calendar.after_early_close",
        "monitoring.undelivered_prior_alert",
        "kill_switch.engaged",
    ):
        assert required in labels, required


# --------------------------------------------------------------------------
# 3. Combinations.
# --------------------------------------------------------------------------


def test_every_cross_axis_pair_is_blocked():
    """342 combinations, none of which may proceed."""
    checked = 0
    for first, second in PAIRS:
        result = run_scenario(apply_faults((first, second)))
        assert result.blocked is True, (first.label, second.label)
        assert result.permits_automated_action is False, (first.label, second.label)
        checked += 1
    assert checked == 342


def test_pairs_never_come_from_the_same_axis():
    """Same-axis faults would overwrite rather than combine."""
    for first, second in PAIRS:
        assert first.axis != second.axis


def test_a_pair_is_blocked_by_whichever_stage_comes_first():
    """Ordering holds under combination, not just in isolation."""
    stage_rank = {
        ScenarioOutcome.BLOCKED_CALENDAR_UNKNOWN: 0,
        ScenarioOutcome.BLOCKED_MARKET_CLOSED: 0,
        ScenarioOutcome.BLOCKED_KILL_SWITCH: 1,
        ScenarioOutcome.BLOCKED_RISK: 2,
        ScenarioOutcome.BLOCKED_RECOVERY: 3,
        ScenarioOutcome.BLOCKED_MONITORING_UNDELIVERED: 4,
        ScenarioOutcome.BLOCKED_GATE_CLOSED: 5,
    }
    for first, second in PAIRS:
        result = run_scenario(apply_faults((first, second)))
        earliest = min(stage_rank[first.expected], stage_rank[second.expected])
        assert stage_rank[result.outcome] == earliest, (
            first.label, second.label, result.outcome
        )


def test_generation_is_deterministic():
    """The same scenarios, in the same order, on every run."""
    first_pass = [(f.axis, f.label) for f in single_faults()]
    second_pass = [(f.axis, f.label) for f in single_faults()]
    assert first_pass == second_pass
    assert [(a.label, b.label) for a, b in fault_pairs()] == [
        (a.label, b.label) for a, b in fault_pairs()
    ]


def test_applying_no_faults_returns_the_baseline_outcome():
    assert run_scenario(apply_faults(())).outcome is (
        ScenarioOutcome.BLOCKED_GATE_CLOSED
    )


# --------------------------------------------------------------------------
# 4. The run-identity defect this generator found.
# --------------------------------------------------------------------------


def test_a_journal_for_another_run_is_detected():
    """Found by generation: the harness used to compare a value with itself.

    `expected_run_id` was read off the journal being checked, so a journal for
    an entirely different run could never mismatch. It is supplied separately
    now.
    """
    from src.offline_reconciliation import JournalRecord, OrderRole
    from src.order_lifecycle_model import LifecycleState

    scenario = baseline_scenario(
        journal=JournalRecord(
            run_id="a-completely-different-run",
            sequence=1,
            recorded_monotonic=generator_module.NOW - 20.0,
            role_states={OrderRole.ENTRY.value: LifecycleState.FILLED.value},
        )
    )
    assert run_scenario(scenario).outcome is ScenarioOutcome.BLOCKED_RECOVERY


def test_the_scenario_requires_an_explicit_run_identity():
    with pytest.raises(TypeError):
        Scenario(
            moment=baseline_scenario().moment,
            proposal=baseline_scenario().proposal,
            limits=baseline_scenario().limits,
            account=baseline_scenario().account,
            kill_switch=baseline_scenario().kill_switch,
            ledger=baseline_scenario().ledger,
            journal=baseline_scenario().journal,
            observed_roles=baseline_scenario().observed_roles,
            preconditions=baseline_scenario().preconditions,
            current_monotonic=baseline_scenario().current_monotonic,
        )


@pytest.mark.parametrize("value", ["", "   ", None, 7])
def test_a_malformed_run_identity_is_refused(value):
    with pytest.raises(ScenarioError):
        baseline_scenario(expected_run_id=value)


# --------------------------------------------------------------------------
# 5. Fault definitions are well formed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "axis,label,apply,expected",
    [
        ("", "l", lambda s: s, ScenarioOutcome.BLOCKED_RISK),
        ("a", "", lambda s: s, ScenarioOutcome.BLOCKED_RISK),
        ("a", "l", "not callable", ScenarioOutcome.BLOCKED_RISK),
        ("a", "l", lambda s: s, "BLOCKED_RISK"),
    ],
)
def test_a_malformed_fault_is_refused(axis, label, apply, expected):
    with pytest.raises(GeneratorError):
        Fault(axis, label, apply, expected)


def test_every_fault_declares_an_expected_stage():
    """An optional expectation would let a fault pass for any reason."""
    for fault in SINGLES:
        assert isinstance(fault.expected, ScenarioOutcome)


def test_every_fault_actually_changes_the_scenario():
    """A no-op fault would silently test the baseline over and over."""
    baseline = baseline_scenario()
    for fault in SINGLES:
        mutated = fault.apply(baseline)
        assert mutated != baseline, f"{fault.axis}.{fault.label} changed nothing"


# --------------------------------------------------------------------------
# 6. Offline and fixture-only.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_new_dependency_was_introduced():
    """`hypothesis` is unavailable, so generation is deterministic instead."""
    import importlib.util

    assert importlib.util.find_spec("hypothesis") is None
    assert "hypothesis" not in _imports(MODULE_PATH)


def test_generation_uses_no_randomness():
    """A generator that finds a bug on Tuesday and not Wednesday is worse."""
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint({"random", "secrets", "uuid", "os"})
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("random.", "shuffle", "sample(", "choice("):
        assert token not in source, token


def test_the_generator_imports_no_pinned_checkpoint_file():
    for pinned in ("read_only_observation_runtime", "read_only_broker_state",
                   "read_only_authorization_manifest", "live_order_state_model"):
        assert not any(pinned in name for name in _imports(MODULE_PATH)), pinned


def test_the_generator_performs_no_io_and_no_network():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "subprocess", "pathlib", "sqlite3"}
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in {"open", "urlopen", "print", "write_text"}


def test_the_generator_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.monotonic(" not in source


def test_the_only_key_is_a_fixture():
    assert FIXTURE_KEY == b"deterministic-generator-fixture-key-01!!!"
    authority = baseline_authority()
    assert FIXTURE_KEY.decode() not in repr(authority)


def test_no_broker_manifest_checkpoint_or_credential_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "local.yaml", ".rh_tokens", "password", "environ"):
        assert token not in source, token


def test_the_generator_defines_no_write_shaped_function():
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


# --------------------------------------------------------------------------
# 7. Privacy across every generated case.
# --------------------------------------------------------------------------


def test_no_generated_scenario_leaks_into_its_projection():
    """Sanitized facts stay allowlisted across the whole enumeration."""
    allowed = {
        "scenario_outcome": {o.value for o in ScenarioOutcome},
        "gate_verdict": None,          # checked loosely below
        "alert_count_bucket": {"none", "one", "many"},
        "permits_automated_action": {"false"},
    }
    for fault in SINGLES:
        facts = run_scenario(apply_faults((fault,))).sanitized_facts()
        assert set(facts) == set(allowed)
        assert facts["permits_automated_action"] == "false"
        assert facts["alert_count_bucket"] in allowed["alert_count_bucket"]
        assert facts["scenario_outcome"] in allowed["scenario_outcome"]


def test_no_generated_projection_carries_a_run_identity_or_key():
    for fault in SINGLES:
        rendered = repr(run_scenario(apply_faults((fault,))).sanitized_facts())
        for forbidden in ("generated-run", "generated-action", "prior-exposure",
                          FIXTURE_KEY.decode()):
            assert forbidden not in rendered, (fault.label, forbidden)


def test_generated_projections_survive_the_commissioning_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    for fault in SINGLES:
        validate_sanitized_evidence(
            run_scenario(apply_faults((fault,))).sanitized_facts()
        )
