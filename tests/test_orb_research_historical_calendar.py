"""Tests for the RESEARCH-ONLY historical calendar
(`src/orb_research_historical_calendar.py`), added for the out-of-sample
validation study (Issue #304).

Two concerns: (1) the calendar itself answers correctly and fails closed
outside its stated coverage, and (2) -- the more important guarantee --
this module never becomes reachable from production runtime or the
digest-pinned checkpoint implementation closure, so its existence cannot
change `checkpoint_implementation_digest` or any manifest built against it.
"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from src.exchange_calendar import EARLY_REGULAR_CLOSE, REGULAR_CLOSE, REGULAR_OPEN, regular_session_bounds
from src.orb_research_historical_calendar import (
    RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE,
    ResearchCalendarError,
    research_regular_session_bounds,
    research_trading_sessions_before,
)
from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MODULE_NAME = "orb_research_historical_calendar"


def test_module_absent_from_pinned_implementation_closure():
    assert not any(MODULE_NAME in entry for entry in PINNED_IMPLEMENTATION_CLOSURE)


def test_no_pinned_closure_file_imports_the_research_calendar():
    for relative_path in PINNED_IMPLEMENTATION_CLOSURE:
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert MODULE_NAME not in source, f"{relative_path} must never reference {MODULE_NAME}"


def test_production_exchange_calendar_does_not_import_research_calendar():
    source = (REPOSITORY_ROOT / "src" / "exchange_calendar.py").read_text(encoding="utf-8")
    assert MODULE_NAME not in source


def test_checkpoint_launcher_does_not_import_research_calendar():
    launcher = REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py"
    if launcher.exists():
        assert MODULE_NAME not in launcher.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "day",
    [
        date(2025, 1, 1),   # New Year's Day
        date(2025, 1, 20),  # MLK Day
        date(2025, 2, 17),  # Washington's Birthday
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial Day
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),   # Independence Day
        date(2025, 9, 1),   # Labor Day
        date(2025, 11, 27), # Thanksgiving
        date(2025, 12, 25), # Christmas
    ],
)
def test_2025_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


@pytest.mark.parametrize("day", [date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)])
def test_2025_early_closes_end_at_1pm(day):
    bounds = research_regular_session_bounds(day)
    assert bounds == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_ordinary_2025_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2025, 4, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_2025_weekend_is_closed():
    assert research_regular_session_bounds(date(2025, 4, 5)) is None  # Saturday


@pytest.mark.parametrize(
    "day",
    [
        date(2024, 1, 1),   # New Year's Day
        date(2024, 1, 15),  # MLK Day
        date(2024, 2, 19),  # Washington's Birthday
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),  # Memorial Day
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),   # Independence Day
        date(2024, 9, 2),   # Labor Day
        date(2024, 11, 28), # Thanksgiving
        date(2024, 12, 25), # Christmas
    ],
)
def test_2024_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


@pytest.mark.parametrize("day", [date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)])
def test_2024_early_closes_end_at_1pm(day):
    bounds = research_regular_session_bounds(day)
    assert bounds == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_ordinary_2024_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2024, 4, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_2024_weekend_is_closed():
    assert research_regular_session_bounds(date(2024, 4, 6)) is None  # Saturday


@pytest.mark.parametrize(
    "day",
    [
        date(2023, 1, 2),   # New Year's Day observed (Jan 1 was a Sunday)
        date(2023, 1, 16),  # MLK Day
        date(2023, 2, 20),  # Washington's Birthday
        date(2023, 4, 7),   # Good Friday
        date(2023, 5, 29),  # Memorial Day
        date(2023, 6, 19),  # Juneteenth
        date(2023, 7, 4),   # Independence Day
        date(2023, 9, 4),   # Labor Day
        date(2023, 11, 23), # Thanksgiving
        date(2023, 12, 25), # Christmas
    ],
)
def test_2023_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


@pytest.mark.parametrize("day", [date(2023, 7, 3), date(2023, 11, 24)])
def test_2023_early_closes_end_at_1pm(day):
    bounds = research_regular_session_bounds(day)
    assert bounds == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2023_has_no_christmas_eve_early_close():
    # Christmas Eve 2023 (Dec 24) was a Sunday -- unlike 2024/2025, there is
    # no separate Christmas-adjacent early-close entry that year.
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2023

    assert date(2023, 12, 24) not in RESEARCH_EARLY_CLOSE_DAYS_2023
    assert len(RESEARCH_EARLY_CLOSE_DAYS_2023) == 2


def test_ordinary_2023_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2023, 4, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_2023_weekend_is_closed():
    assert research_regular_session_bounds(date(2023, 4, 1)) is None  # Saturday


@pytest.mark.parametrize(
    "day",
    [
        date(2021, 1, 1),   # New Year's Day
        date(2021, 1, 18),  # Martin Luther King Jr. Day
        date(2021, 2, 15),  # Washington's Birthday
        date(2021, 4, 2),   # Good Friday
        date(2021, 5, 31),  # Memorial Day
        date(2021, 7, 5),   # Independence Day observed (Jul 4 was a Sunday)
        date(2021, 9, 6),   # Labor Day
        date(2021, 11, 25), # Thanksgiving
        date(2021, 12, 24), # Christmas observed (Dec 25 was a Saturday)
    ],
)
def test_2021_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


def test_2021_12_31_is_a_regular_trading_session_not_new_years_2022_observed():
    # Issue #334: an earlier version of this table incorrectly treated
    # 2021-12-31 as a closure ("New Year's Day 2022 observed", since
    # 2022-01-01 fell on a Saturday). NYSE historical market-data evidence
    # shows real trading activity on 2021-12-31, and
    # src.exchange_calendar.FULL_HOLIDAYS's own documented rule confirms
    # New Year's Day is the one holiday NOT observed on the preceding
    # Friday -- unlike every other Saturday holiday in this file.
    assert research_regular_session_bounds(date(2021, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_2021_early_close_ends_at_1pm():
    bounds = research_regular_session_bounds(date(2021, 11, 26))  # day after Thanksgiving
    assert bounds == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2021_has_no_july3_or_christmas_eve_early_close():
    # Independence Day 2021 fell on a Sunday (observed the following
    # Monday, 2021-07-05), so unlike years where July 4 falls midweek
    # there is no separate July 3 early close; Christmas Eve (2021-12-24)
    # is itself the observed Christmas Day full holiday (Dec 25 fell on a
    # Saturday), not a separate half day. Only the day-after-Thanksgiving
    # half-day exists for 2021.
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2021

    assert date(2021, 7, 2) not in RESEARCH_EARLY_CLOSE_DAYS_2021
    assert date(2021, 12, 24) not in RESEARCH_EARLY_CLOSE_DAYS_2021
    assert len(RESEARCH_EARLY_CLOSE_DAYS_2021) == 1


def test_2021_has_no_juneteenth_holiday():
    # 2021 predates NYSE's first Juneteenth observance (2022).
    from src.orb_research_historical_calendar import RESEARCH_FULL_HOLIDAYS_2021

    assert not any(day.month == 6 for day in RESEARCH_FULL_HOLIDAYS_2021)


def test_ordinary_2021_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2021, 4, 5)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Monday


def test_2021_weekend_is_closed():
    assert research_regular_session_bounds(date(2021, 4, 3)) is None  # Saturday


def test_year_end_boundary_between_2021_and_2022_tables_is_seamless():
    assert research_regular_session_bounds(date(2021, 12, 30)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Thu, ordinary
    assert research_regular_session_bounds(date(2021, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Fri, ordinary (New Year's Day is the standing exception)
    assert research_regular_session_bounds(date(2022, 1, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Mon, ordinary


def test_2021_holiday_table_is_disjoint_from_2022_2023_2024_2025():
    from src.orb_research_historical_calendar import (
        RESEARCH_FULL_HOLIDAYS_2021,
        RESEARCH_FULL_HOLIDAYS_2022,
        RESEARCH_FULL_HOLIDAYS_2023,
        RESEARCH_FULL_HOLIDAYS_2024,
        RESEARCH_FULL_HOLIDAYS_2025,
    )

    assert not (RESEARCH_FULL_HOLIDAYS_2021 & RESEARCH_FULL_HOLIDAYS_2022)
    assert not (RESEARCH_FULL_HOLIDAYS_2021 & RESEARCH_FULL_HOLIDAYS_2023)
    assert not (RESEARCH_FULL_HOLIDAYS_2021 & RESEARCH_FULL_HOLIDAYS_2024)
    assert not (RESEARCH_FULL_HOLIDAYS_2021 & RESEARCH_FULL_HOLIDAYS_2025)


def test_p_minus_2_is_exactly_252_sessions_ending_immediately_before_p_minus_1_start():
    # Local milestone M42: P_MINUS_2 must be exactly 252 sessions strictly
    # before P_MINUS_1's own start date (2022-03-30) -- the reason this
    # module's floor was extended from 2022-01-01 to 2021-01-01. Start
    # date corrected by Issue #334 (2021-12-31 is a regular trading
    # session, not a closure, so the 252-session count now reaches back
    # one session less far -- 2021-03-31, not 2021-03-30).
    p_minus_2_sessions = research_trading_sessions_before(date(2022, 3, 30), count=252)
    assert len(p_minus_2_sessions) == 252
    assert p_minus_2_sessions[0] == date(2021, 3, 31)
    assert p_minus_2_sessions[-1] == date(2022, 3, 29)
    assert p_minus_2_sessions[-1] < date(2022, 3, 30)


def test_p_minus_2_does_not_overlap_p_minus_1_p0_p1_p2_or_oos_sessions():
    p_minus_2_sessions = set(research_trading_sessions_before(date(2022, 3, 30), count=252))
    p_minus_1_sessions = set(research_trading_sessions_before(date(2023, 3, 31), count=252))
    p0_sessions = set(research_trading_sessions_before(date(2024, 4, 3), count=252))
    third_period_sessions = set(research_trading_sessions_before(date(2025, 4, 3), count=252))
    oos_sessions = set(research_trading_sessions_before(date(2026, 4, 7), count=252))

    assert not (p_minus_2_sessions & p_minus_1_sessions)
    assert not (p_minus_2_sessions & p0_sessions)
    assert not (p_minus_2_sessions & third_period_sessions)
    assert not (p_minus_2_sessions & oos_sessions)

    from src.orb_universe_expansion_research import trading_sessions_before

    discovery_sessions = set(trading_sessions_before(date(2026, 8, 14), count=90))
    assert not (p_minus_2_sessions & discovery_sessions)


@pytest.mark.parametrize(
    "day",
    [
        date(2022, 1, 17),  # Martin Luther King Jr. Day
        date(2022, 2, 21),  # Washington's Birthday
        date(2022, 4, 15),  # Good Friday
        date(2022, 5, 30),  # Memorial Day
        date(2022, 6, 20),  # Juneteenth observed (Jun 19 was a Sunday)
        date(2022, 7, 4),   # Independence Day
        date(2022, 9, 5),   # Labor Day
        date(2022, 11, 24), # Thanksgiving
        date(2022, 12, 26), # Christmas observed (Dec 25 was a Sunday)
    ],
)
def test_2022_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


def test_2022_early_close_ends_at_1pm():
    bounds = research_regular_session_bounds(date(2022, 11, 25))  # day after Thanksgiving
    assert bounds == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2022_has_no_july3_or_christmas_eve_early_close():
    # July 4, 2022 was itself a Monday (the holiday), so unlike other years
    # there is no separate July 3 early close; Christmas Eve (2022-12-24)
    # fell on a Saturday, so there is no Christmas Eve early-close trading
    # day either. Only the day-after-Thanksgiving half-day exists for 2022.
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2022

    assert date(2022, 7, 3) not in RESEARCH_EARLY_CLOSE_DAYS_2022
    assert date(2022, 12, 24) not in RESEARCH_EARLY_CLOSE_DAYS_2022
    assert len(RESEARCH_EARLY_CLOSE_DAYS_2022) == 1


def test_2022_has_no_restated_new_years_day():
    # 2022-01-01 fell on a Saturday; NYSE's observance fell on 2021-12-31,
    # a date outside this module's coverage floor and never needed by any
    # study using it -- so 2022's own holiday table has no New Year's Day
    # entry (unlike 2023/2024/2025, where Jan 1 or its observance falls
    # inside the covered year).
    from src.orb_research_historical_calendar import RESEARCH_FULL_HOLIDAYS_2022

    assert len(RESEARCH_FULL_HOLIDAYS_2022) == 9
    assert not any(day.month == 1 and day.day <= 3 for day in RESEARCH_FULL_HOLIDAYS_2022)


def test_ordinary_2022_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2022, 4, 4)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Monday


def test_2022_weekend_is_closed():
    assert research_regular_session_bounds(date(2022, 4, 2)) is None  # Saturday


def test_year_end_boundary_between_2022_and_2023_tables_is_seamless():
    assert research_regular_session_bounds(date(2022, 12, 30)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Fri, ordinary
    assert research_regular_session_bounds(date(2023, 1, 2)) is None  # New Year's Day observed (Mon)
    assert research_regular_session_bounds(date(2023, 1, 3)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Tue, ordinary


def test_2022_holiday_table_is_disjoint_from_2023_2024_2025():
    from src.orb_research_historical_calendar import (
        RESEARCH_FULL_HOLIDAYS_2022,
        RESEARCH_FULL_HOLIDAYS_2023,
        RESEARCH_FULL_HOLIDAYS_2024,
        RESEARCH_FULL_HOLIDAYS_2025,
    )

    assert not (RESEARCH_FULL_HOLIDAYS_2022 & RESEARCH_FULL_HOLIDAYS_2023)
    assert not (RESEARCH_FULL_HOLIDAYS_2022 & RESEARCH_FULL_HOLIDAYS_2024)
    assert not (RESEARCH_FULL_HOLIDAYS_2022 & RESEARCH_FULL_HOLIDAYS_2025)


def test_p_minus_1_is_exactly_252_sessions_ending_immediately_before_p0_start():
    # Local milestone M40: P_MINUS_1 must be exactly 252 sessions strictly
    # before P0's own start date (2023-03-31) -- the reason this module's
    # floor was extended from 2023-01-01 to 2022-01-01.
    p_minus_1_sessions = research_trading_sessions_before(date(2023, 3, 31), count=252)
    assert len(p_minus_1_sessions) == 252
    assert p_minus_1_sessions[0] == date(2022, 3, 30)
    assert p_minus_1_sessions[-1] == date(2023, 3, 30)
    assert p_minus_1_sessions[-1] < date(2023, 3, 31)


def test_p_minus_1_does_not_overlap_p0_p1_p2_or_oos_sessions():
    p_minus_1_sessions = set(research_trading_sessions_before(date(2023, 3, 31), count=252))
    p0_sessions = set(research_trading_sessions_before(date(2024, 4, 3), count=252))
    third_period_sessions = set(research_trading_sessions_before(date(2025, 4, 3), count=252))
    oos_sessions = set(research_trading_sessions_before(date(2026, 4, 7), count=252))

    assert not (p_minus_1_sessions & p0_sessions)
    assert not (p_minus_1_sessions & third_period_sessions)
    assert not (p_minus_1_sessions & oos_sessions)

    from src.orb_universe_expansion_research import trading_sessions_before

    discovery_sessions = set(trading_sessions_before(date(2026, 8, 14), count=90))
    assert not (p_minus_1_sessions & discovery_sessions)


def test_2023_2024_2025_holiday_tables_are_pairwise_disjoint():
    from src.orb_research_historical_calendar import (
        RESEARCH_FULL_HOLIDAYS_2023,
        RESEARCH_FULL_HOLIDAYS_2024,
        RESEARCH_FULL_HOLIDAYS_2025,
    )

    assert not (RESEARCH_FULL_HOLIDAYS_2023 & RESEARCH_FULL_HOLIDAYS_2024)
    assert not (RESEARCH_FULL_HOLIDAYS_2024 & RESEARCH_FULL_HOLIDAYS_2025)
    assert not (RESEARCH_FULL_HOLIDAYS_2023 & RESEARCH_FULL_HOLIDAYS_2025)


def test_year_end_boundary_between_2023_and_2024_tables_is_seamless():
    assert research_regular_session_bounds(date(2023, 12, 29)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Fri, ordinary
    assert research_regular_session_bounds(date(2024, 1, 1)) is None  # New Year's Day
    assert research_regular_session_bounds(date(2024, 1, 2)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Tue, ordinary


def test_year_end_boundary_between_2024_and_2025_tables_is_seamless():
    # 2024-12-31 (Tue, ordinary trading day) and 2025-01-02 (Thu, ordinary
    # trading day) must both resolve correctly across the internal 2024/2025
    # table split, with 2025-01-01 (New Year's Day) closed in between.
    assert research_regular_session_bounds(date(2024, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)
    assert research_regular_session_bounds(date(2025, 1, 1)) is None
    assert research_regular_session_bounds(date(2025, 1, 2)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_dates_inside_production_coverage_delegate_unchanged():
    for day in (date(2026, 1, 2), date(2026, 4, 6), date(2026, 11, 27)):
        assert research_regular_session_bounds(day) == regular_session_bounds(day)


def test_date_before_research_coverage_floor_raises():
    with pytest.raises(ResearchCalendarError):
        research_regular_session_bounds(date(2017, 12, 31))


def test_datetime_input_raises_instead_of_silently_truncating():
    with pytest.raises(ResearchCalendarError):
        research_regular_session_bounds(datetime(2025, 4, 3, 9, 30))  # type: ignore[arg-type]


def test_research_calendar_first_supported_date_is_2018_01_01():
    assert RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE == date(2018, 1, 1)


def test_trading_sessions_before_returns_oldest_first_and_excludes_reference_day():
    sessions = research_trading_sessions_before(date(2025, 1, 10), count=5)
    assert len(sessions) == 5
    assert sessions == tuple(sorted(sessions))
    assert date(2025, 1, 10) not in sessions
    assert all(day < date(2025, 1, 10) for day in sessions)


def test_trading_sessions_before_252_ending_before_discovery_period_has_no_overlap():
    oos_sessions = research_trading_sessions_before(date(2026, 4, 7), count=252)
    assert len(oos_sessions) == 252
    assert oos_sessions[-1] < date(2026, 4, 7)
    from src.orb_universe_expansion_research import trading_sessions_before

    discovery_sessions = set(trading_sessions_before(date(2026, 8, 14), count=90))
    assert not (set(oos_sessions) & discovery_sessions)


def test_trading_sessions_before_beyond_2018_coverage_fails_closed():
    # 2078 is the first count that walks back past the 2018-01-01 floor
    # from 2026-04-07 (verified directly against this module); 2100 is
    # comfortably beyond that.
    with pytest.raises(ResearchCalendarError):
        research_trading_sessions_before(date(2026, 4, 7), count=2100)


def test_third_period_252_ending_before_oos_period_has_no_overlap():
    # The third-period validation study (Issue TBD) window: 252 sessions
    # ending immediately before the OOS study's own first session
    # (2025-04-03), reaching back into 2024 -- the reason this module's
    # floor was extended from 2025-01-01 to 2024-01-01.
    third_period_sessions = research_trading_sessions_before(date(2025, 4, 3), count=252)
    assert len(third_period_sessions) == 252
    assert third_period_sessions[-1] < date(2025, 4, 3)

    oos_sessions = set(research_trading_sessions_before(date(2026, 4, 7), count=252))
    assert not (set(third_period_sessions) & oos_sessions)

    from src.orb_universe_expansion_research import trading_sessions_before

    discovery_sessions = set(trading_sessions_before(date(2026, 8, 14), count=90))
    assert not (set(third_period_sessions) & discovery_sessions)


def test_trading_sessions_before_rejects_non_positive_count():
    with pytest.raises(ResearchCalendarError):
        research_trading_sessions_before(date(2025, 6, 1), count=0)


def test_fourth_period_p0_252_ending_before_third_period_has_no_overlap():
    # The fourth-period baseline validation study (Issue #321) window: 252
    # sessions ending immediately before the third-period study's own first
    # session (2024-04-03), reaching back into 2023 -- the reason this
    # module's floor was extended from 2024-01-01 to 2023-01-01.
    p0_sessions = research_trading_sessions_before(date(2024, 4, 3), count=252)
    assert len(p0_sessions) == 252
    assert p0_sessions[-1] < date(2024, 4, 3)

    third_period_sessions = set(research_trading_sessions_before(date(2025, 4, 3), count=252))
    oos_sessions = set(research_trading_sessions_before(date(2026, 4, 7), count=252))
    assert not (set(p0_sessions) & third_period_sessions)
    assert not (set(p0_sessions) & oos_sessions)

    from src.orb_universe_expansion_research import trading_sessions_before

    discovery_sessions = set(trading_sessions_before(date(2026, 8, 14), count=90))
    assert not (set(p0_sessions) & discovery_sessions)


# ---------------------------------------------------------------------------
# 2018/2019/2020 (M50, Issue #352): the floor was extended from 2021-01-01
# to 2018-01-01 for the untouched-portfolio-validation study's own frozen
# outcome window (2019-01-02..2020-12-31) and warmup target (2018-01-02).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day", [
    date(2018, 1, 1), date(2018, 1, 15), date(2018, 2, 19), date(2018, 3, 30),
    date(2018, 5, 28), date(2018, 7, 4), date(2018, 9, 3), date(2018, 11, 22), date(2018, 12, 25),
])
def test_2018_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


def test_2018_early_closes_are_correct():
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2018

    assert set(RESEARCH_EARLY_CLOSE_DAYS_2018) == {date(2018, 7, 3), date(2018, 11, 23), date(2018, 12, 24)}
    for day in RESEARCH_EARLY_CLOSE_DAYS_2018:
        assert research_regular_session_bounds(day) == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2018_has_no_juneteenth():
    assert research_regular_session_bounds(date(2018, 6, 19)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_ordinary_2018_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2018, 4, 4)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Wednesday


def test_2018_weekend_is_closed():
    assert research_regular_session_bounds(date(2018, 4, 7)) is None  # Saturday


@pytest.mark.parametrize("day", [
    date(2019, 1, 1), date(2019, 1, 21), date(2019, 2, 18), date(2019, 4, 19),
    date(2019, 5, 27), date(2019, 7, 4), date(2019, 9, 2), date(2019, 11, 28), date(2019, 12, 25),
])
def test_2019_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


def test_2019_early_closes_are_correct():
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2019

    assert set(RESEARCH_EARLY_CLOSE_DAYS_2019) == {date(2019, 7, 3), date(2019, 11, 29), date(2019, 12, 24)}
    for day in RESEARCH_EARLY_CLOSE_DAYS_2019:
        assert research_regular_session_bounds(day) == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2019_has_no_juneteenth():
    assert research_regular_session_bounds(date(2019, 6, 19)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_ordinary_2019_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2019, 3, 4)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Monday


def test_2019_weekend_is_closed():
    assert research_regular_session_bounds(date(2019, 3, 3)) is None  # Sunday


@pytest.mark.parametrize("day", [
    date(2020, 1, 1), date(2020, 1, 20), date(2020, 2, 17), date(2020, 4, 10),
    date(2020, 5, 25), date(2020, 7, 3), date(2020, 9, 7), date(2020, 11, 26), date(2020, 12, 25),
])
def test_2020_full_holidays_are_closed(day):
    assert research_regular_session_bounds(day) is None


def test_2020_independence_day_observance_is_a_full_holiday_not_an_early_close():
    # 2020-07-04 fell on a Saturday; NYSE observed it as a FULL closure on
    # the preceding Friday (2020-07-03), unlike 2018/2019 where July 3 was
    # merely a half-day. Confirmed directly against the source press
    # release, not assumed from the general Saturday-holiday rule.
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2020, RESEARCH_FULL_HOLIDAYS_2020

    assert date(2020, 7, 3) in RESEARCH_FULL_HOLIDAYS_2020
    assert date(2020, 7, 3) not in RESEARCH_EARLY_CLOSE_DAYS_2020
    assert research_regular_session_bounds(date(2020, 7, 3)) is None


def test_2020_early_closes_are_correct():
    from src.orb_research_historical_calendar import RESEARCH_EARLY_CLOSE_DAYS_2020

    assert set(RESEARCH_EARLY_CLOSE_DAYS_2020) == {date(2020, 11, 27), date(2020, 12, 24)}
    for day in RESEARCH_EARLY_CLOSE_DAYS_2020:
        assert research_regular_session_bounds(day) == (REGULAR_OPEN, EARLY_REGULAR_CLOSE)


def test_2020_has_no_juneteenth():
    assert research_regular_session_bounds(date(2020, 6, 19)) == (REGULAR_OPEN, REGULAR_CLOSE)


def test_ordinary_2020_trading_day_uses_regular_hours():
    assert research_regular_session_bounds(date(2020, 3, 2)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Monday


def test_2020_weekend_is_closed():
    assert research_regular_session_bounds(date(2020, 3, 1)) is None  # Sunday


def test_year_end_boundary_between_2018_and_2019_tables_is_seamless():
    assert research_regular_session_bounds(date(2018, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Mon, ordinary
    assert research_regular_session_bounds(date(2019, 1, 1)) is None  # New Year's Day (Tue)
    assert research_regular_session_bounds(date(2019, 1, 2)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Wed, ordinary -- M50's own 2019 effective/outcome-start date


def test_year_end_boundary_between_2019_and_2020_tables_is_seamless():
    assert research_regular_session_bounds(date(2019, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Tue, ordinary
    assert research_regular_session_bounds(date(2020, 1, 1)) is None  # New Year's Day (Wed)
    assert research_regular_session_bounds(date(2020, 1, 2)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Thu, ordinary -- M50's own 2020 effective-date boundary


def test_year_end_boundary_between_2020_and_2021_tables_is_seamless():
    assert research_regular_session_bounds(date(2020, 12, 31)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Thu, ordinary
    assert research_regular_session_bounds(date(2021, 1, 1)) is None  # New Year's Day (Fri) -- an ordinary weekday holiday, not the Saturday-shift special case
    assert research_regular_session_bounds(date(2021, 1, 4)) == (REGULAR_OPEN, REGULAR_CLOSE)  # Mon, ordinary


def test_2018_2019_2020_holiday_tables_are_pairwise_disjoint_from_each_other_and_from_2021_2025():
    from src.orb_research_historical_calendar import (
        RESEARCH_FULL_HOLIDAYS_2018,
        RESEARCH_FULL_HOLIDAYS_2019,
        RESEARCH_FULL_HOLIDAYS_2020,
        RESEARCH_FULL_HOLIDAYS_2021,
        RESEARCH_FULL_HOLIDAYS_2022,
        RESEARCH_FULL_HOLIDAYS_2023,
        RESEARCH_FULL_HOLIDAYS_2024,
        RESEARCH_FULL_HOLIDAYS_2025,
    )

    all_tables = [
        RESEARCH_FULL_HOLIDAYS_2018, RESEARCH_FULL_HOLIDAYS_2019, RESEARCH_FULL_HOLIDAYS_2020,
        RESEARCH_FULL_HOLIDAYS_2021, RESEARCH_FULL_HOLIDAYS_2022, RESEARCH_FULL_HOLIDAYS_2023,
        RESEARCH_FULL_HOLIDAYS_2024, RESEARCH_FULL_HOLIDAYS_2025,
    ]
    for i, table_a in enumerate(all_tables):
        for table_b in all_tables[i + 1:]:
            assert not (table_a & table_b)
    assert sum(len(t) for t in all_tables) == len(set().union(*all_tables))


def test_2018_holidays_have_exactly_nine_full_days_and_no_restated_new_years_day():
    # 2018-01-01 (Mon) is New Year's Day observed on its own date -- no
    # Saturday/Sunday shift needed, so unlike 2023/2028-style years there
    # is exactly one New Year's entry, not zero or a shifted one.
    from src.orb_research_historical_calendar import RESEARCH_FULL_HOLIDAYS_2018

    assert len(RESEARCH_FULL_HOLIDAYS_2018) == 9
    assert date(2018, 1, 1) in RESEARCH_FULL_HOLIDAYS_2018


def test_rvol_warmup_lookback_before_2019_outcome_start_reaches_into_december_2018():
    # M50's ORB RVOL warmup (20 sessions before 2019-01-02) must resolve
    # entirely within this module's now-extended coverage.
    sessions = research_trading_sessions_before(date(2019, 1, 2), count=20)
    assert len(sessions) == 20
    assert sessions[-1] < date(2019, 1, 2)
    assert sessions[0] >= date(2018, 1, 1)


def test_full_m50_outcome_window_2019_2020_has_no_calendar_gap():
    # Every calendar day in the M50 outcome window resolves (either to a
    # session or a confirmed non-session) without raising -- the coverage
    # floor genuinely spans the whole window, not just its two boundaries.
    from datetime import timedelta

    day = date(2019, 1, 2)
    end = date(2020, 12, 31)
    session_count = 0
    while day <= end:
        result = research_regular_session_bounds(day)  # raises if out of coverage
        if result is not None:
            session_count += 1
        day += timedelta(days=1)
    assert session_count > 480  # roughly 252/year * 2 years, minus holidays
