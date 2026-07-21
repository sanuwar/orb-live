from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.shadow_campaign_store import (
    EVENT_HYPOTHETICAL_ENTRY,
    EVENT_HYPOTHETICAL_EXIT,
    NON_EXECUTABLE_PREFIX,
    ShadowCampaignStore,
    ShadowCampaignStoreError,
    non_executable_correlation_id,
    week_key_for,
)


ET = ZoneInfo("America/New_York")
CAMPAIGN = "camp-test"
RUN = "run-test"


def store_at(tmp_path: Path, name: str = "campaign.sqlite") -> ShadowCampaignStore:
    return ShadowCampaignStore(tmp_path / name)


def add_entry(
    store: ShadowCampaignStore,
    *,
    campaign: str = CAMPAIGN,
    run: str = RUN,
    trading_date: date,
    symbol: str = "AAPL",
    ordinal: int = 0,
    shares: int = 6,
    entry_price: str = "104.00",
    stop_price: str = "101.50",
    observed_hour: int = 9,
    observed_minute: int = 45,
):
    correlation_id = non_executable_correlation_id(
        shadow_campaign_id=campaign,
        trading_date=trading_date,
        symbol=symbol,
        ordinal=ordinal,
    )
    risk = Decimal(entry_price) - Decimal(stop_price)
    target = Decimal(entry_price) + risk * 2
    observed = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        observed_hour,
        observed_minute,
        tzinfo=ET,
    )
    result = store.record_event(
        shadow_campaign_id=campaign,
        shadow_run_id=run,
        event_type=EVENT_HYPOTHETICAL_ENTRY,
        correlation_id=correlation_id,
        trading_date=trading_date,
        dedup_key=f"{campaign}|entry|{trading_date.isoformat()}|{symbol}|{ordinal}",
        symbol=symbol,
        observed_at=observed,
        payload={
            "shares": shares,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": str(target),
            "risk_per_share": str(risk),
            "non_executable": True,
        },
    )
    return correlation_id, result


def add_exit(
    store: ShadowCampaignStore,
    *,
    correlation_id: str,
    campaign: str = CAMPAIGN,
    run: str = RUN,
    trading_date: date,
    symbol: str = "AAPL",
    realized_pnl: str,
    observed_hour: int = 15,
    observed_minute: int = 0,
):
    observed = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        observed_hour,
        observed_minute,
        tzinfo=ET,
    )
    return store.record_event(
        shadow_campaign_id=campaign,
        shadow_run_id=run,
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
            "realized_pnl": realized_pnl,
            "non_executable": True,
        },
    )


# ---------- path guard ----------

def test_store_refuses_repository_data_path():
    repo_data = Path(__file__).resolve().parent.parent / "data" / "x.sqlite"
    with pytest.raises(ShadowCampaignStoreError):
        ShadowCampaignStore(repo_data)


def test_store_accepts_explicit_temporary_path(tmp_path):
    store = store_at(tmp_path)
    assert store.event_count() == 0


# ---------- append-only + non-executable ----------

def test_updates_are_rejected(tmp_path):
    store = store_at(tmp_path)
    add_entry(store, trading_date=date(2026, 7, 20))
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE shadow_events SET symbol = 'ZZZ'")
    connection.close()


def test_deletes_are_rejected(tmp_path):
    store = store_at(tmp_path)
    add_entry(store, trading_date=date(2026, 7, 20))
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM shadow_events")
    connection.close()


def test_non_executable_check_constraint_blocks_executable_row(tmp_path):
    store = store_at(tmp_path)
    connection = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO shadow_events (
                dedup_key, shadow_campaign_id, shadow_run_id, event_type,
                correlation_id, non_executable, symbol, trading_date,
                week_key, observed_at, payload_json, created_at
            ) VALUES (
                'k', 'c', 'r', 'e', 'HYPO-NONEXEC-x', 0, 'AAPL',
                '2026-07-20', '2026-07-20', NULL, '{}', '2026-07-20T00:00:00'
            )
            """
        )
    connection.close()


def test_correlation_id_must_be_non_executable(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ShadowCampaignStoreError):
        store.record_event(
            shadow_campaign_id=CAMPAIGN,
            shadow_run_id=RUN,
            event_type=EVENT_HYPOTHETICAL_ENTRY,
            correlation_id="orb-saga-1234",
            trading_date=date(2026, 7, 20),
            dedup_key="k",
            payload={},
        )


def test_correlation_id_is_not_shaped_like_a_broker_ref_id():
    correlation_id = non_executable_correlation_id(
        shadow_campaign_id=CAMPAIGN,
        trading_date=date(2026, 7, 20),
        symbol="AAPL",
        ordinal=0,
    )
    assert correlation_id.startswith(NON_EXECUTABLE_PREFIX)
    assert not correlation_id.startswith("orb-saga")
    # The trailing digest is a UUID, but the whole id is not a bare UUID.
    import uuid

    with pytest.raises(ValueError):
        uuid.UUID(correlation_id)


def test_observed_at_must_be_timezone_aware(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ShadowCampaignStoreError):
        store.record_event(
            shadow_campaign_id=CAMPAIGN,
            shadow_run_id=RUN,
            event_type=EVENT_HYPOTHETICAL_ENTRY,
            correlation_id=f"{NON_EXECUTABLE_PREFIX}x",
            trading_date=date(2026, 7, 20),
            dedup_key="k",
            observed_at=datetime(2026, 7, 20, 9, 45),
        )


# ---------- idempotency ----------

def test_duplicate_dedup_key_is_ignored(tmp_path):
    store = store_at(tmp_path)
    _, (first_id, first_inserted) = add_entry(
        store, trading_date=date(2026, 7, 20)
    )
    _, (second_id, second_inserted) = add_entry(
        store, trading_date=date(2026, 7, 20)
    )
    assert first_inserted is True
    assert second_inserted is False
    assert first_id == second_id
    assert store.event_count() == 1


def test_start_campaign_is_idempotent(tmp_path):
    store = store_at(tmp_path)
    store.start_campaign(
        shadow_campaign_id=CAMPAIGN,
        shadow_run_id=RUN,
        trading_date=date(2026, 7, 20),
    )
    store.start_campaign(
        shadow_campaign_id=CAMPAIGN,
        shadow_run_id="run-2",
        trading_date=date(2026, 7, 20),
    )
    assert store.event_count(CAMPAIGN) == 1


# ---------- week key + DST ----------

@pytest.mark.parametrize(
    "day,expected_monday",
    [
        (date(2026, 7, 20), "2026-07-20"),  # Monday
        (date(2026, 7, 24), "2026-07-20"),  # Friday
        (date(2026, 7, 26), "2026-07-20"),  # Sunday
        (date(2026, 7, 27), "2026-07-27"),  # next Monday
    ],
)
def test_week_key_is_monday_start_end_exclusive(day, expected_monday):
    assert week_key_for(day) == expected_monday


def test_week_key_across_spring_dst_boundary():
    # 2026-03-08 is the US spring-forward Sunday; it belongs to the week
    # that began Monday 2026-03-02, not the following Monday 2026-03-09.
    assert week_key_for(date(2026, 3, 8)) == "2026-03-02"
    assert week_key_for(date(2026, 3, 9)) == "2026-03-09"


def test_week_key_across_fall_dst_boundary():
    # 2026-11-01 is the US fall-back Sunday.
    assert week_key_for(date(2026, 11, 1)) == "2026-10-26"
    assert week_key_for(date(2026, 11, 2)) == "2026-11-02"


# ---------- reconstruction ----------

def test_entries_and_open_position_reconstructed(tmp_path):
    store = store_at(tmp_path)
    trading_date = date(2026, 7, 20)
    correlation_id, _ = add_entry(store, trading_date=trading_date)

    state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=trading_date
    )
    assert state.entries_today == 1
    assert state.open_positions == 1
    assert state.open_position is not None
    assert state.open_position.correlation_id == correlation_id
    assert state.entries_for_symbol("AAPL") == 1

    add_exit(
        store,
        correlation_id=correlation_id,
        trading_date=trading_date,
        realized_pnl="30.00",
    )
    state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=trading_date
    )
    assert state.open_positions == 0
    assert state.open_position is None
    assert state.entries_today == 1
    assert state.daily_realized_pnl == Decimal("30.00")


def test_daily_rollover_does_not_reset_weekly(tmp_path):
    store = store_at(tmp_path)
    monday = date(2026, 7, 20)
    tuesday = date(2026, 7, 21)

    c1, _ = add_entry(store, trading_date=monday, ordinal=0)
    add_exit(store, correlation_id=c1, trading_date=monday, realized_pnl="-20.00")
    c2, _ = add_entry(store, trading_date=tuesday, ordinal=0)
    add_exit(store, correlation_id=c2, trading_date=tuesday, realized_pnl="10.00")

    tuesday_state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=tuesday
    )
    # Daily counters reset to Tuesday only; the week still spans both days.
    assert tuesday_state.entries_today == 1
    assert tuesday_state.daily_realized_pnl == Decimal("10.00")
    assert tuesday_state.weekly_realized_pnl == Decimal("-10.00")
    assert tuesday_state.campaign_realized_pnl == Decimal("-10.00")


def test_weekly_rollover_does_not_reset_campaign(tmp_path):
    store = store_at(tmp_path)
    friday = date(2026, 7, 24)
    next_monday = date(2026, 7, 27)

    c1, _ = add_entry(store, trading_date=friday, ordinal=0)
    add_exit(store, correlation_id=c1, trading_date=friday, realized_pnl="-40.00")
    c2, _ = add_entry(store, trading_date=next_monday, ordinal=0)
    add_exit(
        store, correlation_id=c2, trading_date=next_monday, realized_pnl="5.00"
    )

    monday_state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=next_monday
    )
    # New trading week: weekly pnl covers only Monday; campaign persists.
    assert monday_state.weekly_realized_pnl == Decimal("5.00")
    assert monday_state.campaign_realized_pnl == Decimal("-35.00")


def test_campaign_drawdown_persists_and_is_peak_to_trough(tmp_path):
    store = store_at(tmp_path)
    day = date(2026, 7, 20)
    # Cumulative pnl walks 0 -> +30 -> -15 -> -10: peak +30, trough -15,
    # so the peak-to-trough drawdown is 45.
    for ordinal, pnl in enumerate(["30.00", "-45.00", "5.00"]):
        c, _ = add_entry(store, trading_date=day, ordinal=ordinal)
        add_exit(store, correlation_id=c, trading_date=day, realized_pnl=pnl)

    state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=day
    )
    assert state.campaign_realized_pnl == Decimal("-10.00")
    assert state.campaign_drawdown == Decimal("45.00")


def test_explicit_campaign_rollover_is_independent(tmp_path):
    store = store_at(tmp_path)
    day = date(2026, 7, 20)
    c1, _ = add_entry(store, campaign="camp-1", trading_date=day)
    add_exit(
        store, campaign="camp-1", correlation_id=c1, trading_date=day,
        realized_pnl="-100.00",
    )

    # A new campaign starts clean; it never resets or inherits camp-1.
    fresh = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-2", as_of_trading_date=day
    )
    assert fresh.entries_today == 0
    assert fresh.campaign_realized_pnl == Decimal("0")
    assert fresh.campaign_drawdown == Decimal("0")

    original = store.reconstruct_campaign_state(
        shadow_campaign_id="camp-1", as_of_trading_date=day
    )
    assert original.campaign_realized_pnl == Decimal("-100.00")


def test_state_survives_restart(tmp_path):
    store = store_at(tmp_path)
    day = date(2026, 7, 20)
    c1, _ = add_entry(store, trading_date=day)
    add_exit(store, correlation_id=c1, trading_date=day, realized_pnl="12.00")

    # A brand-new store instance over the same path reconstructs identically.
    reopened = ShadowCampaignStore(store.db_path)
    state = reopened.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=day
    )
    assert state.campaign_realized_pnl == Decimal("12.00")
    assert state.entries_today == 1


def test_reconstruction_equals_running_state(tmp_path):
    store = store_at(tmp_path)
    days = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)]
    pnls = ["15.00", "-30.00", "22.00"]

    running_campaign = Decimal("0")
    running_peak = Decimal("0")
    running_drawdown = Decimal("0")
    for day, pnl in zip(days, pnls):
        c, _ = add_entry(store, trading_date=day)
        add_exit(store, correlation_id=c, trading_date=day, realized_pnl=pnl)
        running_campaign += Decimal(pnl)
        running_peak = max(running_peak, running_campaign)
        running_drawdown = max(running_drawdown, running_peak - running_campaign)

    state = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN, as_of_trading_date=days[-1]
    )
    assert state.campaign_realized_pnl == running_campaign
    assert state.campaign_drawdown == running_drawdown


def test_as_of_datetime_bounds_reconstruction(tmp_path):
    store = store_at(tmp_path)
    day = date(2026, 7, 20)
    correlation_id, _ = add_entry(
        store, trading_date=day, observed_hour=9, observed_minute=45
    )
    add_exit(
        store,
        correlation_id=correlation_id,
        trading_date=day,
        realized_pnl="30.00",
        observed_hour=10,
        observed_minute=0,
    )

    # As of 9:50, only the entry is visible: the position is still open.
    early = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN,
        as_of_trading_date=day,
        as_of_datetime=datetime(2026, 7, 20, 9, 50, tzinfo=ET),
    )
    assert early.open_positions == 1
    assert early.daily_realized_pnl == Decimal("0")

    # As of 10:05, the exit is visible too.
    late = store.reconstruct_campaign_state(
        shadow_campaign_id=CAMPAIGN,
        as_of_trading_date=day,
        as_of_datetime=datetime(2026, 7, 20, 10, 5, tzinfo=ET),
    )
    assert late.open_positions == 0
    assert late.daily_realized_pnl == Decimal("30.00")


def test_as_of_datetime_must_be_timezone_aware(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ShadowCampaignStoreError):
        store.reconstruct_campaign_state(
            shadow_campaign_id=CAMPAIGN,
            as_of_trading_date=date(2026, 7, 20),
            as_of_datetime=datetime(2026, 7, 20, 9, 50),
        )
