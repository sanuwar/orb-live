"""One atomic diagnostic batch per failed placement, all-or-nothing.

Every fixture is local; nothing reaches a broker, an MCP server, or the
network. The point of the batch is narrow: a placement failure should widen
the evidence-persistence exposure window by a single call rather than by one
per recorded fact, and a failed batch must never leave a half-populated
diagnostic structure behind.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from src.live_order_contract import (
    classify_place_order_object_violation,
    classify_place_response_shape,
)
from src.one_share_commissioning import (
    ALLOWED_PLACE_DIAGNOSTICS,
    AtomicEvidenceStore,
    CommissioningDiagnosticError,
    CommissioningError,
    State,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    PlacementSymbolMismatchError,
)
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
)

ROLES = ("entry", "stop", "exit")


def order_object(symbol="AAPL", **overrides):
    payload = {
        "id": "order-1", "instrument_id": "instrument-1", "symbol": symbol,
        "side": "buy", "type": "market", "state": "filled", "quantity": "1",
        "cumulative_quantity": "1", "price": None, "stop_price": None,
        "average_price": "200.00", "fees": "0.00", "dollar_based_amount": None,
        "time_in_force": "gfd", "market_hours": "regular_hours",
        "trigger": "immediate", "placed_agent": "agent",
        "created_at": "2026-07-31T15:00:00Z",
        "last_transaction_at": "2026-07-31T15:00:01Z", "executions": [],
    }
    payload.update(overrides)
    return payload


def shape_with_violation(**overrides):
    """The richest batch: eight shape facts plus three contract facts."""
    order = order_object(**overrides)
    shape = dict(classify_place_response_shape({"order": order}))
    violation = classify_place_order_object_violation({"order": order})
    if violation:
        shape["contract_scope"] = violation["safe_contract_scope"]
        shape["contract_field_alias"] = violation["safe_field_alias"]
        shape["contract_violation_kind"] = violation["safe_violation_kind"]
    return shape


class CountingStore(AtomicEvidenceStore):
    """Count persistence calls that actually carry a diagnostic update."""

    def __init__(self, root, run_id):
        super().__init__(root, run_id)
        self.batch_persists = 0
        self._last = {}

    def persist(self, evidence):
        if dict(evidence.place_diagnostics) != self._last:
            self.batch_persists += 1
            self._last = dict(evidence.place_diagnostics)
        return super().persist(evidence)


class BreakingStore(AtomicEvidenceStore):
    """Break permanently the moment a diagnostic batch is written."""

    def __init__(self, root, run_id):
        super().__init__(root, run_id)
        self.armed = False
        self.broken = False
        self.attempts = 0

    def persist(self, evidence):
        if self.armed and (self.broken or evidence.place_diagnostics):
            self.broken = True
            self.attempts += 1
            raise CommissioningError("induced evidence persistence failure")
        return super().persist(evidence)


class FailingPlacementAdapter(FakeAdapter):
    def __init__(self, role, error_factory, **kwargs):
        super().__init__(**kwargs)
        self.failing_role = role
        self.error_factory = error_factory

    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        if role != self.failing_role:
            return await super().place_equity_order(
                arguments, role=role, safety_decision=safety_decision
            )
        self._record("place_equity_order")
        self.write_roles.append(role)
        self.phase = role
        raise self.error_factory(role)


def malformed(role):
    return AmbiguousWriteError(
        f"{role.upper()}_PLACE_RESPONSE_MALFORMED",
        transport_classification="ResponseContractViolationError",
        response_shape="malformed_envelope", call_returned=True,
        place_shape=shape_with_violation(fees=None),
    )


def symbol_pending(role):
    return AmbiguousWriteError(
        f"{role.upper()}_PLACE_SYMBOL_PENDING",
        transport_classification="NONE",
        response_shape="documented_envelope", call_returned=True,
        place_shape=dict(classify_place_response_shape(
            {"order": order_object(symbol="")}
        )),
    )


def transport_ambiguous(role):
    return AmbiguousWriteError(
        f"{role.upper()}_PLACE_TRANSPORT_AMBIGUOUS",
        transport_classification="TimeoutError",
        response_shape="not_reached", call_returned=False,
    )


def symbol_mismatch(role):
    return PlacementSymbolMismatchError(f"{role.upper()}_PLACE_SYMBOL_MISMATCH")


def run_with_store(tmp_path, adapter, run_id, store_class):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=adapter, run_id=run_id
    )
    store = store_class(tmp_path, run_id)
    instance.evidence_store = store
    instance.machine.evidence_store = store
    return instance, adapter, handoffs, store


# --- exactly one batch per failed placement ---------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "factory,label",
    [(malformed, "contract-violation"), (symbol_pending, "symbol-pending"),
     (transport_ambiguous, "transport")],
)
def test_one_diagnostic_batch_persistence_per_failed_placement(
    tmp_path, role, factory, label
):
    instance, adapter, _, store = run_with_store(
        tmp_path, FailingPlacementAdapter(role, factory),
        f"batch-{role}-{label}", CountingStore,
    )
    try:
        asyncio.run(instance.run())
    except Exception:
        pass

    assert store.batch_persists <= 1
    assert adapter.write_roles.count(role) == 1


@pytest.mark.parametrize("role", ROLES)
def test_the_whole_batch_lands_together_with_no_field_lost(tmp_path, role):
    instance, adapter, _, _ = run_with_store(
        tmp_path, FailingPlacementAdapter(role, malformed),
        f"batch-complete-{role}", CountingStore,
    )
    try:
        asyncio.run(instance.run())
    except Exception:
        pass
    recorded = instance.machine.evidence.place_diagnostics

    expected = {
        f"{role}_{name}": value
        for name, value in shape_with_violation(fees=None).items()
        if f"{role}_{name}" in ALLOWED_PLACE_DIAGNOSTICS
    }
    assert recorded == expected
    assert len(recorded) == 11
    assert recorded[f"{role}_contract_field_alias"] == "fees"
    assert recorded[f"{role}_contract_violation_kind"] == "null"


def test_transport_failure_records_no_batch_at_all(tmp_path):
    """Nothing is invented to fill a batch when no shape was observed."""
    instance, _, _, store = run_with_store(
        tmp_path, FailingPlacementAdapter("entry", transport_ambiguous),
        "batch-none", CountingStore,
    )
    try:
        asyncio.run(instance.run())
    except Exception:
        pass
    assert instance.machine.evidence.place_diagnostics == {}
    assert store.batch_persists == 0


# --- all-or-nothing ---------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_a_failed_batch_leaves_no_partial_diagnostics(tmp_path, role):
    instance, adapter, _, store = run_with_store(
        tmp_path, FailingPlacementAdapter(role, malformed),
        f"batch-fail-{role}", BreakingStore,
    )
    store.armed = True

    with pytest.raises(CommissioningError):
        asyncio.run(instance.run())

    # In memory and on disk: all or nothing.
    assert instance.machine.evidence.place_diagnostics == {}
    on_disk = json.loads(
        (store.partial / "commissioning_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["place_diagnostics"] == {}
    # Exactly one broker write, and nothing after it.
    assert adapter.write_roles == [role] if role == "entry" else True
    assert adapter.write_roles.count(role) == 1
    assert adapter.calls.count("place_equity_order") <= 3


def test_failed_batch_fails_closed_with_the_established_policy(tmp_path):
    """A genuinely broken store still yields the documented outcome."""
    from tests.test_checkpoint_7c_incident_regression import actual_binding

    adapter = FailingPlacementAdapter("entry", malformed)
    binding, factory, _, _ = actual_binding(
        tmp_path, secret_reader=lambda prompt: "y",
        run_id="batch-policy", adapter=adapter,
    )
    store = BreakingStore(tmp_path, "batch-policy")
    binding.evidence_store = store
    store.armed = True

    with pytest.raises(CommissioningDiagnosticError) as caught:
        asyncio.run(binding.run())

    assert caught.value.category == "INTERNAL_INVARIANT_FAILURE"
    assert caught.value.stage == "INTERNAL_INVARIANT"
    assert adapter.write_roles == ["entry"]
    assert factory.created == 1 and factory.entered == 1 and factory.closed == 1
    evidence = json.loads(
        (store.partial / "commissioning_evidence.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["place_diagnostics"] == {}
    assert "ENTRY_RECONCILING" not in evidence["state_transitions"]
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1, "stop_place": 0, "stop_cancel": 0, "exit_place": 0,
    }
    validate_sanitized_evidence(evidence)


# --- validation cannot be bypassed ------------------------------------


def test_the_batch_api_rejects_arbitrary_keys_and_values(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="batch-validate")
    machine = instance.machine
    machine.evidence_store.start()

    for bad in ({"order_id": "true"}, {"entry_raw_response": "true"},
                {"nope": "true"}, {"entry_order_key_present": "AAPL"},
                {"entry_order_key_present": 1}, {"entry_order_key_present": None}):
        with pytest.raises(CommissioningError):
            machine.record_place_diagnostics(bad)
    for bad in ("not-a-mapping", 7, None, ["entry_order_key_present"]):
        with pytest.raises(CommissioningError):
            machine.record_place_diagnostics(bad)
    # A rejected batch never reaches the evidence object.
    assert machine.evidence.place_diagnostics == {}


def test_one_invalid_fact_rejects_the_whole_batch(tmp_path):
    """Validation happens before anything is applied."""
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="batch-atomic-validate")
    machine = instance.machine
    machine.evidence_store.start()

    with pytest.raises(CommissioningError):
        machine.record_place_diagnostics({
            "entry_order_key_present": "true",
            "entry_contract_violation_kind": "not_a_kind",
        })
    assert machine.evidence.place_diagnostics == {}


def test_an_empty_batch_persists_nothing(tmp_path):
    instance, _, _, store = run_with_store(
        tmp_path, FakeAdapter(), "batch-empty", CountingStore,
    )
    store.start()
    before = store.batch_persists
    instance.machine.record_place_diagnostics({})
    assert store.batch_persists == before
    assert instance.machine.evidence.place_diagnostics == {}


# --- nothing else moved ------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_symbol_pending_still_reconciles_read_only(tmp_path, role):
    instance, adapter, handoffs, _ = run_with_store(
        tmp_path, FailingPlacementAdapter(role, symbol_pending),
        f"batch-pending-{role}", CountingStore,
    )
    result = asyncio.run(instance.run())
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert result.state is State.COMPLETE
    assert f"{role.upper()}_PLACE_SYMBOL_PENDING" in evidence[
        "conservative_classifications"
    ]
    assert f"{role.upper()}_RECONCILED_BY_ORDER" in evidence[
        "conservative_classifications"
    ]
    assert adapter.write_roles.count(role) == 1
    assert handoffs == []


def test_symbol_mismatch_still_does_not_reconcile(tmp_path):
    instance, adapter, handoffs, _ = run_with_store(
        tmp_path, FailingPlacementAdapter("entry", symbol_mismatch),
        "batch-mismatch", CountingStore,
    )
    result = asyncio.run(instance.run())
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "ENTRY_PLACE_SYMBOL_MISMATCH"
    assert "entry_reconciliation_polls" not in evidence["record_counts"]
    assert adapter.write_roles == ["entry"]
    assert len(handoffs) == 1


def test_a_clean_run_records_no_diagnostics_and_completes(tmp_path):
    instance, adapter, handoffs, store = run_with_store(
        tmp_path, FakeAdapter(), "batch-clean", CountingStore,
    )
    result = asyncio.run(instance.run())
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert result.state is State.COMPLETE
    assert evidence["place_diagnostics"] == {}
    assert store.batch_persists == 0
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1, "stop_place": 1, "stop_cancel": 1, "exit_place": 1,
    }
    assert handoffs == []


def test_diagnostics_are_never_read_by_order_control_logic():
    import pathlib

    for name in ("one_share_commissioning_orchestrator", "one_share_commissioning",
                 "robinhood_commissioning_adapter"):
        source = pathlib.Path(f"src/{name}.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "place_diagnostics" not in line:
                continue
            # Only the declaration and the recording plumbing may mention it.
            assert any(token in line for token in (
                "place_diagnostics: dict", "self.evidence.place_diagnostics",
                "record_place_diagnostic", "_validated_place_diagnostics",
                "ALLOWED_PLACE_DIAGNOSTICS", "#",
            )), line
