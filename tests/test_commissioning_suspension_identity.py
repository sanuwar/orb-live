"""Commissioning suspension identity and durable-store convention (M14
Issue 3).

Every test is offline. Nothing opens a broker session, reaches the network,
or touches a credential; every database lives under `tmp_path`. The
properties that matter most:

1. **Identity is deterministic and reference-only** -- the same
   release/account/domain/namespace always produce the same `controller_id`,
   and no raw account number or token can ever participate.
2. **Restart durability** -- a suspension set by one provider instance is
   still SUSPENDED when a fresh instance re-reads the same durable store
   under the same identity.
3. **Fail closed** -- CLEAR is the only state that permits continuation;
   an unregistered identity, a malformed/corrupt row, and a read exception
   all deny, and registering never clears an existing suspension.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from datetime import datetime, timezone

import pytest

import src.commissioning_suspension_identity as identity_module
from src.commissioning_suspension_identity import (
    COMMISSIONING_SUSPENSION_NAMESPACE,
    EXECUTION_DOMAIN,
    CommissioningSuspensionIdentity,
    CommissioningSuspensionIdentityError,
    CommissioningSuspensionStateProvider,
    commissioning_suspension_controller_id,
)
from src.durable_suspension_controller import (
    ControllerState,
    DurableSuspensionController,
    SuspensionCause,
)
from src.execution_safety_governance import GateOutcome

MODULE_PATH = pathlib.Path(identity_module.__file__)

NOW = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)

RELEASE_SHA = "a" * 40
OTHER_RELEASE_SHA = "b" * 40
FINGERPRINT = "c" * 64
OTHER_FINGERPRINT = "d" * 64


def entry_context():
    """Minimal real `ExecutionSafetyContext`, mirroring the fixture in
    `tests/test_execution_safety_governance.py`, so `make_gate()`'s output
    can be exercised against a real context object."""
    from src.execution_safety_governance import (
        CommissioningGovernanceEvidence,
        CommissioningMode,
        ExecutionAction,
        ExecutionSafetyContext,
        OperatorAcknowledgements,
    )
    from src.one_share_commissioning import QUANTITY, SYMBOL

    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=True,
        operator_acknowledgements=OperatorAcknowledgements(
            acknowledges_loss_may_exceed_plan=True,
            operator_present=True,
            robinhood_ui_open=True,
            no_second_trade=True,
        ),
        source_hash_verified=True,
        release_sha=RELEASE_SHA,
        implementation_digest="e" * 64,
        account_fingerprint_matched=True,
        account_fingerprint_reference=FINGERPRINT,
    )
    return ExecutionSafetyContext(
        context_version="v1",
        decision_time=NOW,
        run_id="run-1",
        action=ExecutionAction.ENTRY_PLACE,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=str(QUANTITY),
        stop_price=None,
        order_id=None,
        governance_evidence=evidence,
    )


def identity(**overrides) -> CommissioningSuspensionIdentity:
    fields = dict(
        release_sha=RELEASE_SHA,
        account_fingerprint_reference=FINGERPRINT,
    )
    fields.update(overrides)
    return CommissioningSuspensionIdentity.derive(**fields)


def provider(tmp_path, *, db_name="commissioning_suspension.sqlite", **overrides):
    return CommissioningSuspensionStateProvider(
        database_path=tmp_path / db_name, identity=identity(**overrides)
    )


# ---------------------------------------------------------------------
# Identity derivation.
# ---------------------------------------------------------------------


def test_identity_is_deterministic():
    first = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT,
    )
    second = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT,
    )
    assert first == second


@pytest.mark.parametrize(
    "overrides",
    [
        dict(release_sha=OTHER_RELEASE_SHA),
        dict(account_fingerprint_reference=OTHER_FINGERPRINT),
        dict(execution_domain="SOME_OTHER_DOMAIN"),
        dict(namespace="orb-live/commissioning-suspension/v2"),
    ],
)
def test_identity_changes_with_any_input(overrides):
    baseline = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT,
    )
    fields = dict(release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT)
    fields.update(overrides)
    varied = commissioning_suspension_controller_id(**fields)
    assert varied != baseline


def test_identity_defaults_match_module_constants():
    explicit = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA,
        account_fingerprint_reference=FINGERPRINT,
        execution_domain=EXECUTION_DOMAIN,
        namespace=COMMISSIONING_SUSPENSION_NAMESPACE,
    )
    implicit = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT,
    )
    assert explicit == implicit


def test_identity_is_a_valid_controller_identifier(tmp_path):
    """The derived controller_id must satisfy
    `DurableSuspensionController`'s own identifier contract -- proven by
    actually registering it, not by re-implementing the regex here."""
    controller_id = commissioning_suspension_controller_id(
        release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT,
    )
    ctl = DurableSuspensionController(tmp_path / "raw.sqlite")
    record = ctl.register(controller_id, now=NOW)
    assert record.controller_id == controller_id
    assert record.state is ControllerState.CLEAR
    ctl.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_sha", "not-a-sha"),
        ("release_sha", "A" * 40),  # uppercase refused
        ("release_sha", "a" * 39),
        ("account_fingerprint_reference", "not-a-fingerprint"),
        ("account_fingerprint_reference", "c" * 63),
        ("account_fingerprint_reference", "raw-account-number-12345"),
        ("execution_domain", ""),
        ("execution_domain", "   "),
        ("namespace", ""),
    ],
)
def test_identity_rejects_malformed_inputs(field, value):
    fields = dict(release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT)
    fields[field] = value
    with pytest.raises(CommissioningSuspensionIdentityError):
        commissioning_suspension_controller_id(**fields)


def test_identity_rejects_non_string_inputs():
    with pytest.raises(CommissioningSuspensionIdentityError):
        commissioning_suspension_controller_id(
            release_sha=None, account_fingerprint_reference=FINGERPRINT,
        )


def test_provider_rejects_a_non_identity():
    with pytest.raises(CommissioningSuspensionIdentityError):
        CommissioningSuspensionStateProvider(
            database_path=pathlib.Path("unused.sqlite"), identity="not-an-identity"
        )


# ---------------------------------------------------------------------
# Restart durability.
# ---------------------------------------------------------------------


def test_restart_preserves_a_suspension(tmp_path):
    db_path = tmp_path / "shared.sqlite"

    first = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    first.ensure_registered(now=NOW)
    first._controller.suspend(
        first.controller_id,
        cause=SuspensionCause.OPERATOR_HALT,
        transition_id="t-1",
        now=NOW,
    )
    first.close()

    second = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    decision = second.assess()
    assert decision.state is ControllerState.SUSPENDED
    assert decision.cause is SuspensionCause.OPERATOR_HALT
    second.close()


def test_restart_does_not_reregister_over_a_suspension(tmp_path):
    db_path = tmp_path / "shared.sqlite"

    first = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    first.ensure_registered(now=NOW)
    first._controller.suspend(
        first.controller_id,
        cause=SuspensionCause.RISK_LIMIT_BREACH,
        transition_id="t-1",
        now=NOW,
    )
    first.close()

    second = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    record = second.ensure_registered(now=NOW)
    assert record.state is ControllerState.SUSPENDED
    assert record.cause is SuspensionCause.RISK_LIMIT_BREACH
    second.close()


def test_recover_on_restart_reads_state_back_unchanged(tmp_path):
    db_path = tmp_path / "shared.sqlite"
    first = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    registered = first.ensure_registered(now=NOW)
    first.close()

    second = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    recovered = second.recover_on_restart()
    assert recovered == registered
    second.close()


# ---------------------------------------------------------------------
# Fail closed: unknown, malformed, and read-failure states.
# ---------------------------------------------------------------------


def test_unregistered_identity_denies(tmp_path):
    p = provider(tmp_path)
    decision = p.assess()
    assert decision.state is ControllerState.MALFORMED_STATE
    p.close()


def test_clear_is_the_only_state_that_permits_continuation(tmp_path):
    p = provider(tmp_path)
    p.ensure_registered(now=NOW)
    decision = p.assess()
    assert decision.state is ControllerState.CLEAR
    gate = p.make_gate()
    result = gate(entry_context())
    assert result.outcome is GateOutcome.SATISFIED
    p.close()


@pytest.mark.parametrize(
    "cause",
    [
        SuspensionCause.OPERATOR_HALT,
        SuspensionCause.RISK_LIMIT_BREACH,
        SuspensionCause.RECONCILIATION_AMBIGUOUS,
    ],
)
def test_suspended_denies_through_the_real_gate(tmp_path, cause):
    p = provider(tmp_path)
    p.ensure_registered(now=NOW)
    p._controller.suspend(p.controller_id, cause=cause, transition_id="t-1", now=NOW)
    gate = p.make_gate()
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    # The gate's reason code names the controller *state*, not the cause --
    # every non-permanent-halt cause lands in SUSPENDED.
    assert result.reason_code == "DENY_SUSPENSION_SUSPENDED"
    decision = p.assess()
    assert decision.cause is cause
    p.close()


def test_permanent_halt_denies_and_has_no_clearance_path(tmp_path):
    p = provider(tmp_path)
    p.ensure_registered(now=NOW)
    p._controller.escalate_to_permanent_halt(p.controller_id, transition_id="t-1", now=NOW)
    decision = p.assess()
    assert decision.state is ControllerState.PERMANENT_HALT
    gate = p.make_gate()
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    p.close()


def test_corrupt_persisted_state_denies_never_defaults_to_clear(tmp_path):
    db_path = tmp_path / "corrupt.sqlite"
    p = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    p.ensure_registered(now=NOW)
    controller_id = p.controller_id
    p.close()

    # Hand-corrupt the persisted row directly, bypassing every write-path
    # guard, the way a hand-edited or partially-written file could.
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "UPDATE controllers SET state = 'NONSENSE_STATE' WHERE controller_id = ?",
        (controller_id,),
    )
    raw.commit()
    raw.close()

    reopened = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    decision = reopened.assess()
    assert decision.state is ControllerState.MALFORMED_STATE
    gate = reopened.make_gate()
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    reopened.close()


def test_read_failure_after_close_denies_through_the_gate(tmp_path):
    """A read against an already-closed connection is exactly the kind of
    read failure `make_suspension_gate` must fail closed against, not
    propagate as an unhandled exception."""
    p = provider(tmp_path)
    p.ensure_registered(now=NOW)
    gate = p.make_gate()
    p.close()
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_SUSPENSION_READ_FAILED"


# ---------------------------------------------------------------------
# Concurrent reads.
# ---------------------------------------------------------------------


def test_two_providers_can_read_the_same_store_concurrently(tmp_path):
    db_path = tmp_path / "shared.sqlite"
    writer = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    writer.ensure_registered(now=NOW)
    writer._controller.suspend(
        writer.controller_id, cause=SuspensionCause.STALE_DATA, transition_id="t-1", now=NOW,
    )

    reader_a = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    reader_b = CommissioningSuspensionStateProvider(database_path=db_path, identity=identity())
    try:
        decision_a = reader_a.assess()
        decision_b = reader_b.assess()
        assert decision_a.state is ControllerState.SUSPENDED
        assert decision_b.state is ControllerState.SUSPENDED
    finally:
        writer.close()
        reader_a.close()
        reader_b.close()


# ---------------------------------------------------------------------
# No wall clock, no accidental broker/network reach.
# ---------------------------------------------------------------------


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


def test_the_module_never_reads_a_clock():
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


def test_the_module_never_imports_broker_or_network_surfaces():
    forbidden_substrings = (
        "robinhood", "mcp", "socket", "http", "urllib", "requests",
        "broker_saga", "broker_action",
    )
    imported = set()
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for name in imported:
        lowered = name.lower()
        assert not any(term in lowered for term in forbidden_substrings), name


def test_the_module_is_not_reached_from_execution_safety_governance():
    """`execution_safety_governance.py` must not import this module -- the
    foundation stays out of the live commissioning gate list until a future
    milestone explicitly wires it in."""
    governance_path = MODULE_PATH.parent / "execution_safety_governance.py"
    imported = set()
    for node in ast.walk(ast.parse(governance_path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "src.commissioning_suspension_identity" not in imported
