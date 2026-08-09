"""M14 Issue 7: commissioning suspension gate, orchestrator/provider
integration.

`tests/test_execution_safety_governance.py` already covers the pure gate
function (gate_commissioning_suspension_clear) and decision-authenticity
interactions directly. This file covers the layer above it: the
orchestrator's `_assess_suspension_state`/`_evaluate_write_safety` reading
fresh state per write, and a REAL `CommissioningSuspensionStateProvider`
backed by a REAL `DurableSuspensionController` sqlite store under
`tmp_path` -- nothing here opens a broker session, reaches the network, or
touches a credential.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.commissioning_suspension_identity import (
    CommissioningSuspensionIdentity,
    CommissioningSuspensionStateProvider,
)
from src.durable_suspension_controller import SuspensionCause
from src.one_share_commissioning import State
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter, make_orchestrator, run,
)

NOW = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
FINGERPRINT = "c" * 64
OTHER_FINGERPRINT = "d" * 64


def real_identity(**overrides) -> CommissioningSuspensionIdentity:
    base = dict(release_sha=RELEASE_SHA, account_fingerprint_reference=FINGERPRINT)
    base.update(overrides)
    return CommissioningSuspensionIdentity.derive(**base)


# ---------------------------------------------------------------------
# Orchestrator-level: assess_commissioning_suspension is read fresh, per
# write, and its result alone decides whether entry/stop/cancel/exit may
# proceed.
# ---------------------------------------------------------------------


def test_clear_allows_the_full_write_sequence(tmp_path):
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, assess_commissioning_suspension=lambda: "CLEAR",
    )
    result = run(instance)
    assert result.state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]


def test_suspended_blocks_the_first_write(tmp_path):
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, assess_commissioning_suspension=lambda: "SUSPENDED",
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []
    assert handoffs
    assert handoffs[-1].reason_code == "ENTRY_EXECUTION_SAFETY_DENIED"


def test_read_failure_denies(tmp_path):
    def _raise():
        raise RuntimeError("private suspension-store detail")

    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, assess_commissioning_suspension=_raise,
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []
    assert "private suspension-store detail" not in repr(handoffs)


def test_unrecognized_return_value_denies(tmp_path):
    """A misbehaving injected callable (wrong type, or a string outside the
    known vocabulary) must never accidentally satisfy the gate."""
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, assess_commissioning_suspension=lambda: "clear",  # wrong case
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []


def test_suspension_is_reread_fresh_for_each_write_not_cached(tmp_path):
    """Proves the state is NOT captured once at orchestrator construction
    and reused: a callable that flips from CLEAR to SUSPENDED after the
    first read lets entry through but blocks stop."""
    calls = {"count": 0}

    def flip():
        calls["count"] += 1
        return "CLEAR" if calls["count"] == 1 else "SUSPENDED"

    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path, assess_commissioning_suspension=flip,
    )
    result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == ["entry"]
    assert calls["count"] >= 2
    assert handoffs[-1].reason_code == "STOP_EXECUTION_SAFETY_DENIED"


# Whether protective-stop and cancel are individually gated (not merely
# entry) is proven structurally in tests/test_execution_safety_governance.py
# via direct stop_context()/cancel_context() constructions -- gate_
# commissioning_suspension_clear does not even inspect role/action, so it
# is unconditionally applied to every write the same way regardless of
# role. See test_stop_and_cancel_contexts_also_require_suspension_clear
# there for the direct proof; an orchestrator-level equivalent would only
# re-test the same unconditional gate through a much more fragile harness
# (timing exactly which write attempt a flipped suspension callable lands
# on across a multi-stage reconciliation flow).


# ---------------------------------------------------------------------
# Provider-level: a REAL CommissioningSuspensionStateProvider backed by a
# REAL DurableSuspensionController sqlite store, wired through the
# orchestrator exactly as the launcher wires it in production.
# ---------------------------------------------------------------------


def test_real_provider_clear_allows_full_run(tmp_path):
    db_path = tmp_path / "suspension.sqlite"
    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as setup:
        setup.ensure_registered(now=NOW)

    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as live:
        instance, adapter, clock, handoffs = make_orchestrator(
            tmp_path, assess_commissioning_suspension=(
                lambda: live.assess().state.value
            ),
        )
        result = run(instance)
    assert result.state is State.COMPLETE
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]


def test_real_provider_suspended_denies_full_run(tmp_path):
    db_path = tmp_path / "suspension.sqlite"
    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as setup:
        setup.ensure_registered(now=NOW)
        setup._controller.suspend(
            setup.controller_id, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="t-1", now=NOW,
        )

    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as live:
        instance, adapter, clock, handoffs = make_orchestrator(
            tmp_path, assess_commissioning_suspension=(
                lambda: live.assess().state.value
            ),
        )
        result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []


def test_real_provider_unregistered_identity_denies_full_run(tmp_path):
    """The core Issue 7 property: absence of durable suspension evidence
    (nothing ever called ensure_registered/register for this identity)
    must not silently become permission. The live orchestrator path never
    calls ensure_registered() itself -- only assess()."""
    db_path = tmp_path / "suspension.sqlite"
    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as live:
        instance, adapter, clock, handoffs = make_orchestrator(
            tmp_path, assess_commissioning_suspension=(
                lambda: live.assess().state.value
            ),
        )
        result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []


def test_real_provider_wrong_identity_reference_denies(tmp_path):
    """A provider constructed for a DIFFERENT account_fingerprint_reference
    than the one that was registered CLEAR must not benefit from that
    registration -- its own controller_id is different and unregistered."""
    db_path = tmp_path / "suspension.sqlite"
    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as setup:
        setup.ensure_registered(now=NOW)

    with CommissioningSuspensionStateProvider(
        database_path=db_path,
        identity=real_identity(account_fingerprint_reference=OTHER_FINGERPRINT),
    ) as mismatched:
        instance, adapter, clock, handoffs = make_orchestrator(
            tmp_path, assess_commissioning_suspension=(
                lambda: mismatched.assess().state.value
            ),
        )
        result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []


def test_real_provider_corrupt_persisted_row_denies(tmp_path):
    db_path = tmp_path / "suspension.sqlite"
    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as setup:
        setup.ensure_registered(now=NOW)
        controller_id = setup.controller_id

    # Hand-corrupt the persisted row directly, bypassing every write-path
    # invariant the controller itself enforces.
    raw = sqlite3.connect(str(db_path))
    try:
        raw.execute(
            "UPDATE controllers SET state = ? WHERE controller_id = ?",
            ("NOT_A_REAL_STATE", controller_id),
        )
        raw.commit()
    finally:
        raw.close()

    with CommissioningSuspensionStateProvider(
        database_path=db_path, identity=real_identity(),
    ) as live:
        instance, adapter, clock, handoffs = make_orchestrator(
            tmp_path, assess_commissioning_suspension=(
                lambda: live.assess().state.value
            ),
        )
        result = run(instance)
    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == []
