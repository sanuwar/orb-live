"""Authoritative exchange calendar — coverage, boundaries, and fail-closed behavior.

Every test is local and offline. Nothing here opens a session, reaches a
broker, or touches the network, and the module under test is proven by parsing
to import nothing from the checkpoint implementation-digest files.

The property that matters most: **`is_regular_session` is true only when the
regular session provably was open.** Unsupported, expired, malformed, naive,
ambiguous, and non-datetime inputs must all be false.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date, datetime, time, timedelta

import pytest

import src.exchange_calendar as calendar
from src.exchange_calendar import (
    EARLY_CLOSE_DAYS,
    EARLY_REGULAR_CLOSE,
    EASTERN,
    FULL_HOLIDAYS,
    POST_MARKET_CLOSE,
    PRE_MARKET_OPEN,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    SCHEDULE_METADATA,
    UNKNOWN_REASONS,
    CalendarSession,
    ClassificationReason,
    DayKind,
    ExchangeCalendarError,
    ScheduleMetadata,
    SessionClassification,
    classify,
    is_regular_session,
    regular_session_bounds,
    schedule_is_expired,
    schedule_metadata,
    schedule_review_is_overdue,
    supported_range,
    validate_schedule,
)

MODULE_PATH = pathlib.Path(calendar.__file__)

#: The five files whose contents form the read-only checkpoint's implementation
#: digest. This module must stay independent of every one of them.
PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)


def et(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN)


# --------------------------------------------------------------------------
# Independent expectations.
#
# These are hand-written literals, transcribed from the published NYSE
# schedule and cross-checked against the standard observance rules: nth
# weekday for the Monday holidays, the fourth Thursday for Thanksgiving,
# Easter minus two days for Good Friday, and Saturday -> preceding Friday /
# Sunday -> following Monday for the fixed-date holidays, with New Year's Day
# as the standing exception.
#
# They are deliberately NOT derived from FULL_HOLIDAYS, EARLY_CLOSE_DAYS, or
# any production helper. Everything else in this file that touches the tables
# reads them from the module, which means a transcription error would agree
# with itself and pass. These two sets are the only place the dates are
# asserted against something written independently, so a typo in the shipped
# table fails here rather than shipping.
#
# Literals are appropriate because coverage is fixed at 2026-2027. Extending
# coverage means extending these sets by hand too, which is the intended cost.
# --------------------------------------------------------------------------

EXPECTED_FULL_HOLIDAYS = {
    # --- 2026 ---
    date(2026, 1, 1),     # New Year's Day, Thursday
    date(2026, 1, 19),    # Martin Luther King Jr. Day, 3rd Monday of January
    date(2026, 2, 16),    # Washington's Birthday, 3rd Monday of February
    date(2026, 4, 3),     # Good Friday (Easter 2026-04-05)
    date(2026, 5, 25),    # Memorial Day, last Monday of May
    date(2026, 6, 19),    # Juneteenth, Friday
    date(2026, 7, 3),     # Independence Day observed; 4 July 2026 is a Saturday
    date(2026, 9, 7),     # Labor Day, 1st Monday of September
    date(2026, 11, 26),   # Thanksgiving, 4th Thursday of November
    date(2026, 12, 25),   # Christmas Day, Friday
    # --- 2027 ---
    date(2027, 1, 1),     # New Year's Day, Friday
    date(2027, 1, 18),    # Martin Luther King Jr. Day, 3rd Monday of January
    date(2027, 2, 15),    # Washington's Birthday, 3rd Monday of February
    date(2027, 3, 26),    # Good Friday (Easter 2027-03-28)
    date(2027, 5, 31),    # Memorial Day, last Monday of May
    date(2027, 6, 18),    # Juneteenth observed; 19 June 2027 is a Saturday
    date(2027, 7, 5),     # Independence Day observed; 4 July 2027 is a Sunday
    date(2027, 9, 6),     # Labor Day, 1st Monday of September
    date(2027, 11, 25),   # Thanksgiving, 4th Thursday of November
    date(2027, 12, 24),   # Christmas observed; 25 December 2027 is a Saturday
}

EXPECTED_EARLY_CLOSES = {
    date(2026, 11, 27),   # day after Thanksgiving 2026
    date(2026, 12, 24),   # Christmas Eve 2026, a Thursday
    date(2027, 11, 26),   # day after Thanksgiving 2027
}


# --------------------------------------------------------------------------
# 1. Ordinary trading days.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        et(2026, 8, 3, 10, 0),    # Monday
        et(2026, 8, 5, 12, 0),    # Wednesday
        et(2026, 8, 7, 15, 59),   # Friday, last regular minute
        et(2027, 3, 1, 9, 30),    # first regular minute
        et(2027, 10, 14, 13, 30), # ordinary afternoon, well after 13:00
    ],
)
def test_an_ordinary_trading_day_is_a_regular_session(moment):
    result = classify(moment)
    assert result.session is CalendarSession.REGULAR
    assert result.day_kind is DayKind.TRADING_DAY
    assert result.reason is ClassificationReason.OK
    assert result.is_regular_session is True
    assert is_regular_session(moment) is True


def test_pre_market_is_not_a_regular_session():
    result = classify(et(2026, 8, 3, 8, 0))
    assert result.session is CalendarSession.PRE_MARKET
    assert result.reason is ClassificationReason.OK
    assert result.is_regular_session is False


def test_post_market_is_not_a_regular_session():
    result = classify(et(2026, 8, 3, 18, 0))
    assert result.session is CalendarSession.POST_MARKET
    assert result.is_regular_session is False


def test_overnight_is_closed_on_both_sides():
    before = classify(et(2026, 8, 3, 3, 59))
    after = classify(et(2026, 8, 3, 20, 0))
    assert before.session is CalendarSession.CLOSED
    assert before.reason is ClassificationReason.BEFORE_PRE_MARKET
    assert after.session is CalendarSession.CLOSED
    assert after.reason is ClassificationReason.AFTER_POST_MARKET


# --------------------------------------------------------------------------
# 2. Weekends.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        et(2026, 8, 1, 10, 0),   # Saturday
        et(2026, 8, 2, 10, 0),   # Sunday
        et(2026, 8, 8, 12, 0),
        et(2026, 8, 9, 12, 0),
        et(2027, 5, 1, 10, 0),
        et(2027, 5, 2, 10, 0),
    ],
)
def test_weekends_are_closed(moment):
    result = classify(moment)
    assert result.session is CalendarSession.CLOSED
    assert result.day_kind is DayKind.WEEKEND
    assert result.reason is ClassificationReason.WEEKEND
    assert result.is_regular_session is False


def test_every_weekend_day_in_range_is_closed():
    day = SCHEDULE_METADATA.first_supported_date
    checked = 0
    while day <= SCHEDULE_METADATA.last_supported_date:
        if day.weekday() >= 5:
            result = classify(datetime.combine(day, time(11, 0), tzinfo=EASTERN))
            assert result.day_kind is DayKind.WEEKEND, day
            assert result.is_regular_session is False, day
            checked += 1
        day += timedelta(days=1)
    assert checked > 200


# --------------------------------------------------------------------------
# 3. Every supported holiday, individually.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("holiday", sorted(FULL_HOLIDAYS), ids=str)
def test_every_supported_holiday_is_fully_closed(holiday):
    for hour, minute in ((5, 0), (9, 30), (12, 0), (15, 0), (18, 0)):
        moment = datetime.combine(holiday, time(hour, minute), tzinfo=EASTERN)
        result = classify(moment)
        assert result.session is CalendarSession.CLOSED, (holiday, hour)
        assert result.day_kind is DayKind.FULL_HOLIDAY
        assert result.reason is ClassificationReason.FULL_HOLIDAY
        assert result.is_regular_session is False


def test_the_shipped_holidays_match_the_independent_expected_set_exactly():
    """The only assertion that can catch a transcription error.

    Compared both directions so neither a missing nor an extra date can hide,
    and reported as sorted differences so a failure names the dates rather
    than printing two opaque sets.
    """
    assert sorted(FULL_HOLIDAYS - EXPECTED_FULL_HOLIDAYS) == [], "unexpected holiday"
    assert sorted(EXPECTED_FULL_HOLIDAYS - FULL_HOLIDAYS) == [], "missing holiday"
    assert FULL_HOLIDAYS == EXPECTED_FULL_HOLIDAYS


def test_the_shipped_early_closes_match_the_independent_expected_set_exactly():
    assert sorted(set(EARLY_CLOSE_DAYS) - EXPECTED_EARLY_CLOSES) == [], "unexpected"
    assert sorted(EXPECTED_EARLY_CLOSES - set(EARLY_CLOSE_DAYS)) == [], "missing"
    assert set(EARLY_CLOSE_DAYS) == EXPECTED_EARLY_CLOSES


def test_every_expected_holiday_is_individually_pinned_to_a_literal():
    """Guards the guard: the expected set must stay a complete literal list.

    If someone later replaces the literals with a comprehension over the
    production table, the assertion above becomes tautological and this
    catches the count drifting away from the reviewed 20.
    """
    assert len(EXPECTED_FULL_HOLIDAYS) == 20
    assert len(EXPECTED_EARLY_CLOSES) == 3
    assert sum(1 for day in EXPECTED_FULL_HOLIDAYS if day.year == 2026) == 10
    assert sum(1 for day in EXPECTED_FULL_HOLIDAYS if day.year == 2027) == 10
    assert EXPECTED_FULL_HOLIDAYS.isdisjoint(EXPECTED_EARLY_CLOSES)
    assert all(day.weekday() < 5 for day in EXPECTED_FULL_HOLIDAYS)
    assert all(day.weekday() < 5 for day in EXPECTED_EARLY_CLOSES)


def test_there_are_exactly_twenty_reviewed_holidays():
    assert len(FULL_HOLIDAYS) == 20
    assert len({day.year for day in FULL_HOLIDAYS}) == 2


def test_holidays_split_evenly_across_the_two_supported_years():
    assert sum(1 for day in FULL_HOLIDAYS if day.year == 2026) == 10
    assert sum(1 for day in FULL_HOLIDAYS if day.year == 2027) == 10


@pytest.mark.parametrize(
    "observed,reason",
    [
        (date(2026, 7, 3), "4 July 2026 is a Saturday"),
        (date(2027, 6, 18), "19 June 2027 is a Saturday"),
        (date(2027, 7, 5), "4 July 2027 is a Sunday"),
        (date(2027, 12, 24), "25 December 2027 is a Saturday"),
    ],
)
def test_weekend_holidays_are_observed_on_the_adjacent_weekday(observed, reason):
    assert observed in FULL_HOLIDAYS, reason
    assert observed.weekday() < 5


def test_new_years_day_keeps_its_standing_exception():
    """NYSE does not close the preceding Friday for a Saturday 1 January.

    2028-01-01 falls on a Saturday, so 2027-12-31 must NOT be a holiday. It is
    the one observance rule that does not follow the general pattern, and
    getting it wrong would close a day the market was open.
    """
    assert date(2027, 12, 31) not in FULL_HOLIDAYS
    result = classify(et(2027, 12, 31, 11, 0))
    assert result.is_regular_session is True


# --------------------------------------------------------------------------
# 4. Every supported early close, individually.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("day", sorted(EARLY_CLOSE_DAYS), ids=str)
def test_an_early_close_morning_is_a_regular_session(day):
    result = classify(datetime.combine(day, time(10, 0), tzinfo=EASTERN))
    assert result.session is CalendarSession.REGULAR
    assert result.day_kind is DayKind.EARLY_CLOSE_DAY
    assert result.is_regular_session is True


@pytest.mark.parametrize("day", sorted(EARLY_CLOSE_DAYS), ids=str)
def test_an_early_close_afternoon_is_closed_not_post_market(day):
    """The explicit requirement: CLOSED, never POST_MARKET."""
    for hour in (13, 14, 15, 17, 19):
        result = classify(datetime.combine(day, time(hour, 30), tzinfo=EASTERN))
        assert result.session is CalendarSession.CLOSED, (day, hour)
        assert result.session is not CalendarSession.POST_MARKET
        assert result.reason is ClassificationReason.AFTER_EARLY_CLOSE
        assert result.is_regular_session is False


@pytest.mark.parametrize("day", sorted(EARLY_CLOSE_DAYS), ids=str)
def test_the_early_close_boundary_is_one_pm(day):
    before = classify(datetime.combine(day, time(12, 59), tzinfo=EASTERN))
    at = classify(datetime.combine(day, time(13, 0), tzinfo=EASTERN))
    assert before.is_regular_session is True
    assert at.is_regular_session is False
    assert at.reason is ClassificationReason.AFTER_EARLY_CLOSE


@pytest.mark.parametrize("day", sorted(EARLY_CLOSE_DAYS), ids=str)
def test_an_early_close_day_still_has_a_pre_market(day):
    result = classify(datetime.combine(day, time(8, 0), tzinfo=EASTERN))
    assert result.session is CalendarSession.PRE_MARKET
    assert result.day_kind is DayKind.EARLY_CLOSE_DAY
    assert result.is_regular_session is False


def test_there_are_exactly_three_reviewed_early_closes():
    assert set(EARLY_CLOSE_DAYS) == {
        date(2026, 11, 27),
        date(2026, 12, 24),
        date(2027, 11, 26),
    }
    assert all(value == EARLY_REGULAR_CLOSE for value in EARLY_CLOSE_DAYS.values())


@pytest.mark.parametrize(
    "absent,why",
    [
        (date(2026, 7, 2), "4 July 2026 is a Saturday, so no eve early close"),
        (date(2027, 7, 2), "4 July 2027 is a Sunday, observed Monday 5 July"),
        (date(2027, 12, 23), "Christmas Eve 2027 is itself the observed holiday"),
    ],
)
def test_absent_early_closes_are_decisions_not_gaps(absent, why):
    assert absent not in EARLY_CLOSE_DAYS, why
    assert classify(
        datetime.combine(absent, time(14, 0), tzinfo=EASTERN)
    ).is_regular_session is True


# --------------------------------------------------------------------------
# 5. Exact boundaries.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clock,expected_session,expected_regular",
    [
        (time(3, 59), CalendarSession.CLOSED, False),
        (time(4, 0), CalendarSession.PRE_MARKET, False),
        (time(9, 29), CalendarSession.PRE_MARKET, False),
        (time(9, 30), CalendarSession.REGULAR, True),
        (time(12, 59), CalendarSession.REGULAR, True),
        (time(13, 0), CalendarSession.REGULAR, True),
        (time(15, 59), CalendarSession.REGULAR, True),
        (time(16, 0), CalendarSession.POST_MARKET, False),
        (time(19, 59), CalendarSession.POST_MARKET, False),
        (time(20, 0), CalendarSession.CLOSED, False),
        (time(23, 59), CalendarSession.CLOSED, False),
    ],
)
def test_full_trading_day_boundaries(clock, expected_session, expected_regular):
    moment = datetime.combine(date(2026, 8, 3), clock, tzinfo=EASTERN)
    result = classify(moment)
    assert result.session is expected_session
    assert result.is_regular_session is expected_regular


def test_the_boundary_constants_are_the_expected_wall_times():
    assert (PRE_MARKET_OPEN, REGULAR_OPEN) == (time(4, 0), time(9, 30))
    assert (REGULAR_CLOSE, POST_MARKET_CLOSE) == (time(16, 0), time(20, 0))
    assert EARLY_REGULAR_CLOSE == time(13, 0)


def test_regular_session_bounds_reflect_the_day():
    assert regular_session_bounds(date(2026, 8, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)
    assert regular_session_bounds(date(2026, 11, 27)) == (
        REGULAR_OPEN,
        EARLY_REGULAR_CLOSE,
    )


@pytest.mark.parametrize(
    "day",
    [date(2026, 8, 1), date(2026, 12, 25), date(2025, 8, 4), date(2028, 8, 7)],
)
def test_regular_session_bounds_are_absent_when_the_market_does_not_open(day):
    assert regular_session_bounds(day) is None


def test_regular_session_bounds_reject_a_datetime():
    assert regular_session_bounds(datetime(2026, 8, 3, 10, 0)) is None


# --------------------------------------------------------------------------
# 6. Daylight-saving transitions.
# --------------------------------------------------------------------------

SPRING_FORWARD = [date(2026, 3, 8), date(2027, 3, 14)]
FALL_BACK = [date(2026, 11, 1), date(2027, 11, 7)]


@pytest.mark.parametrize("day", SPRING_FORWARD + FALL_BACK, ids=str)
def test_dst_transition_days_are_sundays_and_therefore_closed(day):
    assert day.weekday() == 6
    result = classify(datetime.combine(day, time(11, 0), tzinfo=EASTERN))
    assert result.day_kind is DayKind.WEEKEND
    assert result.is_regular_session is False


@pytest.mark.parametrize("day", FALL_BACK, ids=str)
def test_an_ambiguous_fall_back_wall_time_is_refused(day):
    """01:30 names two instants. This module does not classify what it cannot pin down."""
    result = classify(datetime.combine(day, time(1, 30), tzinfo=EASTERN))
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is ClassificationReason.AMBIGUOUS_LOCAL_TIME
    assert result.is_regular_session is False


@pytest.mark.parametrize("day", SPRING_FORWARD, ids=str)
def test_a_nonexistent_spring_forward_wall_time_is_refused(day):
    result = classify(datetime.combine(day, time(2, 30), tzinfo=EASTERN))
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is ClassificationReason.AMBIGUOUS_LOCAL_TIME
    assert result.is_regular_session is False


@pytest.mark.parametrize("day", SPRING_FORWARD + FALL_BACK, ids=str)
def test_dst_ambiguity_never_reaches_a_trading_session(day):
    """The ambiguous windows are overnight, so refusing them costs nothing."""
    for hour in (4, 9, 10, 15, 19):
        moment = datetime.combine(day, time(hour, 0), tzinfo=EASTERN)
        assert classify(moment).reason is not ClassificationReason.AMBIGUOUS_LOCAL_TIME


def test_the_monday_after_a_transition_trades_normally():
    for day in SPRING_FORWARD + FALL_BACK:
        monday = day + timedelta(days=1)
        result = classify(datetime.combine(monday, time(10, 0), tzinfo=EASTERN))
        assert result.is_regular_session is True, monday


def test_a_utc_instant_converts_correctly_across_the_offset_change():
    from datetime import timezone

    # 14:30 UTC is 09:30 ET in winter (UTC-5) and 10:30 ET in summer (UTC-4).
    winter = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 6, 14, 30, tzinfo=timezone.utc)
    assert classify(winter).is_regular_session is True
    assert classify(summer).is_regular_session is True
    assert classify(datetime(2026, 1, 5, 14, 29, tzinfo=timezone.utc)).session is (
        CalendarSession.PRE_MARKET
    )


# --------------------------------------------------------------------------
# 7. Outside the supported range.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        et(2025, 8, 4, 10, 0),
        et(2025, 12, 31, 10, 0),
        et(2028, 1, 3, 10, 0),
        et(2028, 8, 7, 10, 0),
        et(2030, 6, 3, 10, 0),
    ],
)
def test_dates_outside_the_supported_range_are_unknown(moment):
    result = classify(moment)
    assert result.session is CalendarSession.UNKNOWN
    assert result.day_kind is DayKind.UNSUPPORTED
    assert result.reason is ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE
    assert result.is_regular_session is False


def test_the_range_edges_are_inclusive_and_the_next_day_is_not():
    first, last = supported_range()
    assert (first, last) == (date(2026, 1, 1), date(2027, 12, 31))
    # 1 January 2026 is itself a holiday, so coverage means "classified", not "open".
    assert classify(et(2026, 1, 1, 11, 0)).reason is ClassificationReason.FULL_HOLIDAY
    assert classify(et(2027, 12, 31, 11, 0)).is_regular_session is True
    assert classify(et(2028, 1, 1, 11, 0)).reason is (
        ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE
    )


def test_there_is_no_2028_coverage():
    assert 2028 not in SCHEDULE_METADATA.supported_years
    assert not any(day.year == 2028 for day in FULL_HOLIDAYS)
    assert not any(day.year == 2028 for day in EARLY_CLOSE_DAYS)


# --------------------------------------------------------------------------
# 8. Expiry and maintenance.
# --------------------------------------------------------------------------


def test_expiry_tracks_coverage():
    assert schedule_is_expired(date(2027, 12, 31)) is False
    assert schedule_is_expired(date(2028, 1, 1)) is True


def test_review_falls_due_before_coverage_lapses():
    """The distinction that makes the warning actionable."""
    assert SCHEDULE_METADATA.review_due < SCHEDULE_METADATA.last_supported_date
    assert SCHEDULE_METADATA.review_due >= SCHEDULE_METADATA.verified_on


def test_review_can_be_overdue_while_the_calendar_still_works():
    overdue_day = date(2027, 7, 1)
    assert schedule_review_is_overdue(overdue_day) is True
    assert schedule_is_expired(overdue_day) is False
    assert classify(et(2027, 7, 1, 11, 0), today=overdue_day).is_regular_session is True


def test_review_is_not_overdue_before_its_date():
    assert schedule_review_is_overdue(date(2026, 8, 1)) is False


def test_an_expired_schedule_refuses_even_a_covered_date():
    """Expiry is about the schedule, not the query."""
    result = classify(et(2026, 8, 3, 10, 0), today=date(2028, 6, 1))
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is ClassificationReason.SCHEDULE_EXPIRED
    assert result.is_regular_session is False


def test_without_a_supplied_today_expiry_is_not_evaluated():
    assert classify(et(2026, 8, 3, 10, 0)).is_regular_session is True


# --------------------------------------------------------------------------
# 9. Metadata.
# --------------------------------------------------------------------------


def test_metadata_values():
    meta = schedule_metadata()
    assert meta is SCHEDULE_METADATA
    assert meta.source == "NYSE published holiday and early-close schedule"
    assert meta.version == "2026.1"
    assert meta.supported_years == (2026, 2027)
    assert meta.first_supported_date == date(2026, 1, 1)
    assert meta.last_supported_date == date(2027, 12, 31)
    assert meta.verified_on == date(2026, 8, 1)
    assert "PR #44" in meta.verified_by
    assert meta.review_due == date(2027, 6, 30)


def test_metadata_names_a_citable_source_not_a_feed():
    assert "NYSE" in SCHEDULE_METADATA.source
    assert SCHEDULE_METADATA.verified_by.strip() != ""


# --------------------------------------------------------------------------
# 10. Schedule validation.
# --------------------------------------------------------------------------


def base_metadata(**overrides) -> ScheduleMetadata:
    values = {
        "source": "test",
        "version": "0",
        "supported_years": (2026, 2027),
        "first_supported_date": date(2026, 1, 1),
        "last_supported_date": date(2027, 12, 31),
        "verified_on": date(2026, 8, 1),
        "verified_by": "test",
        "review_due": date(2027, 6, 30),
    }
    values.update(overrides)
    return ScheduleMetadata(**values)


def test_the_shipped_schedule_validates():
    validate_schedule()


def test_an_inverted_range_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(
                first_supported_date=date(2027, 1, 1),
                last_supported_date=date(2026, 1, 1),
                supported_years=(2026, 2027),
            ),
            holidays=frozenset(),
            early_closes={},
        )


def test_a_review_due_before_verification_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(review_due=date(2026, 1, 1)),
            holidays=frozenset(),
            early_closes={},
        )


def test_a_review_due_after_coverage_lapses_is_refused():
    """A warning that arrives after the calendar goes blind is not a warning."""
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(review_due=date(2028, 1, 1)),
            holidays=frozenset(),
            early_closes={},
        )


def test_supported_years_disagreeing_with_the_range_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(supported_years=(2026,)),
            holidays=frozenset(),
            early_closes={},
        )


def test_no_supported_years_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(supported_years=()),
            holidays=frozenset(),
            early_closes={},
        )


def test_a_holiday_outside_the_range_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset({date(2025, 12, 25)}),
            early_closes={},
        )


def test_a_holiday_on_a_weekend_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset({date(2026, 8, 1)}),
            early_closes={},
        )


def test_an_early_close_on_a_weekend_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset(),
            early_closes={date(2026, 8, 1): time(13, 0)},
        )


def test_a_date_that_is_both_holiday_and_early_close_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset({date(2026, 11, 27)}),
            early_closes={date(2026, 11, 27): time(13, 0)},
        )


@pytest.mark.parametrize("closing", [time(9, 30), time(16, 0), time(17, 0), time(9, 0)])
def test_an_early_close_outside_the_regular_session_is_refused(closing):
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset(),
            early_closes={date(2026, 11, 27): closing},
        )


def test_a_non_time_early_close_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset(),
            early_closes={date(2026, 11, 27): "13:00"},
        )


def test_a_datetime_masquerading_as_a_schedule_date_is_refused():
    with pytest.raises(ExchangeCalendarError):
        validate_schedule(
            metadata=base_metadata(),
            holidays=frozenset({datetime(2026, 11, 27, 12, 0)}),
            early_closes={},
        )


# --------------------------------------------------------------------------
# 11. Malformed and unusable inputs.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, "2026-08-03T10:00:00", 1754222400, 3.14, date(2026, 8, 3), [], {}, object()],
)
def test_a_non_datetime_input_is_refused(value):
    result = classify(value)
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is ClassificationReason.NOT_A_DATETIME
    assert result.is_regular_session is False
    assert is_regular_session(value) is False


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 3, 10, 0),
        datetime(2026, 8, 3, 0, 0),
        datetime(2027, 3, 1, 9, 30),
    ],
)
def test_a_naive_timestamp_is_refused(value):
    """Naive is ambiguous by exactly the amount that decides openness."""
    result = classify(value)
    assert result.session is CalendarSession.UNKNOWN
    assert result.reason is ClassificationReason.NAIVE_TIMESTAMP
    assert result.is_regular_session is False


def test_a_date_is_not_silently_promoted_to_a_datetime():
    assert classify(date(2026, 8, 3)).reason is ClassificationReason.NOT_A_DATETIME


# --------------------------------------------------------------------------
# 12. Fail-closed invariants.
# --------------------------------------------------------------------------


def test_the_result_object_refuses_to_claim_a_session_it_did_not_find():
    with pytest.raises(ExchangeCalendarError):
        SessionClassification(
            CalendarSession.CLOSED, DayKind.WEEKEND, ClassificationReason.WEEKEND, True
        )
    with pytest.raises(ExchangeCalendarError):
        SessionClassification(
            CalendarSession.UNKNOWN,
            DayKind.UNSUPPORTED,
            ClassificationReason.NAIVE_TIMESTAMP,
            True,
        )
    with pytest.raises(ExchangeCalendarError):
        SessionClassification(
            CalendarSession.REGULAR,
            DayKind.TRADING_DAY,
            ClassificationReason.AFTER_EARLY_CLOSE,
            True,
        )


def test_no_unknown_reason_can_ever_be_tradeable():
    for reason in UNKNOWN_REASONS:
        with pytest.raises(ExchangeCalendarError):
            SessionClassification(
                CalendarSession.UNKNOWN, DayKind.UNSUPPORTED, reason, True
            )


def test_no_closed_day_in_range_ever_reports_a_regular_session():
    """Sweep every covered day at a time that would be mid-session."""
    day = SCHEDULE_METADATA.first_supported_date
    open_days = closed_days = 0
    while day <= SCHEDULE_METADATA.last_supported_date:
        result = classify(datetime.combine(day, time(14, 0), tzinfo=EASTERN))
        shut = (
            day.weekday() >= 5 or day in FULL_HOLIDAYS or day in EARLY_CLOSE_DAYS
        )
        if shut:
            assert result.is_regular_session is False, day
            assert result.session is CalendarSession.CLOSED, day
            closed_days += 1
        else:
            assert result.is_regular_session is True, day
            open_days += 1
        day += timedelta(days=1)
    assert open_days > 400
    assert closed_days > 200


def test_every_covered_minute_of_a_holiday_is_closed():
    holiday = date(2026, 12, 25)
    for hour in range(24):
        for minute in (0, 30):
            moment = datetime.combine(holiday, time(hour, minute), tzinfo=EASTERN)
            assert classify(moment).is_regular_session is False


def test_is_regular_session_agrees_with_classify_everywhere():
    day = date(2026, 11, 23)
    for offset in range(0, 7):
        for hour in (3, 8, 10, 13, 15, 17, 21):
            moment = datetime.combine(
                day + timedelta(days=offset), time(hour, 0), tzinfo=EASTERN
            )
            assert is_regular_session(moment) == classify(moment).is_regular_session


# --------------------------------------------------------------------------
# 13. Independence and offline guarantees.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_calendar_imports_nothing_from_the_pinned_checkpoint_files():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_the_calendar_imports_nothing_broker_shaped():
    imported = _imports(MODULE_PATH)
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator",
                 "commissioning", "manifest", "checkpoint", "strategy")
    for name in imported:
        assert not any(word in name.lower() for word in forbidden), name


def test_the_calendar_makes_no_network_call():
    imported = _imports(MODULE_PATH)
    network = {"socket", "http", "urllib", "requests", "httpx", "aiohttp",
               "ftplib", "telnetlib", "asyncio", "subprocess"}
    assert imported.isdisjoint(network)
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "urlopen" not in source
    assert "http://" not in source


def test_the_calendar_performs_no_io_at_all():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    forbidden_calls = {"open", "read_text", "read_bytes", "write_text", "urlopen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in forbidden_calls, name


def test_the_calendar_does_not_read_the_wall_clock_implicitly():
    """`today` must be supplied; a module that reads `now()` is untestable."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "date.today(" not in source


def test_the_calendar_agrees_with_the_legacy_runtime_table():
    """TEMPORARY drift guard for the pre-integration period. Expected to die.

    `src/read_only_observation_runtime.py` keeps its own private copy of the
    holiday and early-close tables, because it is one of the five files in the
    checkpoint implementation digest and cannot be edited while the
    real-account checkpoint is held at its operator gate. Until delegation
    happens, two copies of the same data exist and must not diverge — a
    divergence would mean the eventual switch silently changed behaviour.

    **This test must be removed or rewritten in the same re-pin window that
    makes `classify_market_session` delegate to `src/exchange_calendar.py`.**
    It deliberately imports private names (`_MARKET_HOLIDAYS`,
    `_EARLY_CLOSE_DAYS`) from that module, and delegation will delete them.

    **An `ImportError` here at that point is the expected, designed outcome —
    not an unexplained regression.** Do not "fix" it by re-adding the private
    names to the runtime; that would recreate the duplication this guard
    exists to retire. Delete this test instead, and rely on
    `test_the_shipped_holidays_match_the_independent_expected_set_exactly`,
    which is independent of both modules and outlives the migration.
    """
    from src.read_only_observation_runtime import (
        _EARLY_CLOSE_DAYS as runtime_early,
        _MARKET_HOLIDAYS as runtime_holidays,
    )

    assert runtime_holidays == FULL_HOLIDAYS
    assert set(runtime_early) == set(EARLY_CLOSE_DAYS)
