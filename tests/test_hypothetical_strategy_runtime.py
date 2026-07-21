from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.broker_identity import CommissionedAccount
from src.shadow_campaign_store import (
    EVENT_HYPOTHETICAL_ENTRY,
    EVENT_HYPOTHETICAL_EXIT,
    NON_EXECUTABLE_PREFIX,
    ShadowCampaignStore,
    non_executable_correlation_id,
)
from src.hypothetical_strategy_runtime import (
    AccountObservation,
    CycleObservation,
    HypotheticalRuntimeError,
    HypotheticalStrategyRuntime,
    SessionClassification,
    SessionSpec,
    ShadowClock,
    TerminalStatus,
)

import tests.shadow_fixtures as fx
from tests.call_site_guard import analyze_source, violations
from src.read_only_broker_gateway import READ_ONLY_TOOL_ALLOWLIST


ET = ZoneInfo("America/New_York")
DAY = fx.TRADING_DATE
WINDOW = (fx.et_dt(DAY, 9, 30), fx.et_dt(DAY, 16, 0))
CONFIG = load_config()
COMMISSIONED = CommissionedAccount(
    expected_fingerprint=fx.ACCOUNT_FINGERPRINT
)
MINUTE = timedelta(minutes=1)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def make_store(tmp_path: Path, name: str = "campaign.sqlite") -> ShadowCampaignStore:
    return ShadowCampaignStore(tmp_path / name)


def make_runtime(
    store: ShadowCampaignStore,
    *,
    run_id: str = "run-1",
    campaign: str = "camp-1",
    session: SessionSpec | None = None,
    max_cycles: int = 10,
    max_broker_calls: int = 20,
    config=None,
    clock_start: datetime | None = None,
) -> HypotheticalStrategyRuntime:
    clock = ShadowClock(clock_start or fx.et_dt(DAY, 9, 30))
    return HypotheticalStrategyRuntime(
        config or CONFIG,
        campaign_store=store,
        commissioned_account=COMMISSIONED,
        clock=clock,
        shadow_run_id=run_id,
        shadow_campaign_id=campaign,
        session=session or SessionSpec(trading_date=DAY),
        max_cycles=max_cycles,
        max_broker_calls=max_broker_calls,
        observation_window=WINDOW,
    )


def ok_account(*, cash: str = "5000.00", positions=(), open_orders=()) -> AccountObservation:
    return AccountObservation.ok(
        fx.make_snapshot(
            completed_at=fx.et_dt(DAY, 9, 29, 58),
            cash=cash,
            positions=positions,
            open_orders=open_orders,
        )
    )


def opening_bars(symbol, *, gap_open="102.00", high="103.00", low="101.50", vol=700_000):
    bars = []
    for minute in range(5):
        open_price = gap_open if minute == 0 else low
        bars.append(
            fx.bar(
                symbol,
                fx.et_dt(DAY, 9, 30 + minute),
                open_price,
                high,
                low,
                low,
                volume=vol,
            )
        )
    return bars


def spy_series(now: datetime, *, above=True):
    fresh = now - MINUTE
    if above:
        return [
            fx.bar("SPY", fx.et_dt(DAY, 9, 30), "498", "499", "497", "498"),
            fx.bar("SPY", fresh, "499", "501", "499", "500"),
        ]
    return [
        fx.bar("SPY", fx.et_dt(DAY, 9, 30), "501", "503", "501", "502"),
        fx.bar("SPY", fresh, "500", "500", "498", "498"),
    ]


def entry_cycle(
    *,
    symbol="AAPL",
    now_hm=(9, 45),
    gap_open="102.00",
    high="103.00",
    low="101.50",
    vol=700_000,
    breakout_close="104.00",
    breakout_completed=True,
    spy_above=True,
    prev_close="100.00",
    opening_volume=1_000_000,
) -> CycleObservation:
    now = fx.et_dt(DAY, *now_hm)
    current = opening_bars(symbol, gap_open=gap_open, high=high, low=low, vol=vol)

    if breakout_completed:
        current.append(
            fx.bar(symbol, now - MINUTE, high, breakout_close, high, breakout_close, volume=500_000)
        )
    else:
        # A fresh, completed, non-breakout bar, and the breakout only in a
        # not-yet-completed bar at `now`.
        current.append(fx.bar(symbol, now - MINUTE, high, high, low, high, volume=500_000))
        current.append(fx.bar(symbol, now, high, breakout_close, high, breakout_close, volume=500_000))

    current.sort(key=lambda item: item.begins_at)
    return CycleObservation(
        now=now,
        current_bars={symbol: current, "SPY": spy_series(now, above=spy_above)},
        historical_bars={
            symbol: fx.historical_five_minute_bars(
                symbol, opening_volume=opening_volume, previous_close=prev_close
            )
        },
    )


def manage_cycle(
    *,
    symbol="AAPL",
    now_hm=(10, 0),
    high,
    low,
    close,
) -> CycleObservation:
    now = fx.et_dt(DAY, *now_hm)
    current = opening_bars(symbol)
    current.append(
        fx.bar(symbol, now - MINUTE, close, high, low, close, volume=100_000)
    )
    current.sort(key=lambda item: item.begins_at)
    return CycleObservation(
        now=now,
        current_bars={symbol: current, "SPY": spy_series(now)},
        historical_bars={symbol: fx.historical_five_minute_bars(symbol)},
    )


def seed_entry(store, *, campaign="camp-1", symbol="AAPL", trading_date, ordinal=0, observed):
    correlation_id = non_executable_correlation_id(
        shadow_campaign_id=campaign,
        trading_date=trading_date,
        symbol=symbol,
        ordinal=ordinal,
    )
    store.record_event(
        shadow_campaign_id=campaign,
        shadow_run_id="seed",
        event_type=EVENT_HYPOTHETICAL_ENTRY,
        correlation_id=correlation_id,
        trading_date=trading_date,
        dedup_key=f"{campaign}|entry|{trading_date.isoformat()}|{symbol}|{ordinal}",
        symbol=symbol,
        observed_at=observed,
        payload={
            "shares": 6,
            "entry_price": "104.00",
            "stop_price": "101.50",
            "target_price": "109.00",
            "risk_per_share": "2.50",
            "non_executable": True,
        },
    )
    return correlation_id


def seed_exit(store, *, campaign="camp-1", symbol="AAPL", trading_date, correlation_id, pnl, observed):
    store.record_event(
        shadow_campaign_id=campaign,
        shadow_run_id="seed",
        event_type=EVENT_HYPOTHETICAL_EXIT,
        correlation_id=correlation_id,
        trading_date=trading_date,
        dedup_key=f"{campaign}|exit|{correlation_id}",
        symbol=symbol,
        observed_at=observed,
        payload={
            "correlation_id": correlation_id,
            "exit_kind": "target",
            "exit_price": "1",
            "shares": 6,
            "realized_pnl": str(pnl),
            "non_executable": True,
        },
    )


def entry_events(store, campaign="camp-1"):
    import sqlite3

    connection = sqlite3.connect(store.db_path)
    rows = connection.execute(
        "SELECT symbol, payload_json FROM shadow_events "
        "WHERE shadow_campaign_id = ? AND event_type = ?",
        (campaign, EVENT_HYPOTHETICAL_ENTRY),
    ).fetchall()
    connection.close()
    return rows


# ---------------------------------------------------------------------------
# constructor bounds
# ---------------------------------------------------------------------------

def test_requires_positive_max_cycles(tmp_path):
    with pytest.raises(HypotheticalRuntimeError):
        make_runtime(make_store(tmp_path), max_cycles=0)


def test_requires_positive_call_budget(tmp_path):
    with pytest.raises(HypotheticalRuntimeError):
        make_runtime(make_store(tmp_path), max_broker_calls=0)


def test_requires_ordered_timezone_aware_window(tmp_path):
    store = make_store(tmp_path)
    clock = ShadowClock(fx.et_dt(DAY, 9, 30))
    with pytest.raises(HypotheticalRuntimeError):
        HypotheticalStrategyRuntime(
            CONFIG,
            campaign_store=store,
            commissioned_account=COMMISSIONED,
            clock=clock,
            shadow_run_id="r",
            shadow_campaign_id="c",
            session=SessionSpec(trading_date=DAY),
            max_cycles=1,
            max_broker_calls=1,
            observation_window=(fx.et_dt(DAY, 16, 0), fx.et_dt(DAY, 9, 30)),
        )


def test_clock_only_moves_forward():
    clock = ShadowClock(fx.et_dt(DAY, 10, 0))
    with pytest.raises(HypotheticalRuntimeError):
        clock.advance_to(fx.et_dt(DAY, 9, 0))


# ---------------------------------------------------------------------------
# session integrity (regular sessions only)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "classification",
    [
        SessionClassification.HOLIDAY,
        SessionClassification.EARLY_CLOSE,
        SessionClassification.MISSING,
        SessionClassification.UNCERTAIN,
    ],
)
def test_non_regular_session_requires_operator(tmp_path, classification):
    store = make_store(tmp_path)
    runtime = make_runtime(
        store,
        session=SessionSpec(trading_date=DAY, classification=classification),
    )
    result = runtime.run(account_observation=ok_account(), cycles=[entry_cycle()])
    assert result.status is TerminalStatus.OPERATOR_REQUIRED
    assert not result.is_clean_completion
    assert store.event_count() == 0


def test_time_exit_never_fires_on_shortened_session(tmp_path):
    store = make_store(tmp_path)
    correlation_id = seed_entry(
        store, trading_date=DAY, observed=fx.et_dt(DAY, 9, 45)
    )
    runtime = make_runtime(
        store,
        session=SessionSpec(
            trading_date=DAY, classification=SessionClassification.EARLY_CLOSE
        ),
    )
    # A 15:46 cycle would trigger the time exit on a regular session.
    result = runtime.run(
        account_observation=ok_account(),
        cycles=[manage_cycle(now_hm=(15, 46), high="105", low="104", close="105")],
    )
    assert result.status is TerminalStatus.OPERATOR_REQUIRED
    # No exit was recorded; the position is untouched.
    state = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.open_positions == 1


# ---------------------------------------------------------------------------
# account evidence
# ---------------------------------------------------------------------------

def test_fingerprint_match_allows_run(tmp_path):
    store = make_store(tmp_path)
    result = make_runtime(store).run(
        account_observation=ok_account(), cycles=[entry_cycle()]
    )
    assert result.status is TerminalStatus.COMPLETED_SHADOW_SIGNAL


def test_fingerprint_mismatch_terminates(tmp_path):
    store = make_store(tmp_path)
    wrong = fx.make_snapshot(
        completed_at=fx.et_dt(DAY, 9, 29, 58),
        account_number="ACCT-OTHER-9999",
    )
    result = make_runtime(store).run(
        account_observation=AccountObservation.ok(wrong), cycles=[entry_cycle()]
    )
    assert result.status is TerminalStatus.TERMINATED_ACCOUNT_MISMATCH
    assert store.event_count() == 0


def test_account_unavailable_is_incomplete_evidence(tmp_path):
    store = make_store(tmp_path)
    result = make_runtime(store).run(
        account_observation=AccountObservation.unavailable(), cycles=[entry_cycle()]
    )
    assert result.status is TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE


def test_account_stale_terminates(tmp_path):
    store = make_store(tmp_path)
    result = make_runtime(store).run(
        account_observation=AccountObservation.stale(), cycles=[entry_cycle()]
    )
    assert result.status is TerminalStatus.TERMINATED_STALE_DATA


def test_stale_account_snapshot_by_age_terminates(tmp_path):
    store = make_store(tmp_path)
    old_snapshot = fx.make_snapshot(completed_at=fx.et_dt(DAY, 9, 0, 0))
    result = make_runtime(store).run(
        account_observation=AccountObservation.ok(old_snapshot),
        cycles=[entry_cycle()],
    )
    assert result.status is TerminalStatus.TERMINATED_STALE_DATA


def test_existing_position_conflict_terminates(tmp_path):
    store = make_store(tmp_path)
    result = make_runtime(store).run(
        account_observation=ok_account(positions=(fx.sample_position(),)),
        cycles=[entry_cycle()],
    )
    assert result.status is TerminalStatus.TERMINATED_ACCOUNT_CONFLICT
    assert store.event_count() == 0


def test_existing_open_order_conflict_terminates(tmp_path):
    store = make_store(tmp_path)
    result = make_runtime(store).run(
        account_observation=ok_account(open_orders=(fx.sample_open_order(),)),
        cycles=[entry_cycle()],
    )
    assert result.status is TerminalStatus.TERMINATED_ACCOUNT_CONFLICT


# ---------------------------------------------------------------------------
# bounds: call budget and cycle limit
# ---------------------------------------------------------------------------

def test_call_budget_exhaustion_terminates(tmp_path):
    store = make_store(tmp_path)
    # Budget of 1 is consumed by the account read, leaving nothing for a
    # cycle.
    runtime = make_runtime(store, max_broker_calls=1)
    result = runtime.run(account_observation=ok_account(), cycles=[entry_cycle()])
    assert result.status is TerminalStatus.TERMINATED_CALL_LIMIT
    assert result.broker_calls_used == 1


def test_cycle_limit_terminates(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store, max_cycles=1, max_broker_calls=20)
    cycles = [
        entry_cycle(symbol="AAPL", now_hm=(9, 45), breakout_completed=False),
        entry_cycle(symbol="AAPL", now_hm=(9, 50), breakout_completed=False),
    ]
    result = runtime.run(account_observation=ok_account(), cycles=cycles)
    assert result.status is TerminalStatus.TERMINATED_CYCLE_LIMIT
    assert result.cycles_executed == 1


def test_cycle_timestamp_outside_window_is_safety_terminal(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    late = CycleObservation(now=fx.et_dt(DAY, 17, 0))
    result = runtime.run(account_observation=ok_account(), cycles=[late])
    assert result.status is TerminalStatus.TERMINATED_SAFETY


# ---------------------------------------------------------------------------
# market-data integrity
# ---------------------------------------------------------------------------

def test_market_data_feed_error_terminates(tmp_path):
    store = make_store(tmp_path)
    cycle = CycleObservation(now=fx.et_dt(DAY, 9, 45), feed_status="market_data_error")
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_MARKET_DATA_ERROR


def test_paginated_market_data_is_incomplete_evidence(tmp_path):
    store = make_store(tmp_path)
    cycle = CycleObservation(now=fx.et_dt(DAY, 9, 45), feed_status="incomplete")
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE


def test_duplicate_bars_terminate(tmp_path):
    store = make_store(tmp_path)
    now = fx.et_dt(DAY, 9, 45)
    bars = opening_bars("AAPL")
    bars.append(bars[-1])  # duplicate timestamp
    cycle = CycleObservation(
        now=now, current_bars={"AAPL": bars, "SPY": spy_series(now)}, historical_bars={}
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_MARKET_DATA_ERROR


def test_out_of_order_bars_terminate(tmp_path):
    store = make_store(tmp_path)
    now = fx.et_dt(DAY, 9, 45)
    bars = list(reversed(opening_bars("AAPL")))
    cycle = CycleObservation(
        now=now, current_bars={"AAPL": bars, "SPY": spy_series(now)}, historical_bars={}
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_MARKET_DATA_ERROR


def test_malformed_bar_terminates(tmp_path):
    store = make_store(tmp_path)
    now = fx.et_dt(DAY, 9, 45)
    bad = fx.bar("AAPL", fx.et_dt(DAY, 9, 30), "100", "99", "101", "100")  # high < low
    cycle = CycleObservation(
        now=now, current_bars={"AAPL": [bad], "SPY": spy_series(now)}, historical_bars={}
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_MARKET_DATA_ERROR


def test_stale_completed_bar_terminates(tmp_path):
    store = make_store(tmp_path)
    now = fx.et_dt(DAY, 10, 30)
    # SPY latest completed bar is from 9:44 — far older than the freshness
    # window at 10:30.
    cycle = CycleObservation(
        now=now,
        current_bars={
            "AAPL": entry_cycle().current_bars["AAPL"],
            "SPY": [
                fx.bar("SPY", fx.et_dt(DAY, 9, 30), "498", "499", "497", "498"),
                fx.bar("SPY", fx.et_dt(DAY, 9, 44), "499", "501", "499", "500"),
            ],
        },
        historical_bars={"AAPL": fx.historical_five_minute_bars("AAPL")},
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_STALE_DATA


def test_incomplete_opening_range_is_incomplete_evidence(tmp_path):
    store = make_store(tmp_path)
    now = fx.et_dt(DAY, 9, 45)
    # Only three opening bars — the five-minute opening range cannot form.
    partial = opening_bars("AAPL")[:3]
    partial.append(fx.bar("AAPL", now - MINUTE, "103", "104", "103", "104"))
    cycle = CycleObservation(
        now=now,
        current_bars={"AAPL": partial, "SPY": spy_series(now)},
        historical_bars={"AAPL": fx.historical_five_minute_bars("AAPL")},
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE


# ---------------------------------------------------------------------------
# strategy rule boundaries
# ---------------------------------------------------------------------------

def test_gap_exactly_at_threshold_signals(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(
        gap_open="101.00", high="101.50", low="100.50", breakout_close="102.00"
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_SHADOW_SIGNAL


def test_gap_below_threshold_rejects(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(
        gap_open="100.50", high="101.50", low="100.00", breakout_close="102.00"
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL


def test_gap_down_never_enters_long(tmp_path):
    store = make_store(tmp_path)
    # Today opens below the previous close: a long entry must never form.
    cycle = entry_cycle(
        gap_open="98.00", high="99.00", low="97.50", breakout_close="99.50"
    )
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert store.event_count("camp-1") >= 0
    assert entry_events(store) == []


def test_volume_below_threshold_rejects(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(vol=599_999)  # 5 * 599_999 / 1_000_000 < 3.0
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert entry_events(store) == []


def test_volume_exactly_at_threshold_signals(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(vol=600_000)  # 5 * 600_000 / 1_000_000 == 3.0
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_SHADOW_SIGNAL


def test_spy_below_vwap_rejects(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(spy_above=False)
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert entry_events(store) == []


def test_before_entry_window_rejects(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(now_hm=(9, 39))
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert entry_events(store) == []


def test_after_entry_window_rejects(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(now_hm=(11, 5))
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert entry_events(store) == []


def test_breakout_counts_only_completed_candles(tmp_path):
    store = make_store(tmp_path)
    cycle = entry_cycle(breakout_completed=False)
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_NO_SIGNAL
    assert entry_events(store) == []


# ---------------------------------------------------------------------------
# hypothetical risk sizing and lifecycle
# ---------------------------------------------------------------------------

def test_risk_sizing_recorded(tmp_path):
    import json

    store = make_store(tmp_path)
    make_runtime(store).run(account_observation=ok_account(), cycles=[entry_cycle()])
    rows = entry_events(store)
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["shares"] == 6  # floor(15 / (104 - 101.5))
    assert payload["entry_price"] == "104.00"
    assert payload["stop_price"] == "101.50"
    assert payload["target_price"] == "109.00"
    assert payload["non_executable"] is True


def test_position_cap_binds(tmp_path):
    import json

    store = make_store(tmp_path)
    # A very tight stop makes risk-based sizing large, so the $3,000
    # position cap becomes the binding constraint.
    cycle = entry_cycle(high="103.95", low="103.90", gap_open="103.90", breakout_close="104.00")
    result = make_runtime(store).run(account_observation=ok_account(), cycles=[cycle])
    assert result.status is TerminalStatus.COMPLETED_SHADOW_SIGNAL
    payload = json.loads(entry_events(store)[0][1])
    assert payload["shares"] == 28  # floor(3000 / 104)


def test_target_exit_realises_two_r(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    cycles = [
        entry_cycle(),
        manage_cycle(now_hm=(10, 0), high="110", low="104", close="109"),
    ]
    result = runtime.run(account_observation=ok_account(), cycles=cycles)
    assert result.status is TerminalStatus.COMPLETED_SHADOW_SIGNAL
    state = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.open_positions == 0
    assert state.daily_realized_pnl == Decimal("30.00")  # (109 - 104) * 6


def test_stop_exit_realises_one_r_loss(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    cycles = [
        entry_cycle(),
        manage_cycle(now_hm=(10, 0), high="104", low="101", close="101.50"),
    ]
    runtime.run(account_observation=ok_account(), cycles=cycles)
    state = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.daily_realized_pnl == Decimal("-15.00")  # (101.5 - 104) * 6


def test_breakeven_then_flat_exit_is_scratch(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    cycles = [
        entry_cycle(),
        # +1R (>= 106.50) reached, no target, no stop: move stop to entry.
        manage_cycle(now_hm=(10, 0), high="107", low="105", close="106"),
        # Later the price falls back to the breakeven stop (entry 104).
        manage_cycle(now_hm=(10, 30), high="105", low="103", close="104"),
    ]
    runtime.run(account_observation=ok_account(), cycles=cycles)
    state = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.open_positions == 0
    assert state.daily_realized_pnl == Decimal("0.00")


def test_time_exit_at_1545(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    cycles = [
        entry_cycle(),
        manage_cycle(now_hm=(15, 46), high="105", low="104.50", close="105"),
    ]
    runtime.run(account_observation=ok_account(), cycles=cycles)
    state = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.open_positions == 0
    assert state.daily_realized_pnl == Decimal("6.00")  # (105 - 104) * 6


def test_same_symbol_not_re_entered_same_day(tmp_path):
    store = make_store(tmp_path)
    runtime = make_runtime(store)
    cycles = [
        entry_cycle(now_hm=(9, 45)),
        manage_cycle(now_hm=(10, 0), high="110", low="104", close="109"),  # exit
        entry_cycle(now_hm=(10, 15)),  # same symbol, same day
    ]
    runtime.run(account_observation=ok_account(), cycles=cycles)
    # Exactly one entry for AAPL despite a second qualifying cycle.
    assert len(entry_events(store)) == 1


# ---------------------------------------------------------------------------
# risk halts (portfolio-wide)
# ---------------------------------------------------------------------------

def test_two_entries_per_day_limit(tmp_path):
    store = make_store(tmp_path)
    # Seed two completed entries today; a third attempt must be rejected.
    for ordinal in range(2):
        symbol = ("MSFT", "NVDA")[ordinal]
        corr = seed_entry(
            store, symbol=symbol, trading_date=DAY, ordinal=ordinal,
            observed=fx.et_dt(DAY, 9, 40),
        )
        seed_exit(
            store, symbol=symbol, trading_date=DAY, correlation_id=corr,
            pnl="1.00", observed=fx.et_dt(DAY, 9, 41),
        )
    runtime = make_runtime(store)
    runtime.run(account_observation=ok_account(), cycles=[entry_cycle(symbol="AAPL")])
    assert [row for row in entry_events(store) if row[0] == "AAPL"] == []


def test_daily_loss_halt(tmp_path):
    store = make_store(tmp_path)
    corr = seed_entry(store, symbol="MSFT", trading_date=DAY, observed=fx.et_dt(DAY, 9, 40))
    seed_exit(store, symbol="MSFT", trading_date=DAY, correlation_id=corr, pnl="-45.00", observed=fx.et_dt(DAY, 9, 41))
    runtime = make_runtime(store)
    runtime.run(account_observation=ok_account(), cycles=[entry_cycle(symbol="AAPL")])
    assert [row for row in entry_events(store) if row[0] == "AAPL"] == []


def test_weekly_loss_halt(tmp_path):
    store = make_store(tmp_path)
    monday, tuesday = date(2026, 7, 20), date(2026, 7, 21)
    for day, sym in ((monday, "MSFT"), (tuesday, "NVDA")):
        corr = seed_entry(store, symbol=sym, trading_date=day, observed=fx.et_dt(day, 9, 40))
        seed_exit(store, symbol=sym, trading_date=day, correlation_id=corr, pnl="-60.00", observed=fx.et_dt(day, 9, 41))
    # Wednesday of the same week.
    wednesday = date(2026, 7, 22)
    window = (fx.et_dt(wednesday, 9, 30), fx.et_dt(wednesday, 16, 0))
    clock = ShadowClock(fx.et_dt(wednesday, 9, 30))
    runtime = HypotheticalStrategyRuntime(
        CONFIG, campaign_store=store, commissioned_account=COMMISSIONED, clock=clock,
        shadow_run_id="run-w", shadow_campaign_id="camp-1",
        session=SessionSpec(trading_date=wednesday), max_cycles=10, max_broker_calls=20,
        observation_window=window,
    )
    account = AccountObservation.ok(fx.make_snapshot(completed_at=fx.et_dt(wednesday, 9, 29, 58)))
    now = fx.et_dt(wednesday, 9, 45)
    cur = opening_bars("AAPL")
    cur.append(fx.bar("AAPL", now - MINUTE, "103.00", "104.00", "103.00", "104.00", volume=500_000))
    cur.sort(key=lambda item: item.begins_at)
    cycle = CycleObservation(
        now=now,
        current_bars={"AAPL": cur, "SPY": spy_series(now)},
        historical_bars={"AAPL": fx.historical_five_minute_bars("AAPL", trading_date=wednesday)},
    )
    runtime.run(account_observation=account, cycles=[cycle])
    assert [row for row in entry_events(store) if row[0] == "AAPL"] == []


def test_drawdown_halt(tmp_path):
    store = make_store(tmp_path)
    # A prior-week loss produces a campaign drawdown of $300.
    prior = date(2026, 7, 6)
    corr = seed_entry(store, symbol="MSFT", trading_date=prior, observed=fx.et_dt(prior, 9, 40))
    seed_exit(store, symbol="MSFT", trading_date=prior, correlation_id=corr, pnl="-300.00", observed=fx.et_dt(prior, 9, 41))
    runtime = make_runtime(store)
    runtime.run(account_observation=ok_account(), cycles=[entry_cycle(symbol="AAPL")])
    assert [row for row in entry_events(store) if row[0] == "AAPL"] == []


# ---------------------------------------------------------------------------
# idempotency and persistence
# ---------------------------------------------------------------------------

def test_replay_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    cycles = [
        entry_cycle(now_hm=(9, 45)),
        manage_cycle(now_hm=(10, 0), high="110", low="104", close="109"),
    ]
    make_runtime(store, run_id="run-1").run(account_observation=ok_account(), cycles=cycles)
    after_first = store.event_count("camp-1")
    make_runtime(store, run_id="run-1").run(account_observation=ok_account(), cycles=cycles)
    assert store.event_count("camp-1") == after_first


def test_limits_persist_across_runs_and_restart(tmp_path):
    path = tmp_path / "camp.sqlite"

    # Run 1 enters and exits MSFT.
    store1 = ShadowCampaignStore(path)
    make_runtime(store1, run_id="run-1").run(
        account_observation=ok_account(),
        cycles=[
            entry_cycle(symbol="MSFT", now_hm=(9, 45)),
            manage_cycle(symbol="MSFT", now_hm=(10, 0), high="110", low="104", close="109"),
        ],
    )

    # Run 2 (a fresh store instance — a restart) enters and exits NVDA.
    store2 = ShadowCampaignStore(path)
    make_runtime(store2, run_id="run-2").run(
        account_observation=ok_account(),
        cycles=[
            entry_cycle(symbol="NVDA", now_hm=(9, 45)),
            manage_cycle(symbol="NVDA", now_hm=(10, 0), high="110", low="104", close="109"),
        ],
    )

    state = store2.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=DAY
    )
    assert state.entries_today == 2

    # Run 3 attempts a third entry; the two-per-day limit now blocks it.
    store3 = ShadowCampaignStore(path)
    make_runtime(store3, run_id="run-3").run(
        account_observation=ok_account(),
        cycles=[entry_cycle(symbol="GOOGL", now_hm=(9, 45))],
    )
    assert [row for row in entry_events(store3) if row[0] == "GOOGL"] == []


# ---------------------------------------------------------------------------
# safety invariants
# ---------------------------------------------------------------------------

def test_strategy_enabled_stays_false_and_config_unmutated(tmp_path):
    store = make_store(tmp_path)
    supplied = copy.deepcopy(CONFIG)
    make_runtime(store, config=supplied).run(
        account_observation=ok_account(), cycles=[entry_cycle()]
    )
    # The supplied dict is not mutated and the on-disk config stays false.
    assert supplied["strategy"]["enabled"] is False
    assert load_config()["strategy"]["enabled"] is False


def test_risk_configuration_unchanged(tmp_path):
    store = make_store(tmp_path)
    supplied = copy.deepcopy(CONFIG)
    make_runtime(store, config=supplied).run(
        account_observation=ok_account(), cycles=[entry_cycle()]
    )
    assert supplied["risk"] == CONFIG["risk"]
    assert CONFIG["risk"]["short_selling_allowed"] is False
    assert CONFIG["risk"]["averaging_down_allowed"] is False


def test_every_recorded_action_is_non_executable(tmp_path):
    import json
    import sqlite3

    store = make_store(tmp_path)
    make_runtime(store).run(
        account_observation=ok_account(),
        cycles=[
            entry_cycle(),
            manage_cycle(now_hm=(10, 0), high="110", low="104", close="109"),
        ],
    )
    connection = sqlite3.connect(store.db_path)
    rows = connection.execute(
        "SELECT non_executable, correlation_id, payload_json FROM shadow_events"
    ).fetchall()
    connection.close()
    assert rows
    for non_executable, correlation_id, payload_json in rows:
        assert non_executable == 1
        assert correlation_id.startswith(NON_EXECUTABLE_PREFIX)
        assert not correlation_id.startswith("orb-saga")


def test_runtime_makes_no_broker_tool_call():
    source = (Path(__file__).resolve().parent.parent / "src" / "hypothetical_strategy_runtime.py").read_text(encoding="utf-8-sig")
    sites = analyze_source(source, path="src/hypothetical_strategy_runtime.py")
    # The runtime invokes no broker tool at all, so there is nothing that
    # could violate the read-only allowlist.
    assert sites == []
    assert violations(sites, READ_ONLY_TOOL_ALLOWLIST) == []


def test_store_has_only_the_shadow_events_table(tmp_path):
    import sqlite3

    store = make_store(tmp_path)
    connection = sqlite3.connect(store.db_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    connection.close()
    # No saga, lifecycle, or executable-order table exists.
    assert tables == {"shadow_events", "sqlite_sequence"}
