"""Tests for the M55 (Issue #362) prospective-monitoring protocol:
session-completion timing, the prospective-boundary rule (first
eligible signal strictly after the freeze date), the once-and-exit run
decision, frozen-candidate-config enforcement (no M53 leakage), and the
append-only hash-chained checkpoint ledger primitives. Pure logic only
-- no network I/O, no simulation, committed before any prospective
evidence is read."""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from src.momentum_prospective_monitor import (
    NY_TZ,
    RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL,
    RUN_STATUS_NOT_DUE,
    RUN_STATUS_SIGNAL_DUE,
    MonitorProtocolError,
    assert_frozen_candidate_config,
    checkpoint_run_decision,
    compute_checkpoint_hash,
    first_eligible_signal_date,
    latest_completed_regular_session,
    month_end_sessions,
    next_pending_signal_date,
    to_ny_date,
    to_ny_datetime,
    validate_hash_chain,
)


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return days


# ---------------------------------------------------------------------------
# Timezone conversion.
# ---------------------------------------------------------------------------


def test_to_ny_datetime_rejects_naive_input():
    with pytest.raises(MonitorProtocolError):
        to_ny_datetime(datetime(2026, 8, 23, 12, 0))


def test_to_ny_date_converts_utc_evening_to_prior_ny_date():
    # 2026-08-24 02:00 UTC is 2026-08-23 22:00 EDT (UTC-4) -- still the prior NY day.
    moment = datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)
    assert to_ny_date(moment) == date(2026, 8, 23)


# ---------------------------------------------------------------------------
# Session-completion timing.
# ---------------------------------------------------------------------------


def test_latest_completed_session_returns_today_after_close():
    master_dates = _business_days(date(2026, 8, 17), 5)  # includes 2026-08-21 (Fri)
    today = master_dates[-1]
    now_utc = datetime.combine(today, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)  # 21:00 ET, well after 16:00 close
    result = latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates)
    assert result == today


def test_latest_completed_session_excludes_today_before_close():
    master_dates = _business_days(date(2026, 8, 17), 5)
    today = master_dates[-1]
    now_utc = datetime.combine(today, time(10, 0), tzinfo=NY_TZ).astimezone(timezone.utc)  # 10:00 ET, before 16:00 close
    result = latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates)
    assert result == master_dates[-2]


def test_latest_completed_session_respects_early_close_override():
    master_dates = _business_days(date(2026, 8, 17), 5)
    today = master_dates[-1]
    now_utc = datetime.combine(today, time(14, 0), tzinfo=NY_TZ).astimezone(timezone.utc)  # 14:00 ET
    # Without an override, 14:00 < 16:00 standard close -> today excluded.
    assert latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates) == master_dates[-2]
    # With an early-close override at 13:00, 14:00 > 13:00 -> today included.
    result = latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates, session_close_time={today: time(13, 0)})
    assert result == today


def test_latest_completed_session_none_when_now_precedes_all_master_dates():
    master_dates = _business_days(date(2026, 8, 17), 5)
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates) is None


def test_latest_completed_session_skips_a_non_session_today():
    # "Today" (per now_utc) is a Saturday, not in master_dates -- falls back
    # to the most recent prior session.
    master_dates = _business_days(date(2026, 8, 17), 5)  # ends Fri 2026-08-21
    saturday = date(2026, 8, 22)
    now_utc = datetime.combine(saturday, time(12, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    assert latest_completed_regular_session(now_utc=now_utc, master_dates=master_dates) == master_dates[-1]


# ---------------------------------------------------------------------------
# Month-end grouping and the prospective-boundary rule.
# ---------------------------------------------------------------------------


def test_month_end_sessions_picks_last_session_per_calendar_month():
    master_dates = _business_days(date(2026, 6, 25), 40)  # spans June, July, August
    result = month_end_sessions(master_dates)
    for d in result:
        assert d in master_dates
    months = [(d.year, d.month) for d in result]
    assert len(months) == len(set(months))  # exactly one entry per month
    assert result == tuple(sorted(result))


def test_first_eligible_signal_date_is_strictly_after_freeze():
    master_dates = _business_days(date(2026, 8, 1), 60)  # spans Aug, Sep, Oct
    month_ends = month_end_sessions(master_dates)
    aug_end = month_ends[0]
    freeze = date.fromordinal(aug_end.toordinal() - 5)  # mid-August, before month end
    result = first_eligible_signal_date(freeze_date_ny=freeze, master_dates=master_dates)
    assert result == aug_end


def test_first_eligible_signal_date_excludes_same_day_freeze():
    # Freezing ON the month-end date itself must NOT count that day's
    # own signal -- conservative "strictly after" reading.
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end = month_ends[0]
    result = first_eligible_signal_date(freeze_date_ny=aug_end, master_dates=master_dates)
    assert result == month_ends[1]
    assert result > aug_end


def test_first_eligible_signal_date_rolls_to_next_month_when_freeze_is_after_month_end():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end = month_ends[0]
    freeze = date.fromordinal(aug_end.toordinal() + 2)  # a couple of days into September
    result = first_eligible_signal_date(freeze_date_ny=freeze, master_dates=master_dates)
    assert result == month_ends[1]


def test_next_pending_signal_date_returns_first_eligible_when_nothing_processed():
    master_dates = _business_days(date(2026, 8, 1), 60)
    freeze = date(2026, 8, 10)
    result = next_pending_signal_date(last_processed_signal_date=None, freeze_date_ny=freeze, master_dates=master_dates)
    assert result == first_eligible_signal_date(freeze_date_ny=freeze, master_dates=master_dates)


def test_next_pending_signal_date_advances_past_last_processed():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    result = next_pending_signal_date(last_processed_signal_date=month_ends[0], freeze_date_ny=date(2026, 8, 10), master_dates=master_dates)
    assert result == month_ends[1]


# ---------------------------------------------------------------------------
# The once-and-exit run decision.
# ---------------------------------------------------------------------------


def test_checkpoint_run_decision_first_run_waiting_for_first_signal():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end = month_ends[0]
    freeze = date.fromordinal(aug_end.toordinal() - 5)
    # "Now" is well before August month-end -- the signal hasn't happened yet.
    now_utc = datetime.combine(freeze, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    result = checkpoint_run_decision(now_utc=now_utc, freeze_date_ny=freeze, master_dates=master_dates, last_processed_signal_date=None)
    assert result["status"] == RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL
    assert result["next_expected_signal_date"] == aug_end.isoformat()
    assert "signal_date" not in result  # never fabricated


def test_checkpoint_run_decision_first_run_signal_due():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end = month_ends[0]
    freeze = date.fromordinal(aug_end.toordinal() - 5)
    # "Now" is well after August month-end closed.
    now_utc = datetime.combine(aug_end, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    result = checkpoint_run_decision(now_utc=now_utc, freeze_date_ny=freeze, master_dates=master_dates, last_processed_signal_date=None)
    assert result["status"] == RUN_STATUS_SIGNAL_DUE
    assert result["signal_date"] == aug_end.isoformat()
    assert result["execution_date"] is not None
    assert date.fromisoformat(result["execution_date"]) > aug_end


def test_checkpoint_run_decision_subsequent_run_not_due():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end, sep_end = month_ends[0], month_ends[1]
    freeze = date.fromordinal(aug_end.toordinal() - 5)
    # "Now" is right after August's signal, before September's.
    now_utc = datetime.combine(aug_end, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    result = checkpoint_run_decision(now_utc=now_utc, freeze_date_ny=freeze, master_dates=master_dates, last_processed_signal_date=aug_end)
    assert result["status"] == RUN_STATUS_NOT_DUE
    assert result["next_expected_signal_date"] == sep_end.isoformat()


def test_checkpoint_run_decision_subsequent_run_signal_due():
    master_dates = _business_days(date(2026, 8, 1), 60)
    month_ends = month_end_sessions(master_dates)
    aug_end, sep_end = month_ends[0], month_ends[1]
    freeze = date.fromordinal(aug_end.toordinal() - 5)
    now_utc = datetime.combine(sep_end, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    result = checkpoint_run_decision(now_utc=now_utc, freeze_date_ny=freeze, master_dates=master_dates, last_processed_signal_date=aug_end)
    assert result["status"] == RUN_STATUS_SIGNAL_DUE
    assert result["signal_date"] == sep_end.isoformat()


def test_checkpoint_run_decision_raises_when_master_dates_too_short():
    # freeze_date is on/after the last date master_dates covers -- no
    # month-end session exists strictly after it, so the decision
    # cannot be determined without guessing.
    master_dates = _business_days(date(2026, 8, 1), 15)
    freeze = master_dates[-1]
    now_utc = datetime.combine(freeze, time(21, 0), tzinfo=NY_TZ).astimezone(timezone.utc)
    with pytest.raises(MonitorProtocolError):
        checkpoint_run_decision(now_utc=now_utc, freeze_date_ny=freeze, master_dates=master_dates, last_processed_signal_date=None)


# ---------------------------------------------------------------------------
# Frozen-candidate-config enforcement -- no M53 leakage.
# ---------------------------------------------------------------------------


def test_assert_frozen_candidate_config_accepts_m52_defaults():
    assert_frozen_candidate_config(top_n=15, vol_target_annualized=None)


def test_assert_frozen_candidate_config_rejects_breadth_30_leakage():
    with pytest.raises(MonitorProtocolError):
        assert_frozen_candidate_config(top_n=30, vol_target_annualized=None)


def test_assert_frozen_candidate_config_rejects_market_vol_leakage():
    from decimal import Decimal
    with pytest.raises(MonitorProtocolError):
        assert_frozen_candidate_config(top_n=15, vol_target_annualized=Decimal("0.15"))


# ---------------------------------------------------------------------------
# Append-only, hash-chained checkpoint ledger.
# ---------------------------------------------------------------------------


def test_compute_checkpoint_hash_is_deterministic_regardless_of_key_order():
    a = {"b": 2, "a": 1, "c": [1, 2, 3]}
    b = {"a": 1, "c": [1, 2, 3], "b": 2}
    assert compute_checkpoint_hash(a) == compute_checkpoint_hash(b)


def test_compute_checkpoint_hash_changes_with_content():
    assert compute_checkpoint_hash({"a": 1}) != compute_checkpoint_hash({"a": 2})


def test_validate_hash_chain_empty_list_is_valid():
    assert validate_hash_chain([]) is True


def test_validate_hash_chain_single_valid_checkpoint():
    content = {"checkpoint_index": 0, "previous_checkpoint_sha256": None, "status": "INITIALIZATION"}
    content["checkpoint_sha256"] = compute_checkpoint_hash(content)
    assert validate_hash_chain([content]) is True


def test_validate_hash_chain_rejects_first_checkpoint_with_nonnull_previous():
    content = {"checkpoint_index": 0, "previous_checkpoint_sha256": "deadbeef", "status": "INITIALIZATION"}
    content["checkpoint_sha256"] = compute_checkpoint_hash(content)
    assert validate_hash_chain([content]) is False


def test_validate_hash_chain_two_linked_checkpoints_valid():
    cp0 = {"checkpoint_index": 0, "previous_checkpoint_sha256": None, "status": "INITIALIZATION"}
    cp0["checkpoint_sha256"] = compute_checkpoint_hash(cp0)
    cp1 = {"checkpoint_index": 1, "previous_checkpoint_sha256": cp0["checkpoint_sha256"], "status": "SIGNAL"}
    cp1["checkpoint_sha256"] = compute_checkpoint_hash(cp1)
    assert validate_hash_chain([cp0, cp1]) is True


def test_validate_hash_chain_rejects_broken_link():
    cp0 = {"checkpoint_index": 0, "previous_checkpoint_sha256": None, "status": "INITIALIZATION"}
    cp0["checkpoint_sha256"] = compute_checkpoint_hash(cp0)
    cp1 = {"checkpoint_index": 1, "previous_checkpoint_sha256": "wrong-hash", "status": "SIGNAL"}
    cp1["checkpoint_sha256"] = compute_checkpoint_hash(cp1)
    assert validate_hash_chain([cp0, cp1]) is False


def test_validate_hash_chain_rejects_tampered_content():
    cp0 = {"checkpoint_index": 0, "previous_checkpoint_sha256": None, "status": "INITIALIZATION"}
    cp0["checkpoint_sha256"] = compute_checkpoint_hash(cp0)
    tampered = dict(cp0)
    tampered["status"] = "TAMPERED"  # content changed, hash not recomputed
    assert validate_hash_chain([tampered]) is False
