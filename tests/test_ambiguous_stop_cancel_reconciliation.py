"""Offline regression coverage for ambiguous protective-stop cancellation."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from checkpoint_7c_one_share_commissioning_launcher import RETIRED_LIVE_RUN_IDS
from src.one_share_commissioning import (
    AtomicEvidenceStore,
    State,
    validate_sanitized_evidence,
)
from src.one_share_commissioning_orchestrator import (
    OneShareCommissioningOrchestrator,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    CommissioningPositionRecord,
    RobinhoodCommissioningAdapter,
)
from tests.test_ambiguous_stop_reconciliation import (
    AmbiguousStopAdapter,
    Clock,
    EXPECTED_STOP,
    NOW,
    POLICY,
    authorization,
    order,
)


class CancelIncidentAdapter(AmbiguousStopAdapter):
    def __init__(
        self,
        scenario: str = "pending_then_cancelled",
        *,
        cancel_result: str = "malformed",
    ) -> None:
        super().__init__("pending")
        self.cancel_scenario = scenario
        self.cancel_result = cancel_result

    def _cancel_stop(self, state: str):
        cumulative = Decimal("0")
        remaining = Decimal("1")
        average = None
        if state == "filled":
            cumulative = Decimal("1")
            remaining = Decimal("0")
            average = EXPECTED_STOP
        elif state == "partially_filled_rest_cancelled":
            cumulative = Decimal("0.5")
            remaining = Decimal("0.5")
        return order(
            "stop-fixture",
            side="sell",
            order_type="stop_market",
            state=state,
            cumulative=cumulative,
            remaining=remaining,
            stop_value=EXPECTED_STOP,
            average=average,
        )

    async def get_equity_orders(self):
        if self.phase != "cancel":
            rows = await super().get_equity_orders()
            if (
                self.cancel_scenario == "already_terminal_before_cancel"
                and self.phase == "ambiguous_stop"
                and self.stop_reads >= 3
            ):
                return self._entry(), self._cancel_stop("cancelled")
            return rows

        self._record("get_equity_orders")
        self.cancel_reads += 1
        entry = self._entry()
        scenario = self.cancel_scenario
        if scenario == "pending_then_cancelled":
            state = "pending_cancelled" if self.cancel_reads == 1 else "cancelled"
            return entry, self._cancel_stop(state)
        if scenario == "pending_forever":
            return entry, self._cancel_stop("pending_cancelled")
        if scenario == "still_active":
            return entry, self._cancel_stop("queued")
        if scenario in {"cancelled_long", "cancelled_flat"}:
            return entry, self._cancel_stop("cancelled")
        if scenario in {"filled_flat", "filled_long"}:
            return entry, self._cancel_stop("filled")
        if scenario == "partial":
            return entry, self._cancel_stop("partially_filled_rest_cancelled")
        if scenario == "missing":
            return (entry,)
        if scenario == "multiple":
            stop = self._cancel_stop("pending_cancelled")
            return entry, stop, stop
        if scenario == "new_competing_sell":
            competitor = order(
                "new-sell-fixture",
                side="sell",
                state="queued",
                cumulative=Decimal("0"),
                remaining=Decimal("1"),
                average=None,
            )
            return entry, self._cancel_stop("pending_cancelled"), competitor
        raise AssertionError(f"unknown fake scenario: {scenario}")

    async def get_equity_positions(self):
        if self.phase != "cancel":
            return await super().get_equity_positions()
        self._record("get_equity_positions")
        if self.cancel_scenario in {"filled_flat", "cancelled_flat"}:
            return ()
        return (CommissioningPositionRecord("AAPL", Decimal("1")),)

    async def cancel_equity_order(self, arguments):
        self._record("cancel_equity_order")
        self.write_roles.append("cancel")
        self.phase = "cancel"
        if self.cancel_result == "malformed":
            raise AmbiguousWriteError("STOP_CANCEL_RESPONSE_MALFORMED")
        if self.cancel_result == "transport":
            raise AmbiguousWriteError("STOP_CANCEL_TRANSPORT_AMBIGUOUS")
        if self.cancel_result == "accepted":
            return SimpleNamespace(request_was_accepted=True)
        raise AssertionError(f"unknown fake cancel result: {self.cancel_result}")


def make_orchestrator(
    tmp_path,
    scenario: str = "pending_then_cancelled",
    *,
    cancel_result: str = "malformed",
    run_id: str = "cancel-run",
):
    adapter = CancelIncidentAdapter(scenario, cancel_result=cancel_result)
    clock = Clock()
    handoffs = []
    instance = OneShareCommissioningOrchestrator(
        run_id=run_id,
        adapter=adapter,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now_eastern=lambda: NOW,
        authorization_provider=lambda stage: authorization(run_id, stage),
        evidence_store=AtomicEvidenceStore(tmp_path, run_id),
        emergency_handoff=handoffs.append,
        strategy_enabled=False,
        regular_market_hours=True,
        time_policy=POLICY,
    )
    return instance, adapter, clock, handoffs


def run(instance):
    return asyncio.run(instance.run())


class Session:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    "cancel_result",
    ["malformed", "transport", "accepted"],
)
def test_possible_cancel_write_is_reconciled_without_retry(
    tmp_path, cancel_result
):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, cancel_result=cancel_result
    )
    result = run(instance)
    assert result.state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert adapter.write_roles.count("cancel") == 1
    assert instance.machine.evidence.record_counts["stop_cancel_retries"] == 0
    assert instance.machine.evidence.record_counts[
        "cancel_reconciliation_orders_reads"
    ] == 2
    assert instance.machine.evidence.record_counts[
        "cancel_reconciliation_positions_reads"
    ] == 2


def test_data_accepted_is_request_acceptance_not_terminal_cancellation(tmp_path):
    instance, adapter, clock, _ = make_orchestrator(
        tmp_path, cancel_result="accepted"
    )
    assert run(instance).state is State.COMPLETE
    categories = instance.machine.evidence.conservative_classifications
    assert "STOP_CANCEL_REQUEST_ACCEPTED" in categories
    assert "STOP_CANCEL_PENDING" in categories
    assert "STOP_CANCEL_CONFIRMED" in categories
    assert adapter.cancel_reads >= 2
    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(
    ("response", "error", "category"),
    [
        ({"accepted": True}, None, None),
        ({"accepted": "true"}, None, "STOP_CANCEL_RESPONSE_MALFORMED"),
        (None, TimeoutError(), "STOP_CANCEL_TRANSPORT_AMBIGUOUS"),
    ],
)
def test_cancel_adapter_response_categories_and_one_shot(
    response, error, category
):
    session = Session(response, error)
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    if category is None:
        parsed = asyncio.run(
            adapter.cancel_equity_order({"order_id": "transient"})
        )
        assert parsed.request_was_accepted is True
    else:
        with pytest.raises(AmbiguousWriteError) as captured:
            asyncio.run(
                adapter.cancel_equity_order({"order_id": "transient"})
            )
        assert captured.value.category == category
    with pytest.raises(Exception):
        asyncio.run(adapter.cancel_equity_order({"order_id": "transient"}))
    assert session.calls == 1


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("partial", "STOP_CANCEL_PARTIAL_FILL"),
        ("multiple", "STOP_CANCEL_MULTIPLE_MATCHES"),
        ("new_competing_sell", "STOP_CANCEL_MULTIPLE_MATCHES"),
        ("filled_long", "STOP_CANCEL_INCONSISTENT"),
    ],
)
def test_unsafe_cancel_reconciliation_requires_operator_without_exit(
    tmp_path, scenario, reason
):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, scenario)
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == reason
    assert adapter.write_roles.count("cancel") == 1
    assert "exit" not in adapter.write_roles


@pytest.mark.parametrize("scenario", ["pending_forever", "still_active", "missing"])
def test_unresolved_cancel_reconciliation_is_bounded(
    tmp_path, scenario
):
    instance, adapter, clock, handoffs = make_orchestrator(tmp_path, scenario)
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "STOP_CANCEL_TIMEOUT"
    assert adapter.write_roles.count("cancel") == 1
    assert "exit" not in adapter.write_roles
    assert len(clock.sleeps) == 30
    assert instance.machine.evidence.record_counts["stop_cancel_retries"] == 0


def test_attempt_20260730_04_transport_ambiguity_stays_active_until_timeout(
    tmp_path,
):
    run_id = 'cp7c-live-aapl-one-share-20260730-04'
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path,
        'still_active',
        cancel_result='transport',
        run_id=run_id,
    )

    result = run(instance)

    assert result.state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == 'STOP_CANCEL_TIMEOUT'
    assert adapter.write_roles == ['entry', 'stop', 'cancel']
    assert adapter.write_roles.count('cancel') == 1
    assert 'exit' not in adapter.write_roles
    evidence = instance.machine.evidence
    assert evidence.write_attempt_counters == {
        'entry_place': 1,
        'stop_place': 1,
        'stop_cancel': 1,
        'exit_place': 0,
    }
    assert evidence.record_counts['stop_cancel_retries'] == 0
    assert evidence.record_counts[
        'cancel_reconciliation_orders_reads'
    ] == evidence.record_counts['cancel_reconciliation_positions_reads']
    assert evidence.record_counts['cancel_reconciliation_orders_reads'] > 1
    assert evidence.boolean_validations['cancel_attribution_unique'] is True
    assert 'STOP_CANCEL_TRANSPORT_AMBIGUOUS' in (
        evidence.conservative_classifications
    )
    assert 'STOP_CANCEL_STILL_ACTIVE' in (
        evidence.conservative_classifications
    )
    assert evidence.conservative_classifications[-2:] == [
        'STOP_CANCEL_TIMEOUT',
        'OPERATOR_REQUIRED',
    ]
    assert len(clock.sleeps) == 30
    payload = json.loads(result.evidence_path.read_text(encoding='utf-8'))
    validate_sanitized_evidence(payload)


def test_stop_fill_during_cancel_reconciliation_finishes_without_sell(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, "filled_flat")
    assert run(instance).state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles == ["entry", "stop", "cancel"]
    assert "STOP_CANCEL_STOP_FILLED_POSITION_FLAT" in (
        instance.machine.evidence.conservative_classifications
    )


@pytest.mark.parametrize(
    ("scenario", "expected_exit_count"),
    [("cancelled_long", 1), ("cancelled_flat", 0)],
)
def test_terminal_cancel_controls_exit_by_latest_position(
    tmp_path, scenario, expected_exit_count
):
    instance, adapter, _, handoffs = make_orchestrator(tmp_path, scenario)
    assert run(instance).state is State.COMPLETE
    assert handoffs == []
    assert adapter.write_roles.count("exit") == expected_exit_count
    assert adapter.write_roles.count("cancel") == 1


def test_already_terminal_before_cancel_fails_closed_without_cancel_or_exit(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, "already_terminal_before_cancel"
    )
    assert run(instance).state is State.OPERATOR_REQUIRED
    assert handoffs[-1].reason_code == "STOP_CANCEL_ALREADY_TERMINAL"
    assert "cancel" not in adapter.write_roles
    assert "exit" not in adapter.write_roles


def test_cancel_evidence_is_sanitized(tmp_path):
    run_id = "sanitized-cancel-run"
    instance, _, _, _ = make_orchestrator(tmp_path, run_id=run_id)
    result = run(instance)
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    validate_sanitized_evidence(payload)
    rendered = json.dumps(payload, sort_keys=True)
    for prohibited in (
        "entry-fixture",
        "stop-fixture",
        "exit-fixture",
        format(EXPECTED_STOP, "f"),
    ):
        assert prohibited not in rendered
    assert payload["write_attempt_counters"] == {
        "entry_place": 1,
        "stop_place": 1,
        "stop_cancel": 1,
        "exit_place": 1,
    }
    assert payload["record_counts"]["stop_cancel_retries"] == 0


def test_failed_live_run_id_is_permanently_retired():
    assert 'cp7c-live-aapl-one-share-20260730-04' in RETIRED_LIVE_RUN_IDS
    assert "cp7c-live-aapl-one-share-20260730-05" in RETIRED_LIVE_RUN_IDS
    assert "cp7c-live-aapl-one-share-20260729-04" in RETIRED_LIVE_RUN_IDS
