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


def test_dates_inside_production_coverage_delegate_unchanged():
    for day in (date(2026, 1, 2), date(2026, 4, 6), date(2026, 11, 27)):
        assert research_regular_session_bounds(day) == regular_session_bounds(day)


def test_date_before_research_coverage_floor_raises():
    with pytest.raises(ResearchCalendarError):
        research_regular_session_bounds(date(2024, 12, 31))


def test_datetime_input_raises_instead_of_silently_truncating():
    with pytest.raises(ResearchCalendarError):
        research_regular_session_bounds(datetime(2025, 4, 3, 9, 30))  # type: ignore[arg-type]


def test_research_calendar_first_supported_date_is_2025_01_01():
    assert RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE == date(2025, 1, 1)


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


def test_trading_sessions_before_beyond_2025_coverage_fails_closed():
    with pytest.raises(ResearchCalendarError):
        research_trading_sessions_before(date(2026, 4, 7), count=400)


def test_trading_sessions_before_rejects_non_positive_count():
    with pytest.raises(ResearchCalendarError):
        research_trading_sessions_before(date(2025, 6, 1), count=0)
