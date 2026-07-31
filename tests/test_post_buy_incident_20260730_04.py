'''Conservative offline coverage for the 2026-07-30 post-buy incident.'''

from __future__ import annotations

from decimal import Decimal

from src.one_share_commissioning import State
from src.robinhood_commissioning_adapter import CommissioningPositionRecord
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
    order,
    run,
)


class ExitReconciliationAdapter(FakeAdapter):
    def __init__(self, scenario: str) -> None:
        super().__init__()
        self.scenario = scenario
        self.exit_reads = 0

    async def get_equity_orders(self):
        if self.phase != 'exit':
            return await super().get_equity_orders()
        self._record('get_equity_orders')
        self.exit_reads += 1
        entry = order('entry-fixture', side='buy')
        stop = order(
            'stop-fixture', side='sell', order_type='stop_market',
            state='cancelled', cumulative=Decimal('0'),
            remaining=Decimal('1'), stop_price=Decimal('185'), average=None,
        )
        state = 'filled'
        cumulative = Decimal('1')
        remaining = Decimal('0')
        average = Decimal('190')
        if self.scenario in {'delayed_fill', 'flat_active'} and self.exit_reads == 1:
            state = 'confirmed'
            cumulative = Decimal('0')
            remaining = Decimal('1')
            average = None
        exit_order = order(
            'exit-1', side='sell', state=state,
            cumulative=cumulative, remaining=remaining, average=average,
        )
        return entry, stop, exit_order

    async def get_equity_positions(self):
        if self.phase != 'exit':
            return await super().get_equity_positions()
        self._record('get_equity_positions')
        if self.scenario == 'delayed_fill' and self.exit_reads == 1:
            return (CommissioningPositionRecord('AAPL', Decimal('1')),)
        if self.scenario == 'filled_long_conflict':
            return (CommissioningPositionRecord('AAPL', Decimal('1')),)
        return ()


def test_ambiguous_exit_fails_closed_without_duplicate_write(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        FakeAdapter(ambiguous_role='exit'),
    )

    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles.count('exit') == 1
    # An ambiguous exit is now read back rather than concluded. This fake never
    # reveals an attributable sell and keeps the share, so bounded reads run out
    # at the deadline and the run still fails closed with exactly one write.
    assert handoffs[-1].reason_code == 'EXIT_RECONCILIATION_TIMEOUT'
    assert instance.machine.evidence.write_attempt_counters['exit_place'] == 1


def test_delayed_exit_fill_reconciles_to_verified_flat(tmp_path):
    instance, adapter, clock, handoffs = make_orchestrator(
        tmp_path,
        ExitReconciliationAdapter('delayed_fill'),
    )

    assert run(instance).state is State.COMPLETE
    assert adapter.write_roles.count('exit') == 1
    assert adapter.exit_reads == 3
    assert clock.sleeps == [1.0, 1.0]
    assert handoffs == []


def test_flat_position_with_executable_exit_requires_operator(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        ExitReconciliationAdapter('flat_active'),
    )

    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles.count('exit') == 1
    assert handoffs[-1].reason_code == 'EXIT_POSITION_ORDER_MISMATCH'


def test_filled_exit_with_long_position_requires_operator(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        ExitReconciliationAdapter('filled_long_conflict'),
    )

    assert run(instance).state is State.OPERATOR_REQUIRED
    assert adapter.write_roles.count('exit') == 1
    assert handoffs[-1].reason_code == 'EXIT_FILL_POSITION_MISMATCH'
