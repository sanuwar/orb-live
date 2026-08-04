"""Durable kill-switch and suspension controller — causes, clearance, restart.

Every test is offline. Nothing opens a broker session, reaches the network,
issues a manifest, runs a checkpoint, or touches a credential. All signing
material is a fixture, all databases live under `tmp_path`.

The properties that matter most:

1. **Suspension dominates every favourable condition**, and nothing normal
   overrides it.
2. **Clearing requires reviewed, verified, non-self-declared evidence**, and
   takes two steps — neither of which reaches a runnable state.
3. **`PERMANENT_HALT` has no clearance path.**
4. **Restart changes nothing**, and an unreadable record fails closed.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import src.durable_suspension_controller as controller_module
from src.attestation_authority import (
    AttestationAuthority,
    ProducerIdentity,
    SignedAttestation,
)
from src.durable_scheduler import (
    DurableScheduler,
    RunState,
    SchedulerAction,
)
from src.durable_suspension_controller import (
    ALLOWED_CONTROLLER_TRANSITIONS,
    CAUSE_EVIDENCE_FACTS,
    CAUSE_SEVERITY,
    CLEARANCE_FACT,
    HALTED_STATES,
    MAXIMUM_CLEARANCE_VALIDITY,
    PERMANENT_HALT_CAUSES,
    TERMINAL_CONTROLLER_STATES,
    ControllerState,
    DurableSuspensionController,
    EvidenceClass,
    SuspensionCause,
    SuspensionClearance,
    SuspensionControllerError,
    SuspensionDecision,
    controller_transition_is_allowed,
    is_repository_data_path,
)
from src.execution_gates import Attestation, EvidenceSource

MODULE_PATH = pathlib.Path(controller_module.__file__)

NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
CONTROLLER = "ctl-1"

#: A fixture key. Not a secret, never used anywhere real.
FIXTURE_KEY = b"orb-live-test-signing-key-0123456789ab"


def authority() -> AttestationAuthority:
    return AttestationAuthority(
        FIXTURE_KEY,
        {
            "operator-console": ProducerIdentity(
                "operator-console",
                frozenset(
                    {"supervised_evidence_reviewed", "no_unresolved_operator_action"}
                ),
                frozenset({EvidenceSource.OPERATOR_ATTESTED}),
            ),
            "risk-engine": ProducerIdentity(
                "risk-engine",
                frozenset({"risk_within_limits"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "recovery-probe": ProducerIdentity(
                "recovery-probe",
                frozenset({"recovery_consistent"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "monitor": ProducerIdentity(
                "monitor",
                frozenset({"monitoring_healthy"}),
                frozenset({EvidenceSource.MEASURED, EvidenceSource.TESTED}),
            ),
            "alert-harness": ProducerIdentity(
                "alert-harness",
                frozenset({"alerting_verified_reachable"}),
                frozenset({EvidenceSource.TESTED}),
            ),
            "kill-switch-probe": ProducerIdentity(
                "kill-switch-probe",
                frozenset({"kill_switch_disengaged"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "calendar-probe": ProducerIdentity(
                "calendar-probe",
                frozenset({"market_session_valid"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "broker-state-probe": ProducerIdentity(
                "broker-state-probe",
                frozenset({"broker_state_verified_fresh"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            # Deliberately mis-scoped: permitted to speak about review but only
            # in a MEASURED voice, which is not a review.
            "measuring-reviewer": ProducerIdentity(
                "measuring-reviewer",
                frozenset({"supervised_evidence_reviewed"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
        },
    )


def review_evidence(auth: AttestationAuthority, **kw) -> SignedAttestation:
    fields = dict(
        producer_id="operator-console",
        fact_name=CLEARANCE_FACT,
        satisfied=True,
        source=EvidenceSource.OPERATOR_ATTESTED,
        recorded_monotonic=1000.0,
    )
    fields.update(kw)
    return auth.issue(**fields)


def clearance(auth, *, sequence, state=ControllerState.SUSPENDED, **kw):
    fields = dict(
        controller_id=CONTROLLER,
        expected_state=state,
        expected_sequence=sequence,
        reviewed=True,
        evidence=review_evidence(auth),
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        transition_id="clr-1",
    )
    fields.update(kw)
    return SuspensionClearance(**fields)


def store(tmp_path) -> DurableSuspensionController:
    return DurableSuspensionController(tmp_path / "controller.sqlite")


def suspended(tmp_path, cause=SuspensionCause.RISK_LIMIT_BREACH):
    ctl = store(tmp_path)
    ctl.register(CONTROLLER, now=NOW)
    ctl.suspend(CONTROLLER, cause=cause, transition_id="s-1", now=NOW)
    return ctl


# --------------------------------------------------------------------------
# 1. Offline by construction.
# --------------------------------------------------------------------------


def _code_identifiers(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                found.add(node.value)
    return found


def _dotted_call_names(path: pathlib.Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        parts, current = [], node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        if parts:
            names.add(".".join(reversed(parts)))
    return names


def test_the_controller_imports_nothing_broker_or_network_capable():
    forbidden = (
        "robinhood", "mcp", "adapter", "client", "socket", "http", "urllib",
        "requests", "commissioning", "manifest", "broker_saga",
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


def test_the_controller_never_reads_a_clock():
    forbidden = {
        "datetime.now", "datetime.utcnow", "datetime.datetime.now",
        "datetime.datetime.utcnow", "time.time", "time.monotonic",
        "time.perf_counter", "date.today",
    }
    assert _dotted_call_names(MODULE_PATH) & forbidden == set()
    imported = set()
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "time" not in imported


def test_the_controller_has_no_order_or_activation_vocabulary():
    identifiers = {item.lower() for item in _code_identifiers(MODULE_PATH)}
    for forbidden in (
        "place_equity_order", "cancel_equity_order", "call_tool",
        "activation_approvals", "account_number", "token", "signing_key",
    ):
        assert not any(forbidden in item for item in identifiers), forbidden


def test_no_real_signing_material_is_embedded():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "b'" not in source and 'b"' not in source


# --------------------------------------------------------------------------
# 2. States, causes, transitions.
# --------------------------------------------------------------------------


def test_every_state_appears_in_the_transition_matrix():
    assert set(ALLOWED_CONTROLLER_TRANSITIONS) == set(ControllerState)


def test_permanent_halt_is_terminal():
    assert ALLOWED_CONTROLLER_TRANSITIONS[ControllerState.PERMANENT_HALT] == frozenset()
    assert TERMINAL_CONTROLLER_STATES == {ControllerState.PERMANENT_HALT}


def test_malformed_state_has_no_transitions_and_blocks_work():
    assert ALLOWED_CONTROLLER_TRANSITIONS[ControllerState.MALFORMED_STATE] == frozenset()
    assert ControllerState.MALFORMED_STATE in HALTED_STATES


def test_the_model_preserves_every_required_distinction():
    required = {
        "no known suspension": ControllerState.CLEAR,
        "active suspension": ControllerState.SUSPENDED,
        "clearance pending": ControllerState.CLEARANCE_PENDING_REVIEW,
        "irreversible halt": ControllerState.PERMANENT_HALT,
        "malformed": ControllerState.MALFORMED_STATE,
    }
    assert len(set(required.values())) == 5


def test_every_cause_has_a_severity_rank():
    assert set(CAUSE_SEVERITY) == set(SuspensionCause)
    ranks = [CAUSE_SEVERITY[c] for c in SuspensionCause if c is not SuspensionCause.NONE]
    assert len(set(ranks)) == len(ranks), "ranks must be a total order"


def test_unknown_cause_outranks_every_ordinary_cause():
    """Not knowing why work stopped is worse than knowing."""
    ordinary = [
        c for c in SuspensionCause
        if c not in {SuspensionCause.NONE, SuspensionCause.UNKNOWN_CAUSE,
                     SuspensionCause.INTERNAL_INVARIANT_FAILURE}
    ]
    for cause in ordinary:
        assert CAUSE_SEVERITY[SuspensionCause.UNKNOWN_CAUSE] > CAUSE_SEVERITY[cause]


def test_none_is_the_weakest_cause():
    for cause in SuspensionCause:
        if cause is not SuspensionCause.NONE:
            assert CAUSE_SEVERITY[cause] > CAUSE_SEVERITY[SuspensionCause.NONE]


@pytest.mark.parametrize(
    "previous,target",
    [
        (previous, target)
        for previous, targets in ALLOWED_CONTROLLER_TRANSITIONS.items()
        for target in targets
    ],
)
def test_allowed_transitions_are_reported_allowed(previous, target):
    assert controller_transition_is_allowed(previous, target)


@pytest.mark.parametrize(
    "previous,target",
    [
        (previous, target)
        for previous in ControllerState
        for target in ControllerState
        if target not in ALLOWED_CONTROLLER_TRANSITIONS[previous]
    ],
)
def test_disallowed_transitions_are_reported_disallowed(previous, target):
    assert not controller_transition_is_allowed(previous, target)


def test_transition_helper_refuses_non_states():
    with pytest.raises(SuspensionControllerError):
        controller_transition_is_allowed("CLEAR", ControllerState.SUSPENDED)


# --------------------------------------------------------------------------
# 3. Raising a suspension.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cause",
    [c for c in SuspensionCause if c is not SuspensionCause.NONE],
)
def test_every_cause_can_halt_from_clear(tmp_path, cause):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=cause, transition_id="s-1", now=NOW
        )
        expected = (
            ControllerState.PERMANENT_HALT
            if cause in PERMANENT_HALT_CAUSES
            else ControllerState.SUSPENDED
        )
        assert record.state is expected
        assert record.cause is cause
        assert record.blocks_work


def test_the_none_cause_cannot_raise_a_suspension(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.NONE, transition_id="s", now=NOW
            )


def test_a_second_halt_signal_never_weakens_the_first(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.KILL_SWITCH_ENGAGED,
            transition_id="s-1", now=NOW,
        )
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-2", now=LATER,
        )
        assert record.cause is SuspensionCause.KILL_SWITCH_ENGAGED
        assert record.state is ControllerState.SUSPENDED


def test_a_more_severe_cause_replaces_the_recorded_one(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-1", now=NOW,
        )
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="s-2", now=LATER,
        )
        assert record.cause is SuspensionCause.OPERATOR_HALT


def test_an_equal_severity_cause_does_not_replace(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-1", now=NOW,
        )
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-2", now=LATER,
        )
        assert record.cause is SuspensionCause.RISK_LIMIT_BREACH
        assert ctl.event_count(CONTROLLER) == 2  # history keeps both


def test_every_halt_signal_is_recorded_in_immutable_history(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        for index, cause in enumerate(
            [SuspensionCause.STALE_DATA, SuspensionCause.MONITORING_UNHEALTHY,
             SuspensionCause.RISK_LIMIT_BREACH], start=1
        ):
            ctl.suspend(
                CONTROLLER, cause=cause, transition_id=f"s-{index}", now=NOW
            )
        assert ctl.event_count(CONTROLLER) == 3


def test_an_internal_invariant_failure_halts_permanently(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.escalate_to_permanent_halt(
            CONTROLLER, transition_id="h-1", now=NOW
        )
        assert record.state is ControllerState.PERMANENT_HALT
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.STALE_DATA,
                transition_id="s-2", now=LATER,
            )


def test_evidence_classification(tmp_path):
    auth = authority()
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        # No attestation: an internal observation, honestly labelled.
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-1", now=NOW,
        )
        assert record.evidence_class is EvidenceClass.INTERNAL_OBSERVATION

        # Verified attestation for the matching fact.
        signed = auth.issue(
            producer_id="risk-engine", fact_name="risk_within_limits",
            satisfied=False, source=EvidenceSource.MEASURED,
            recorded_monotonic=1.0,
        )
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-2", now=LATER, evidence=signed, authority=auth,
        )
        assert record.evidence_class is EvidenceClass.VERIFIED_ATTESTATION


def test_an_unverifiable_attestation_is_recorded_unverified_not_absent(tmp_path):
    """Offered-and-failed is strictly worse than never offered."""
    auth = authority()
    forged = SignedAttestation(
        fact_name="risk_within_limits",
        attestation=Attestation(False, EvidenceSource.MEASURED, 1.0),
        producer_id="risk-engine",
        signature="0" * 64,
    )
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-1", now=NOW, evidence=forged, authority=auth,
        )
        assert record.evidence_class is EvidenceClass.UNVERIFIED
        assert record.state is ControllerState.SUSPENDED


def test_evidence_for_the_wrong_fact_is_unverified(tmp_path):
    auth = authority()
    signed = auth.issue(
        producer_id="monitor", fact_name="monitoring_healthy", satisfied=False,
        source=EvidenceSource.MEASURED, recorded_monotonic=1.0,
    )
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-1", now=NOW, evidence=signed, authority=auth,
        )
        assert record.evidence_class is EvidenceClass.UNVERIFIED


def test_causes_with_no_external_fact_cannot_be_attested():
    """There is no external fact that proves them, so none is mapped."""
    for cause in (
        SuspensionCause.STALE_DATA, SuspensionCause.DUPLICATE_ACTION_RISK,
        SuspensionCause.RESTART_AMBIGUITY,
        SuspensionCause.INTERNAL_INVARIANT_FAILURE, SuspensionCause.UNKNOWN_CAUSE,
    ):
        assert cause not in CAUSE_EVIDENCE_FACTS


def test_every_mapped_cause_fact_is_a_real_gate_fact():
    from src.execution_gates import REQUIRED_EVIDENCE

    for fact in CAUSE_EVIDENCE_FACTS.values():
        assert fact in REQUIRED_EVIDENCE
    assert CLEARANCE_FACT in REQUIRED_EVIDENCE


# --------------------------------------------------------------------------
# 4. Clearance.
# --------------------------------------------------------------------------


def test_a_valid_clearance_moves_only_to_pending_review(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        record = ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth,
            now=LATER,
        )
        assert record.state is ControllerState.CLEARANCE_PENDING_REVIEW
        assert record.blocks_work
        assert record.cause is SuspensionCause.RISK_LIMIT_BREACH


def test_returning_to_clear_needs_a_second_attested_act(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth,
            now=LATER,
        )
        record = ctl.complete_review(
            CONTROLLER,
            clearance=clearance(
                auth,
                sequence=ctl.load(CONTROLLER).sequence,
                state=ControllerState.CLEARANCE_PENDING_REVIEW,
                transition_id="clr-2",
            ),
            authority=auth,
            now=LATER,
        )
        assert record.state is ControllerState.CLEAR
        assert record.cause is SuspensionCause.NONE
        assert not record.blocks_work


def test_a_permanent_halt_cannot_be_cleared(tmp_path):
    auth = authority()
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.escalate_to_permanent_halt(CONTROLLER, transition_id="h-1", now=NOW)
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence,
                    state=ControllerState.PERMANENT_HALT,
                ),
                authority=auth,
                now=LATER,
            )
        assert ctl.load(CONTROLLER).state is ControllerState.PERMANENT_HALT


def test_an_unreviewed_clearance_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, reviewed=False
                ),
                authority=auth,
                now=LATER,
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_a_self_declared_clearance_is_refused(tmp_path):
    """The authority will not grant SELF_DECLARED, and a forged one fails."""
    auth = authority()
    forged = SignedAttestation(
        fact_name=CLEARANCE_FACT,
        attestation=Attestation(True, EvidenceSource.SELF_DECLARED, 1.0),
        producer_id="operator-console",
        signature="0" * 64,
    )
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, evidence=forged
                ),
                authority=auth,
                now=LATER,
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_no_producer_may_be_granted_self_declared():
    with pytest.raises(Exception):
        ProducerIdentity(
            "x", frozenset({CLEARANCE_FACT}), frozenset({EvidenceSource.SELF_DECLARED})
        )


def test_a_measured_clearance_source_is_refused(tmp_path):
    """Machinery observing itself is not a review."""
    auth = authority()
    measured = auth.issue(
        producer_id="measuring-reviewer", fact_name=CLEARANCE_FACT, satisfied=True,
        source=EvidenceSource.MEASURED, recorded_monotonic=1.0,
    )
    assert auth.verify(measured)  # genuinely signed, still refused below
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, evidence=measured
                ),
                authority=auth,
                now=LATER,
            )


def test_clearance_evidence_must_affirm_review(tmp_path):
    auth = authority()
    negative = review_evidence(auth, satisfied=False)
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, evidence=negative
                ),
                authority=auth,
                now=LATER,
            )


def test_clearance_evidence_for_the_wrong_fact_is_refused(tmp_path):
    auth = authority()
    wrong = auth.issue(
        producer_id="operator-console", fact_name="no_unresolved_operator_action",
        satisfied=True, source=EvidenceSource.OPERATOR_ATTESTED,
        recorded_monotonic=1.0,
    )
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, evidence=wrong
                ),
                authority=auth,
                now=LATER,
            )


def test_a_stale_clearance_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        expired = clearance(
            auth,
            sequence=ctl.load(CONTROLLER).sequence,
            issued_at=NOW - timedelta(hours=6),
            expires_at=NOW - timedelta(hours=1),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER, clearance=expired, authority=auth, now=NOW
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_a_future_dated_clearance_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        future = clearance(
            auth,
            sequence=ctl.load(CONTROLLER).sequence,
            issued_at=NOW + timedelta(hours=5),
            expires_at=NOW + timedelta(hours=6),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER, clearance=future, authority=auth, now=NOW
            )


def test_an_over_long_clearance_window_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        wide = clearance(
            auth,
            sequence=ctl.load(CONTROLLER).sequence,
            issued_at=NOW,
            expires_at=NOW + MAXIMUM_CLEARANCE_VALIDITY + timedelta(minutes=1),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(CONTROLLER, clearance=wide, authority=auth, now=NOW)


def test_a_clearance_for_the_wrong_controller_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        wrong = clearance(
            auth, sequence=ctl.load(CONTROLLER).sequence, controller_id="ctl-other"
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(CONTROLLER, clearance=wrong, authority=auth, now=LATER)


def test_a_clearance_for_an_outdated_sequence_is_refused(tmp_path):
    """A clearance reviewed against an earlier suspension says nothing about
    a later one that arrived in between."""
    auth = authority()
    with suspended(tmp_path) as ctl:
        stale_sequence = ctl.load(CONTROLLER).sequence
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="s-2", now=LATER,
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(auth, sequence=stale_sequence),
                authority=auth,
                now=LATER,
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_a_clearance_expecting_the_wrong_state_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence,
                    state=ControllerState.CLEAR,
                ),
                authority=auth,
                now=LATER,
            )


def test_a_reused_clearance_identity_is_refused(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth,
            now=LATER,
        )
        with pytest.raises(SuspensionControllerError):
            ctl.complete_review(
                CONTROLLER,
                clearance=clearance(
                    auth,
                    sequence=ctl.load(CONTROLLER).sequence,
                    state=ControllerState.CLEARANCE_PENDING_REVIEW,
                    transition_id="clr-1",  # already spent
                ),
                authority=auth,
                now=LATER,
            )


def test_clearing_a_controller_that_is_not_suspended_is_refused(tmp_path):
    auth = authority()
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(auth, sequence=0, state=ControllerState.CLEAR),
                authority=auth,
                now=NOW,
            )


def test_a_bare_boolean_is_not_a_clearance(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER, clearance=True, authority=auth, now=LATER
            )


# --------------------------------------------------------------------------
# 5. Idempotency.
# --------------------------------------------------------------------------


def test_a_duplicate_transition_identity_is_refused(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-1", now=NOW,
        )
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
                transition_id="s-1", now=LATER,
            )
        assert ctl.event_count(CONTROLLER) == 1


def test_an_identity_cannot_move_between_controllers(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register("ctl-a", now=NOW)
        ctl.register("ctl-b", now=NOW)
        ctl.suspend(
            "ctl-a", cause=SuspensionCause.STALE_DATA, transition_id="s", now=NOW
        )
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                "ctl-b", cause=SuspensionCause.STALE_DATA,
                transition_id="s", now=NOW,
            )


def test_identities_stay_spent_across_restart(tmp_path):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-1", now=NOW,
        )
    with DurableSuspensionController(path) as reopened:
        with pytest.raises(SuspensionControllerError):
            reopened.suspend(
                CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
                transition_id="s-1", now=LATER,
            )
        assert reopened.total_event_count == 1


def test_a_permanently_halted_controller_does_not_free_identities(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register("ctl-a", now=NOW)
        ctl.register("ctl-b", now=NOW)
        ctl.escalate_to_permanent_halt("ctl-a", transition_id="h", now=NOW)
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                "ctl-b", cause=SuspensionCause.STALE_DATA,
                transition_id="h", now=NOW,
            )


def test_a_controller_identity_cannot_be_reused(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        with pytest.raises(SuspensionControllerError):
            ctl.register(CONTROLLER, now=NOW)


# --------------------------------------------------------------------------
# 6. Restart.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cause,expected",
    [
        (None, ControllerState.CLEAR),
        (SuspensionCause.RISK_LIMIT_BREACH, ControllerState.SUSPENDED),
        (SuspensionCause.INTERNAL_INVARIANT_FAILURE, ControllerState.PERMANENT_HALT),
    ],
)
def test_restart_preserves_state_exactly(tmp_path, cause, expected):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        if cause is not None:
            ctl.suspend(CONTROLLER, cause=cause, transition_id="s-1", now=NOW)
    with DurableSuspensionController(path) as reopened:
        record = reopened.recover_on_restart(CONTROLLER)
        assert record.state is expected


def test_restart_preserves_clearance_pending_review(tmp_path):
    auth = authority()
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-1", now=NOW,
        )
        ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth,
            now=LATER,
        )
    with DurableSuspensionController(path) as reopened:
        record = reopened.recover_on_restart(CONTROLLER)
        assert record.state is ControllerState.CLEARANCE_PENDING_REVIEW
        assert record.blocks_work


def test_reopening_many_times_never_clears_suspension(tmp_path):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.KILL_SWITCH_ENGAGED,
            transition_id="s-1", now=NOW,
        )
    for _ in range(5):
        with DurableSuspensionController(path) as reopened:
            assert reopened.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_no_elapsed_time_clears_suspension(tmp_path):
    with suspended(tmp_path) as ctl:
        for offset in (timedelta(days=1), timedelta(days=365), timedelta(days=3650)):
            decision = ctl.assess(CONTROLLER)
            assert decision.state is ControllerState.SUSPENDED
            assert decision.scheduler_action is SchedulerAction.SUSPEND


# --------------------------------------------------------------------------
# 7. Persistence integrity.
# --------------------------------------------------------------------------


def test_a_database_under_repository_data_is_refused():
    from src.durable_suspension_controller import REPOSITORY_ROOT

    with pytest.raises(SuspensionControllerError):
        DurableSuspensionController(REPOSITORY_ROOT / "data" / "c.sqlite")
    assert is_repository_data_path(REPOSITORY_ROOT / "data" / "x.sqlite")
    assert not is_repository_data_path(REPOSITORY_ROOT / "src")


def test_an_unknown_schema_version_is_refused(tmp_path):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE controller_meta SET value = '99' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(SuspensionControllerError):
        DurableSuspensionController(path)


@pytest.mark.parametrize(
    "column,value",
    [
        ("state", "TOTALLY_FINE"),
        ("state", "MALFORMED_STATE"),
        ("cause", "BECAUSE"),
        ("evidence_class", "PROBABLY"),
        ("created_at", ""),
        ("created_at", "yesterday"),
        ("updated_at", "2026-08-03T10:00:00"),
    ],
)
def test_a_malformed_stored_value_fails_closed(tmp_path, column, value):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl._connection.execute(
            f"UPDATE controllers SET {column} = ? WHERE controller_id = ?",
            (value, CONTROLLER),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.load(CONTROLLER)


def test_a_clear_controller_carrying_a_cause_fails_closed(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl._connection.execute(
            "UPDATE controllers SET cause = ? WHERE controller_id = ?",
            (SuspensionCause.OPERATOR_HALT.value, CONTROLLER),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.load(CONTROLLER)


def test_a_halted_controller_without_a_cause_fails_closed(tmp_path):
    with suspended(tmp_path) as ctl:
        ctl._connection.execute(
            "UPDATE controllers SET cause = ? WHERE controller_id = ?",
            (SuspensionCause.NONE.value, CONTROLLER),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.load(CONTROLLER)


def test_conflicting_controller_records_fail_closed(tmp_path):
    """Two rows for one controller: which history to believe is not knowable.

    The primary key makes this unreachable through the module, so the table is
    pre-created without one — `CREATE TABLE IF NOT EXISTS` then leaves it
    alone. This is what a corrupted or hand-edited file looks like.
    """
    path = tmp_path / "controller.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE controllers ("
        " controller_id TEXT, state TEXT NOT NULL, cause TEXT NOT NULL,"
        " evidence_class TEXT NOT NULL, sequence INTEGER NOT NULL,"
        " clearance_reviewed INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    for cause in ("OPERATOR_HALT", "STALE_DATA"):
        connection.execute(
            "INSERT INTO controllers VALUES (?, 'SUSPENDED', ?, 'NONE', 1, 0, ?, ?)",
            (CONTROLLER, cause, NOW.isoformat(), NOW.isoformat()),
        )
    connection.commit()
    connection.close()

    with DurableSuspensionController(path) as reopened:
        with pytest.raises(SuspensionControllerError):
            reopened.load(CONTROLLER)
        assert reopened.assess(CONTROLLER).state is ControllerState.MALFORMED_STATE


def test_an_unreadable_record_assesses_as_malformed_and_suspends(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl._connection.execute(
            "UPDATE controllers SET state = 'NONSENSE' WHERE controller_id = ?",
            (CONTROLLER,),
        )
        decision = ctl.assess(CONTROLLER)
        assert decision.state is ControllerState.MALFORMED_STATE
        assert decision.cause is SuspensionCause.INTERNAL_INVARIANT_FAILURE
        assert decision.scheduler_action is SchedulerAction.SUSPEND
        assert decision.blocks_work


def test_an_unregistered_controller_assesses_as_malformed(tmp_path):
    with store(tmp_path) as ctl:
        decision = ctl.assess("never-registered")
        assert decision.state is ControllerState.MALFORMED_STATE
        assert decision.scheduler_action is SchedulerAction.SUSPEND


def test_a_sequence_collision_is_not_reported_as_a_duplicate_identity(tmp_path):
    """Two constraints reach the same handler and mean different things.

    Blaming a concurrent advance on a replayed identity would send an operator
    hunting for a replay that never happened.
    """
    with suspended(tmp_path) as ctl:
        record = ctl.load(CONTROLLER)
        # Occupy the next sequence with a different identity, the way a second
        # writer would between another caller's read and write.
        ctl._connection.execute(
            "INSERT INTO controller_events (transition_id, controller_id, sequence, "
            " from_state, to_state, cause, evidence_class, recorded_at) "
            "VALUES ('other-writer', ?, ?, 'SUSPENDED', 'SUSPENDED', "
            " 'STALE_DATA', 'NONE', ?)",
            (CONTROLLER, record.sequence + 1, NOW.isoformat()),
        )
        with pytest.raises(SuspensionControllerError, match="concurrently"):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
                transition_id="fresh-identity", now=LATER,
            )


def test_a_duplicate_identity_is_still_reported_as_one(tmp_path):
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError, match="already been used"):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
                transition_id="s-1", now=LATER,
            )


def test_a_write_failure_reports_its_own_cause(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        real = ctl._connection

        class FailingOnUpdate:
            def execute(self, statement, *args, **kwargs):
                if statement.lstrip().upper().startswith("UPDATE CONTROLLERS"):
                    raise sqlite3.OperationalError("disk is on fire")
                return real.execute(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(real, name)

        ctl._connection = FailingOnUpdate()
        with pytest.raises(sqlite3.OperationalError, match="disk is on fire"):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.STALE_DATA,
                transition_id="s-1", now=NOW,
            )
        ctl._connection = real
        assert ctl.load(CONTROLLER).state is ControllerState.CLEAR
        assert ctl.event_count(CONTROLLER) == 0


# --------------------------------------------------------------------------
# 8. Privacy.
# --------------------------------------------------------------------------


def test_persisted_columns_cannot_carry_private_values(tmp_path):
    with store(tmp_path) as ctl:
        columns = {
            row[1].lower()
            for table in ("controllers", "controller_events")
            for row in ctl._connection.execute(f"PRAGMA table_info({table})")
        }
        for forbidden in (
            "symbol", "quantity", "price", "balance", "account", "payload",
            "raw", "message", "credential", "token", "path", "manifest",
            "username", "alert_text",
        ):
            assert not any(forbidden in column for column in columns), forbidden


def test_the_record_projection_omits_the_controller_identity(tmp_path):
    with store(tmp_path) as ctl:
        record = ctl.register("operator-named-ctl-98765", now=NOW)
        assert "operator-named-ctl-98765" not in repr(record.sanitized_facts())


def test_projection_values_are_fixed_vocabulary(tmp_path):
    with suspended(tmp_path) as ctl:
        for key, value in ctl.load(CONTROLLER).sanitized_facts().items():
            assert value in (
                {s.value for s in ControllerState}
                | {c.value for c in SuspensionCause}
                | {e.value for e in EvidenceClass}
                | {"true", "false", "none", "one", "many"}
            ), (key, value)


def test_no_cause_or_state_value_is_free_text():
    for member in list(SuspensionCause) + list(ControllerState) + list(EvidenceClass):
        assert member.value.replace("_", "").isalnum()
        assert member.value == member.value.upper()


def test_a_decision_never_permits_automated_action():
    with pytest.raises(SuspensionControllerError):
        SuspensionDecision(
            state=ControllerState.CLEAR,
            cause=SuspensionCause.NONE,
            requires_operator_review=False,
            scheduler_action=SchedulerAction.REQUIRE_OPERATOR_REVIEW,
            permits_automated_action=True,
        )


@pytest.mark.parametrize(
    "action",
    [a for a in SchedulerAction
     if a not in (SchedulerAction.SUSPEND, SchedulerAction.REQUIRE_OPERATOR_REVIEW)],
)
def test_a_decision_cannot_drive_the_scheduler_anywhere_permissive(action):
    with pytest.raises(SuspensionControllerError):
        SuspensionDecision(
            state=ControllerState.CLEAR,
            cause=SuspensionCause.NONE,
            requires_operator_review=False,
            scheduler_action=action,
        )


# --------------------------------------------------------------------------
# 9. Scheduler integration.
# --------------------------------------------------------------------------


def scheduler_for(tmp_path) -> DurableScheduler:
    return DurableScheduler(tmp_path / "scheduler.sqlite")


def test_an_active_suspension_drives_the_scheduler_to_suspended(tmp_path):
    with suspended(tmp_path) as ctl, scheduler_for(tmp_path) as sched:
        sched.create_run("run-1", session_date=NOW.date(), now=NOW)
        decision = ctl.assess(CONTROLLER)
        assert decision.scheduler_action is SchedulerAction.SUSPEND

        run = sched.apply_transition(
            "run-1", target=RunState.SUSPENDED, transition_id="t-1", now=NOW,
            suspension_reason="SAFETY_HALT",
        )
        assert run.state is RunState.SUSPENDED
        assert sched.decide(
            "run-1", now=NOW, facts=_clean_scheduler_facts()
        ).action is SchedulerAction.SUSPEND


def _clean_scheduler_facts():
    from src.durable_scheduler import UpstreamFacts

    return UpstreamFacts(
        gate_is_open=True, risk_limits_satisfied=True,
        reconciliation_resolved=True, monitoring_delivery_confirmed=True,
        attestations_verified=True, unresolved_operator_action=False,
        safety_halt_active=False,
    )


def test_a_failed_clearance_leaves_the_scheduler_suspended(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl, scheduler_for(tmp_path) as sched:
        sched.create_run("run-1", session_date=NOW.date(), now=NOW)
        sched.apply_transition(
            "run-1", target=RunState.SUSPENDED, transition_id="t-1", now=NOW,
            suspension_reason="SAFETY_HALT",
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, reviewed=False
                ),
                authority=auth,
                now=LATER,
            )
        assert ctl.assess(CONTROLLER).scheduler_action is SchedulerAction.SUSPEND
        assert sched.load_run("run-1").state is RunState.SUSPENDED


def test_a_successful_clearance_sends_the_scheduler_back_to_review(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl, scheduler_for(tmp_path) as sched:
        sched.create_run("run-1", session_date=NOW.date(), now=NOW)
        sched.apply_transition(
            "run-1", target=RunState.SUSPENDED, transition_id="t-1", now=NOW,
            suspension_reason="SAFETY_HALT",
        )
        ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth,
            now=LATER,
        )
        decision = ctl.assess(CONTROLLER)
        assert decision.scheduler_action is SchedulerAction.REQUIRE_OPERATOR_REVIEW

        from src.durable_scheduler import OperatorClearance

        run = sched.clear_suspension(
            "run-1",
            clearance=OperatorClearance(
                transition_id="t-clear", operator_reference="op-1",
                reason_code="OPERATOR_REVIEWED_AND_RESOLVED", reviewed=True,
            ),
            now=LATER,
        )
        assert run.state is RunState.SUPERVISED_REVIEW_REQUIRED


def test_no_controller_result_reaches_offline_evaluation(tmp_path):
    """Every state, not just the suspended ones."""
    auth = authority()
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        seen = set()
        seen.add(ctl.assess(CONTROLLER).scheduler_action)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-1", now=NOW,
        )
        seen.add(ctl.assess(CONTROLLER).scheduler_action)
        ctl.apply_clearance(
            CONTROLLER,
            clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
            authority=auth, now=LATER,
        )
        seen.add(ctl.assess(CONTROLLER).scheduler_action)
        assert seen <= {
            SchedulerAction.SUSPEND, SchedulerAction.REQUIRE_OPERATOR_REVIEW
        }
        assert SchedulerAction.RUN_OFFLINE_CHECKS not in seen


def test_no_controller_result_opens_a_gate_or_enables_strategy():
    import yaml

    from src.execution_gates import ACTIVATION_APPROVALS
    from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES

    assert ACTIVATION_APPROVALS == frozenset()
    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()
    assert yaml.safe_load(
        pathlib.Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
    )["strategy"]["enabled"] is False
    assert "ACTIVATION_APPROVALS" not in _code_identifiers(MODULE_PATH)


def test_the_controller_is_not_in_the_milestone_9_pinned_closure():
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    assert "src/durable_suspension_controller.py" not in PINNED_IMPLEMENTATION_CLOSURE


# --------------------------------------------------------------------------
# 10. Suspension dominates.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cause",
    [
        SuspensionCause.RISK_LIMIT_BREACH,
        SuspensionCause.RECONCILIATION_AMBIGUOUS,
        SuspensionCause.MONITORING_UNHEALTHY,
        SuspensionCause.ALERT_DELIVERY_UNCONFIRMED,
        SuspensionCause.BROKER_STATE_UNKNOWN,
        SuspensionCause.CALENDAR_INVALID,
        SuspensionCause.KILL_SWITCH_ENGAGED,
        SuspensionCause.OPERATOR_HALT,
    ],
)
def test_each_cause_suspends_regardless_of_favourable_conditions(tmp_path, cause):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(CONTROLLER, cause=cause, transition_id="s-1", now=NOW)
        decision = ctl.assess(CONTROLLER)
        assert decision.scheduler_action is SchedulerAction.SUSPEND
        assert decision.requires_operator_review is True
        assert decision.blocks_work


def test_a_favourable_risk_decision_cannot_override_a_kill_switch(tmp_path):
    auth = authority()
    good_risk = auth.issue(
        producer_id="risk-engine", fact_name="risk_within_limits", satisfied=True,
        source=EvidenceSource.MEASURED, recorded_monotonic=1.0,
    )
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.KILL_SWITCH_ENGAGED,
            transition_id="s-1", now=NOW,
        )
        # A perfectly good risk attestation arriving afterwards changes nothing.
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="s-2", now=LATER, evidence=good_risk, authority=auth,
        )
        record = ctl.load(CONTROLLER)
        assert record.cause is SuspensionCause.KILL_SWITCH_ENGAGED
        assert ctl.assess(CONTROLLER).scheduler_action is SchedulerAction.SUSPEND


def test_missing_monitoring_evidence_is_not_health(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.MONITORING_UNHEALTHY,
            transition_id="s-1", now=NOW,
        )
        assert record.evidence_class is EvidenceClass.INTERNAL_OBSERVATION
        assert record.blocks_work


def test_an_undelivered_critical_alert_suspends(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.ALERT_DELIVERY_UNCONFIRMED,
            transition_id="s-1", now=NOW,
        )
        assert record.state is ControllerState.SUSPENDED
        assert ctl.assess(CONTROLLER).scheduler_action is SchedulerAction.SUSPEND


def test_an_unknown_cause_never_reads_as_clear(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        record = ctl.suspend(
            CONTROLLER, cause=SuspensionCause.UNKNOWN_CAUSE,
            transition_id="s-1", now=NOW,
        )
        assert record.state is ControllerState.SUSPENDED
        assert record.cause is SuspensionCause.UNKNOWN_CAUSE
        # And a later, better-understood signal does not displace it.
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.STALE_DATA,
            transition_id="s-2", now=LATER,
        )
        assert ctl.load(CONTROLLER).cause is SuspensionCause.UNKNOWN_CAUSE


# --------------------------------------------------------------------------
# 11. Adversarial probes.
# --------------------------------------------------------------------------


def test_probe_suspension_cannot_be_cleared_by_restart(tmp_path):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="s-1", now=NOW,
        )
    with DurableSuspensionController(path) as reopened:
        assert reopened.recover_on_restart(CONTROLLER).state is (
            ControllerState.SUSPENDED
        )
        assert reopened.assess(CONTROLLER).scheduler_action is SchedulerAction.SUSPEND


def test_probe_permanent_halt_cannot_be_downgraded(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.escalate_to_permanent_halt(CONTROLLER, transition_id="h", now=NOW)
        for cause in (SuspensionCause.STALE_DATA, SuspensionCause.OPERATOR_HALT):
            with pytest.raises(SuspensionControllerError):
                ctl.suspend(
                    CONTROLLER, cause=cause, transition_id=f"x-{cause.value}",
                    now=LATER,
                )
        assert ctl.load(CONTROLLER).state is ControllerState.PERMANENT_HALT


def test_probe_suspended_cannot_jump_straight_to_clear(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.complete_review(
                CONTROLLER,
                clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
                authority=auth,
                now=LATER,
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_probe_the_matrix_forbids_suspended_to_clear():
    assert ControllerState.CLEAR not in ALLOWED_CONTROLLER_TRANSITIONS[
        ControllerState.SUSPENDED
    ]


def test_probe_a_replayed_clearance_on_a_new_suspension_is_refused(tmp_path):
    """Clear, re-suspend, then replay the spent artifact."""
    auth = authority()
    with suspended(tmp_path) as ctl:
        first = clearance(auth, sequence=ctl.load(CONTROLLER).sequence)
        ctl.apply_clearance(CONTROLLER, clearance=first, authority=auth, now=LATER)
        ctl.complete_review(
            CONTROLLER,
            clearance=clearance(
                auth, sequence=ctl.load(CONTROLLER).sequence,
                state=ControllerState.CLEARANCE_PENDING_REVIEW,
                transition_id="clr-2",
            ),
            authority=auth, now=LATER,
        )
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="s-9", now=LATER,
        )
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER, clearance=first, authority=auth, now=LATER
            )
        assert ctl.load(CONTROLLER).state is ControllerState.SUSPENDED


def test_probe_no_authority_means_no_verification(tmp_path):
    auth = authority()
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
                authority=None,
                now=LATER,
            )


def test_probe_a_different_authority_key_fails_verification(tmp_path):
    auth = authority()
    other = AttestationAuthority(
        b"a-completely-different-fixture-key-000000",
        {
            "operator-console": ProducerIdentity(
                "operator-console",
                frozenset({CLEARANCE_FACT}),
                frozenset({EvidenceSource.OPERATOR_ATTESTED}),
            )
        },
    )
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(auth, sequence=ctl.load(CONTROLLER).sequence),
                authority=other,
                now=LATER,
            )


def test_probe_a_tampered_attestation_fails_verification(tmp_path):
    auth = authority()
    genuine = review_evidence(auth)
    tampered = SignedAttestation(
        fact_name=genuine.fact_name,
        attestation=Attestation(True, EvidenceSource.OPERATOR_ATTESTED, 9999.0),
        producer_id=genuine.producer_id,
        signature=genuine.signature,
    )
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.apply_clearance(
                CONTROLLER,
                clearance=clearance(
                    auth, sequence=ctl.load(CONTROLLER).sequence, evidence=tampered
                ),
                authority=auth,
                now=LATER,
            )


# --------------------------------------------------------------------------
# 12. Public transition inspection.
# --------------------------------------------------------------------------


def test_a_persisted_controller_transition_is_inspectable(tmp_path):
    with suspended(tmp_path) as ctl:
        receipt = ctl.inspect_transition("s-1")
        assert receipt is not None
        assert receipt.transition_id == "s-1"
        assert receipt.controller_id == CONTROLLER
        assert receipt.from_state is ControllerState.CLEAR
        assert receipt.to_state is ControllerState.SUSPENDED
        assert receipt.cause is SuspensionCause.RISK_LIMIT_BREACH
        assert receipt.sequence == 1
        assert receipt.persisted is True


def test_an_unknown_controller_transition_is_not_found(tmp_path):
    with suspended(tmp_path) as ctl:
        assert ctl.inspect_transition("never-happened") is None


@pytest.mark.parametrize("bad", ["", "   ", " s ", "x" * 129, None, 7, "s/1"])
def test_a_malformed_controller_transition_identity_is_refused(tmp_path, bad):
    with suspended(tmp_path) as ctl:
        with pytest.raises(SuspensionControllerError):
            ctl.inspect_transition(bad)


def test_a_controller_receipt_is_immutable(tmp_path):
    from dataclasses import FrozenInstanceError

    with suspended(tmp_path) as ctl:
        receipt = ctl.inspect_transition("s-1")
        with pytest.raises(FrozenInstanceError):
            receipt.to_state = ControllerState.CLEAR


def test_a_controller_receipt_exposes_only_allowlisted_fields(tmp_path):
    from dataclasses import fields

    with suspended(tmp_path) as ctl:
        receipt = ctl.inspect_transition("s-1")
        assert {f.name for f in fields(receipt)} == {
            "transition_id", "controller_id", "from_state", "to_state", "cause",
            "evidence_class", "sequence", "persisted",
        }


def test_a_controller_receipt_carries_no_connection_cursor_path_or_sql(tmp_path):
    import sqlite3 as sqlite_module

    with suspended(tmp_path) as ctl:
        receipt = ctl.inspect_transition("s-1")
        for value in vars(receipt).values():
            assert not isinstance(
                value, (sqlite_module.Connection, sqlite_module.Cursor, pathlib.Path)
            )
            if isinstance(value, str):
                assert "SELECT" not in value.upper()
                assert ".sqlite" not in value
        assert not hasattr(receipt, "_connection")


def test_a_controller_receipt_cannot_claim_an_unpersisted_transition():
    from src.durable_suspension_controller import SuspensionTransitionReceipt

    with pytest.raises(SuspensionControllerError):
        SuspensionTransitionReceipt(
            transition_id="t", controller_id="c",
            from_state=ControllerState.CLEAR, to_state=ControllerState.SUSPENDED,
            cause=SuspensionCause.OPERATOR_HALT,
            evidence_class=EvidenceClass.NONE, sequence=1, persisted=False,
        )


def test_controller_inspection_survives_restart(tmp_path):
    path = tmp_path / "controller.sqlite"
    with DurableSuspensionController(path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.suspend(
            CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="s-1", now=NOW,
        )
    with DurableSuspensionController(path) as reopened:
        receipt = reopened.inspect_transition("s-1")
        assert receipt is not None and receipt.controller_id == CONTROLLER


def test_a_permanent_halt_transition_remains_inspectable(tmp_path):
    with store(tmp_path) as ctl:
        ctl.register(CONTROLLER, now=NOW)
        ctl.escalate_to_permanent_halt(CONTROLLER, transition_id="h-1", now=NOW)
        receipt = ctl.inspect_transition("h-1")
        assert receipt is not None
        assert receipt.to_state is ControllerState.PERMANENT_HALT


def test_controller_inspection_does_not_make_an_identity_reusable(tmp_path):
    with suspended(tmp_path) as ctl:
        ctl.inspect_transition("s-1")
        with pytest.raises(SuspensionControllerError):
            ctl.suspend(
                CONTROLLER, cause=SuspensionCause.OPERATOR_HALT,
                transition_id="s-1", now=LATER,
            )


def test_controller_inspection_is_read_only(tmp_path):
    with suspended(tmp_path) as ctl:
        before = (ctl.total_event_count, ctl.load(CONTROLLER).state)
        for _ in range(3):
            ctl.inspect_transition("s-1")
            ctl.inspect_transition("absent")
        assert (ctl.total_event_count, ctl.load(CONTROLLER).state) == before


def test_a_malformed_persisted_controller_transition_fails_closed(tmp_path):
    with suspended(tmp_path) as ctl:
        ctl._connection.execute(
            "UPDATE controller_events SET to_state = 'ELSEWHERE' "
            "WHERE transition_id = ?",
            ("s-1",),
        )
        with pytest.raises(SuspensionControllerError):
            ctl.inspect_transition("s-1")
