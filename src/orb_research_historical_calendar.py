"""RESEARCH-ONLY historical NYSE trading-session calendar (Issue TBD, opened
by the out-of-sample validation study).

Why this file exists: `src/exchange_calendar.py` -- the digest-pinned,
production-authoritative calendar -- states its own coverage as finite
(2026-01-01 through 2027-12-31, `SCHEDULE_METADATA`) and returns `None`/
`UNKNOWN` outside it *by design*, never a guess. The out-of-sample
validation study (Issue TBD) needs roughly 252 trading sessions ending
before 2026-04-07, which reaches back into 2025 -- outside that window.
Extending `src/exchange_calendar.py` itself was explicitly ruled out: it is
listed in `src.read_only_authorization_manifest.PINNED_IMPLEMENTATION_CLOSURE`
and backs a real broker-facing checkpoint, so widening its table is a
production-safety change, not a research one.

This module is the smallest alternative: a second, independent, explicitly
RESEARCH-ONLY table that extends coverage backward, used only by
`src/orb_out_of_sample_validation_research.py`, `src/orb_regime_diagnostic_research.py`,
the third-period RVOL-band validation study (Issue TBD), and their CLI
scripts. It is never imported by `src/exchange_calendar.py`, by any file in
`PINNED_IMPLEMENTATION_CLOSURE`, or by anything reachable from
`read_only_observation_checkpoint_launcher.py` --
`tests/test_orb_research_historical_calendar.py` asserts this isolation
directly (both by grepping the pinned closure's source for this module's
name and by checking it is absent from the closure list itself), so the
checkpoint's implementation digest cannot change because this file exists.

Provenance: NYSE published holiday and early-close schedule -- the same
source class `src/exchange_calendar.py`'s own `SCHEDULE_METADATA` cites --
restated here for 2024 and 2025, both years outside that module's own
coverage. Every date below is a fixed, publicly published NYSE closure that
does not change after the fact (holiday and early-close schedules are set
years in advance and are a matter of public record), matching the
observance rules `src/exchange_calendar.py` documents (Saturday holidays
observed the preceding Friday, Sunday holidays observed the following
Monday, New Year's Day the standing exception).

2024 was added (Issue #317, third-period RVOL-band validation) once the
252-session lookback window ending immediately before 2025-04-03 was found
to reach back into 2024 -- the original 2025-01-01 floor covered only 63
sessions before that reference day, short of even the study's 120-session
absolute minimum. Same sourcing standard as 2025: no fabricated dates, only
the standard, publicly published NYSE holiday/early-close schedule.

2023 was added (Issue #321, fourth-period baseline validation) for the
identical reason one year further back: the 252-session window ending
immediately before 2024-04-03 (P1's own first session) reaches into 2023.
Same sourcing standard again -- no fabricated dates.

For any date already inside `src.exchange_calendar`'s own coverage window,
this module delegates to the production `regular_session_bounds` unchanged
-- there is exactly one source of truth for any given date, never two
tables that could silently disagree. Coverage is finite and explicitly
stated here too: a date outside [2023-01-01, exchange_calendar's own
`last_supported_date`] raises `ResearchCalendarError` rather than guessing,
so a mistaken request (e.g. for 2022 data) fails loudly instead of
returning a fabricated answer.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Mapping

from src.exchange_calendar import (
    EARLY_REGULAR_CLOSE,
    REGULAR_CLOSE,
    REGULAR_OPEN,
    regular_session_bounds,
    supported_range,
)


class ResearchCalendarError(RuntimeError):
    """Refused: the research-only historical calendar could not safely
    answer -- date outside its own stated coverage, or a malformed input.
    Never returned as `None`; a caller must not be able to mistake "outside
    coverage" for "market closed"."""


#: This module's own lower bound. Below this, no NYSE holiday/early-close
#: data has been restated here, so this module refuses rather than guess.
RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE = date(2023, 1, 1)

#: The last date this module answers from its own 2023/2024/2025 tables.
#: From `src.exchange_calendar`'s own `first_supported_date` (2026-01-01)
#: onward, every lookup delegates to the production calendar instead, so
#: the two tables never overlap and can never disagree.
_RESEARCH_ONLY_UPPER_BOUND = date(2025, 12, 31)

#: Source: NYSE published 2023 holiday calendar. Full-day closures.
RESEARCH_FULL_HOLIDAYS_2023: frozenset[date] = frozenset({
    date(2023, 1, 2),   # New Year's Day observed (Jan 1 was a Sunday)
    date(2023, 1, 16),  # Martin Luther King Jr. Day
    date(2023, 2, 20),  # Washington's Birthday
    date(2023, 4, 7),   # Good Friday
    date(2023, 5, 29),  # Memorial Day
    date(2023, 6, 19),  # Juneteenth (Mon)
    date(2023, 7, 4),   # Independence Day (Tue)
    date(2023, 9, 4),   # Labor Day
    date(2023, 11, 23), # Thanksgiving
    date(2023, 12, 25), # Christmas (Mon)
})

#: Source: NYSE published 2023 early-close (half-day, 13:00 ET) schedule.
#: Only two half-days in 2023 -- Christmas Eve (2023-12-24) fell on a
#: Sunday, so unlike 2024/2025 there is no separate Christmas-adjacent
#: early close that year.
RESEARCH_EARLY_CLOSE_DAYS_2023: Mapping[date, time] = {
    date(2023, 7, 3): EARLY_REGULAR_CLOSE,   # day before Independence Day (Mon)
    date(2023, 11, 24): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
}

#: Source: NYSE published 2024 holiday calendar. Full-day closures.
RESEARCH_FULL_HOLIDAYS_2024: frozenset[date] = frozenset({
    date(2024, 1, 1),   # New Year's Day (Mon)
    date(2024, 1, 15),  # Martin Luther King Jr. Day
    date(2024, 2, 19),  # Washington's Birthday
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth (Wed)
    date(2024, 7, 4),   # Independence Day (Thu)
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas (Wed)
})

#: Source: NYSE published 2024 early-close (half-day, 13:00 ET) schedule.
RESEARCH_EARLY_CLOSE_DAYS_2024: Mapping[date, time] = {
    date(2024, 7, 3): EARLY_REGULAR_CLOSE,   # day before Independence Day (Wed)
    date(2024, 11, 29): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
    date(2024, 12, 24): EARLY_REGULAR_CLOSE, # Christmas Eve (Tue)
}

#: Source: NYSE published 2025 holiday calendar. Full-day closures.
RESEARCH_FULL_HOLIDAYS_2025: frozenset[date] = frozenset({
    date(2025, 1, 1),   # New Year's Day (Wed)
    date(2025, 1, 20),  # Martin Luther King Jr. Day
    date(2025, 2, 17),  # Washington's Birthday
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),  # Memorial Day
    date(2025, 6, 19),  # Juneteenth (Thu)
    date(2025, 7, 4),   # Independence Day (Fri)
    date(2025, 9, 1),   # Labor Day
    date(2025, 11, 27), # Thanksgiving
    date(2025, 12, 25), # Christmas (Thu)
})

#: Source: NYSE published 2025 early-close (half-day, 13:00 ET) schedule.
RESEARCH_EARLY_CLOSE_DAYS_2025: Mapping[date, time] = {
    date(2025, 7, 3): EARLY_REGULAR_CLOSE,   # day before Independence Day (Thu)
    date(2025, 11, 28): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
    date(2025, 12, 24): EARLY_REGULAR_CLOSE, # Christmas Eve (Wed)
}

#: Combined lookup spanning all three restated years -- built once here so
#: `research_regular_session_bounds` has exactly one merged table to
#: consult instead of per-year branching. The three years' key sets are
#: disjoint by construction (tested), so a plain union cannot silently
#: shadow any source table.
_RESEARCH_FULL_HOLIDAYS: frozenset[date] = (
    RESEARCH_FULL_HOLIDAYS_2023 | RESEARCH_FULL_HOLIDAYS_2024 | RESEARCH_FULL_HOLIDAYS_2025
)
_RESEARCH_EARLY_CLOSE_DAYS: Mapping[date, time] = {
    **RESEARCH_EARLY_CLOSE_DAYS_2023,
    **RESEARCH_EARLY_CLOSE_DAYS_2024,
    **RESEARCH_EARLY_CLOSE_DAYS_2025,
}


def research_regular_session_bounds(day: date) -> tuple[time, time] | None:
    """Open/close bounds for `day`, or `None` if the market does not open,
    extending `src.exchange_calendar.regular_session_bounds` backward to
    2023-01-01 for research use only.

    Delegates unchanged to the production function for any date already in
    its own coverage; answers from the merged 2023/2024/2025
    `RESEARCH_FULL_HOLIDAYS_*`/`RESEARCH_EARLY_CLOSE_DAYS_*` tables for
    2023-2025 dates; raises `ResearchCalendarError` for anything before
    2023-01-01 or after production's own `last_supported_date` -- never a
    silent guess."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ResearchCalendarError(f"expected a date, got {day!r}")
    production_first, production_last = supported_range()
    if production_first <= day <= production_last:
        return regular_session_bounds(day)
    if RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE <= day <= _RESEARCH_ONLY_UPPER_BOUND:
        if day.weekday() >= 5 or day in _RESEARCH_FULL_HOLIDAYS:
            return None
        return (REGULAR_OPEN, _RESEARCH_EARLY_CLOSE_DAYS.get(day, REGULAR_CLOSE))
    raise ResearchCalendarError(
        f"{day.isoformat()} is outside this research calendar's coverage "
        f"({RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE.isoformat()} through "
        f"{production_last.isoformat()}); refusing to guess"
    )


def research_trading_sessions_before(reference_day: date, *, count: int) -> tuple[date, ...]:
    """The `count` most recent research-calendar-supported trading sessions
    strictly before `reference_day`, oldest first -- the research-calendar
    equivalent of
    `src.orb_universe_expansion_research.trading_sessions_before`, using
    `research_regular_session_bounds` instead of the production-only
    function so it can reach back to 2023. Raises `ResearchCalendarError`
    (propagated from `research_regular_session_bounds`, fail-closed, not
    caught here) if walking back runs past this module's own coverage floor
    before finding `count` sessions."""
    if count < 1:
        raise ResearchCalendarError("count must be positive")
    values: list[date] = []
    cursor = reference_day - timedelta(days=1)
    while len(values) < count:
        if research_regular_session_bounds(cursor) is not None:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(reversed(values))


__all__ = [
    "ResearchCalendarError",
    "RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE",
    "RESEARCH_FULL_HOLIDAYS_2023",
    "RESEARCH_EARLY_CLOSE_DAYS_2023",
    "RESEARCH_FULL_HOLIDAYS_2024",
    "RESEARCH_EARLY_CLOSE_DAYS_2024",
    "RESEARCH_FULL_HOLIDAYS_2025",
    "RESEARCH_EARLY_CLOSE_DAYS_2025",
    "research_regular_session_bounds",
    "research_trading_sessions_before",
]
