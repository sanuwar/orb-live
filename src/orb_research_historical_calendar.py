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

2022 was added (local milestone M40, independent single-trade-core
validation) for the identical reason one year further back again: an
independent out-of-sample validation window ("P_MINUS_1") of 252 sessions
ending immediately before 2023-03-31 (P0's own first session) reaches into
2022 -- the 2023-01-01 floor covered only 61 sessions before that
reference day, 191 short. Same sourcing standard again -- no fabricated
dates. 2022-01-01 fell on a Saturday; per `src.exchange_calendar`'s own
documented rule ("New Year's Day the standing exception, which is why
there is no entry for a Friday before a Saturday 1 January" --
`src/exchange_calendar.py`, `FULL_HOLIDAYS`), New Year's Day is NOT
observed on the preceding Friday the way every other Saturday holiday is,
so 2021-12-31 is an ordinary trading session, not a closure -- see the
2021 paragraph and Issue #334 for the correction of an earlier mistake
here. 2022 was also the first year NYSE observed Juneteenth as a market
holiday.

2021 was added (local milestone M42, independent 09:50-confirmation
validation) for the identical reason one year further back again: an
independent out-of-sample validation window ("P_MINUS_2") of 252 sessions
ending immediately before 2022-03-30 (P_MINUS_1's own first session)
reaches into 2021. Same sourcing standard again -- no fabricated dates.
2021 predates NYSE's first Juneteenth observance (2022), so unlike 2022+
there is no Juneteenth entry this year. `RESEARCH_FULL_HOLIDAYS_2021`
originally (incorrectly) included 2021-12-31 as "New Year's Day 2022
observed" -- corrected by Issue #334 after NYSE historical market-data
evidence showed real trading activity that day. The mistake was applying
the general Saturday-holiday-observed-the-preceding-Friday rule to New
Year's Day, which `src.exchange_calendar.FULL_HOLIDAYS`'s own
documentation already states is the one holiday that rule does NOT apply
to (see the 2022 paragraph above). 2021-12-31 is therefore an ordinary
trading session in this module, matching NYSE's actual historical
calendar.

2018, 2019, and 2020 were added (M50, Issue #352, untouched 2019-2020
portfolio validation) for a different reason than every year above: not a
252-session lookback reaching backward from a newer study, but the study's
own frozen outcome window (2019-01-02 through 2020-12-31) and its
daily-indicator warmup target (2018-01-02) landing entirely before this
module's previous 2021-01-01 floor. Source: the official NYSE Group press
release "NYSE Group Announces 2018, 2019 and 2020 Holiday and Early
Closings Calendar" (Intercontinental Exchange investor-relations site),
cross-checked against the fixed-position US market-holiday rules (Monday
holidays computed by weekday, Good Friday by the Easter date) already
used to sanity-check every other year in this file. Same sourcing
standard as every year above -- no fabricated dates. 2018-2020 all
predate NYSE's first Juneteenth observance (2022), so none of the three
years has a Juneteenth entry. 2020 is the one year among the three where
Independence Day's Saturday observance shifts to a FULL holiday on the
preceding Friday (July 3) rather than a half-day -- confirmed directly
against the source press release, not inferred from the general Saturday-
holiday rule, since New Year's Day is a documented exception to that rule
and Independence Day's own treatment is therefore verified explicitly
rather than assumed to follow the general pattern.

For any date already inside `src.exchange_calendar`'s own coverage window,
this module delegates to the production `regular_session_bounds` unchanged
-- there is exactly one source of truth for any given date, never two
tables that could silently disagree. Coverage is finite and explicitly
stated here too: a date outside [2018-01-01, exchange_calendar's own
`last_supported_date`] raises `ResearchCalendarError` rather than guessing,
so a mistaken request (e.g. for 2017 data) fails loudly instead of
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
RESEARCH_CALENDAR_FIRST_SUPPORTED_DATE = date(2018, 1, 1)

#: Source: NYSE Group's official "2018, 2019 and 2020 Holiday and Early
#: Closings Calendar" press release. Full-day closures. 2018 predates
#: NYSE's first Juneteenth observance (2022), so there is no Juneteenth
#: entry this year (same as 2021's own table).
RESEARCH_FULL_HOLIDAYS_2018: frozenset[date] = frozenset({
    date(2018, 1, 1),   # New Year's Day (Mon)
    date(2018, 1, 15),  # Martin Luther King Jr. Day
    date(2018, 2, 19),  # Washington's Birthday
    date(2018, 3, 30),  # Good Friday
    date(2018, 5, 28),  # Memorial Day
    date(2018, 7, 4),   # Independence Day (Wed)
    date(2018, 9, 3),   # Labor Day
    date(2018, 11, 22), # Thanksgiving
    date(2018, 12, 25), # Christmas (Tue)
})

#: Source: NYSE Group's official 2018 early-close (half-day, 13:00 ET)
#: schedule. July 4 fell midweek (Wednesday), so July 3 is a half-day, not
#: a full closure -- unlike 2020 below.
RESEARCH_EARLY_CLOSE_DAYS_2018: Mapping[date, time] = {
    date(2018, 7, 3): EARLY_REGULAR_CLOSE,   # day before Independence Day (Tue)
    date(2018, 11, 23): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
    date(2018, 12, 24): EARLY_REGULAR_CLOSE, # Christmas Eve (Mon)
}

#: Source: NYSE Group's official "2018, 2019 and 2020 Holiday and Early
#: Closings Calendar" press release. Full-day closures. 2019 predates
#: NYSE's first Juneteenth observance (2022), so there is no Juneteenth
#: entry this year.
RESEARCH_FULL_HOLIDAYS_2019: frozenset[date] = frozenset({
    date(2019, 1, 1),   # New Year's Day (Tue)
    date(2019, 1, 21),  # Martin Luther King Jr. Day
    date(2019, 2, 18),  # Washington's Birthday
    date(2019, 4, 19),  # Good Friday
    date(2019, 5, 27),  # Memorial Day
    date(2019, 7, 4),   # Independence Day (Thu)
    date(2019, 9, 2),   # Labor Day
    date(2019, 11, 28), # Thanksgiving
    date(2019, 12, 25), # Christmas (Wed)
})

#: Source: NYSE Group's official 2019 early-close (half-day, 13:00 ET)
#: schedule. July 4 fell midweek (Thursday), so July 3 is a half-day, not
#: a full closure -- unlike 2020 below.
RESEARCH_EARLY_CLOSE_DAYS_2019: Mapping[date, time] = {
    date(2019, 7, 3): EARLY_REGULAR_CLOSE,   # day before Independence Day (Wed)
    date(2019, 11, 29): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
    date(2019, 12, 24): EARLY_REGULAR_CLOSE, # Christmas Eve (Tue)
}

#: Source: NYSE Group's official "2018, 2019 and 2020 Holiday and Early
#: Closings Calendar" press release. Full-day closures. 2020 predates
#: NYSE's first Juneteenth observance (2022), so there is no Juneteenth
#: entry this year. Independence Day (2020-07-04) fell on a Saturday; per
#: the source press release, NYSE observed it as a FULL holiday on the
#: preceding Friday (2020-07-03), not a half-day -- verified directly
#: against the source rather than assumed from the general Saturday-
#: holiday rule (which, per the 2022 table's own comment, does NOT apply
#: uniformly to every holiday -- New Year's Day is the documented
#: exception, so Independence Day's own treatment is confirmed explicitly
#: here, not inferred).
RESEARCH_FULL_HOLIDAYS_2020: frozenset[date] = frozenset({
    date(2020, 1, 1),   # New Year's Day (Wed)
    date(2020, 1, 20),  # Martin Luther King Jr. Day
    date(2020, 2, 17),  # Washington's Birthday
    date(2020, 4, 10),  # Good Friday
    date(2020, 5, 25),  # Memorial Day
    date(2020, 7, 3),   # Independence Day observed (Jul 4 was a Saturday)
    date(2020, 9, 7),   # Labor Day
    date(2020, 11, 26), # Thanksgiving
    date(2020, 12, 25), # Christmas (Fri)
})

#: Source: NYSE Group's official 2020 early-close (half-day, 13:00 ET)
#: schedule. No separate July 3 early close this year -- 2020-07-03 is
#: itself the full Independence Day holiday observance above, not a
#: half-day.
RESEARCH_EARLY_CLOSE_DAYS_2020: Mapping[date, time] = {
    date(2020, 11, 27): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
    date(2020, 12, 24): EARLY_REGULAR_CLOSE, # Christmas Eve (Thu)
}

#: The last date this module answers from its own 2018/2019/2020/2021/
#: 2022/2023/2024/2025 tables. From `src.exchange_calendar`'s own `first_supported_date`
#: (2026-01-01) onward, every lookup delegates to the production calendar
#: instead, so the two tables never overlap and can never disagree.
_RESEARCH_ONLY_UPPER_BOUND = date(2025, 12, 31)

#: Source: NYSE published 2021 holiday calendar. Full-day closures. 2021
#: predates NYSE's first Juneteenth observance (2022), so unlike 2022+
#: there is no Juneteenth entry this year. 2021-01-01 fell on a Friday, so
#: New Year's Day is observed on its own date -- no Saturday/Sunday shift
#: needed, unlike most other years in this file. 2021-12-31 is
#: deliberately absent: 2022-01-01 (New Year's Day 2022) fell on a
#: Saturday, and per `src.exchange_calendar.FULL_HOLIDAYS`'s own
#: documented rule, New Year's Day is the one holiday NOT observed on the
#: preceding Friday -- confirmed by NYSE historical market-data evidence
#: of real trading activity on 2021-12-31 (Issue #334; an earlier version
#: of this table incorrectly included it as a closure).
RESEARCH_FULL_HOLIDAYS_2021: frozenset[date] = frozenset({
    date(2021, 1, 1),   # New Year's Day (Fri)
    date(2021, 1, 18),  # Martin Luther King Jr. Day
    date(2021, 2, 15),  # Washington's Birthday
    date(2021, 4, 2),   # Good Friday
    date(2021, 5, 31),  # Memorial Day
    date(2021, 7, 5),   # Independence Day observed (Jul 4 was a Sunday)
    date(2021, 9, 6),   # Labor Day
    date(2021, 11, 25), # Thanksgiving
    date(2021, 12, 24), # Christmas observed (Dec 25 was a Saturday)
})

#: Source: NYSE published 2021 early-close (half-day, 13:00 ET) schedule.
#: Only one half-day in 2021 -- day after Thanksgiving. Independence Day
#: fell on a Sunday (observed the following Monday), so unlike years where
#: July 4 falls midweek there is no separate July 3 early close that year;
#: Christmas Eve (2021-12-24) is itself the observed Christmas Day full
#: holiday above (Dec 25 fell on a Saturday), not a separate half day.
RESEARCH_EARLY_CLOSE_DAYS_2021: Mapping[date, time] = {
    date(2021, 11, 26): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
}

#: Source: NYSE published 2022 holiday calendar. Full-day closures. 2022
#: has no restated New Year's Day entry: 2022-01-01 fell on a Saturday,
#: and per `src.exchange_calendar.FULL_HOLIDAYS`'s own documented rule,
#: New Year's Day is the one holiday NOT observed on the preceding Friday
#: -- 2021-12-31 is an ordinary trading session (see the 2021 table's own
#: comment; Issue #334 corrected an earlier mistake here). 2022 was also
#: the first year NYSE observed Juneteenth as a market holiday.
RESEARCH_FULL_HOLIDAYS_2022: frozenset[date] = frozenset({
    date(2022, 1, 17),  # Martin Luther King Jr. Day
    date(2022, 2, 21),  # Washington's Birthday
    date(2022, 4, 15),  # Good Friday
    date(2022, 5, 30),  # Memorial Day
    date(2022, 6, 20),  # Juneteenth observed (Jun 19 was a Sunday) -- first year observed
    date(2022, 7, 4),   # Independence Day (Mon)
    date(2022, 9, 5),   # Labor Day
    date(2022, 11, 24), # Thanksgiving
    date(2022, 12, 26), # Christmas observed (Dec 25 was a Sunday)
})

#: Source: NYSE published 2022 early-close (half-day, 13:00 ET) schedule.
#: Only one half-day in 2022 -- July 4 fell on a Monday (the holiday
#: itself), so unlike 2023/2024/2025 there is no separate July 3
#: early-close that year, and Christmas Eve (2022-12-24) fell on a
#: Saturday, so there is no Christmas Eve early-close trading day either.
RESEARCH_EARLY_CLOSE_DAYS_2022: Mapping[date, time] = {
    date(2022, 11, 25): EARLY_REGULAR_CLOSE, # day after Thanksgiving (Fri)
}

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

#: Combined lookup spanning all eight restated years -- built once here so
#: `research_regular_session_bounds` has exactly one merged table to
#: consult instead of per-year branching. The eight years' key sets are
#: disjoint by construction (tested), so a plain union cannot silently
#: shadow any source table.
_RESEARCH_FULL_HOLIDAYS: frozenset[date] = (
    RESEARCH_FULL_HOLIDAYS_2018 | RESEARCH_FULL_HOLIDAYS_2019 | RESEARCH_FULL_HOLIDAYS_2020
    | RESEARCH_FULL_HOLIDAYS_2021 | RESEARCH_FULL_HOLIDAYS_2022 | RESEARCH_FULL_HOLIDAYS_2023
    | RESEARCH_FULL_HOLIDAYS_2024 | RESEARCH_FULL_HOLIDAYS_2025
)
_RESEARCH_EARLY_CLOSE_DAYS: Mapping[date, time] = {
    **RESEARCH_EARLY_CLOSE_DAYS_2018,
    **RESEARCH_EARLY_CLOSE_DAYS_2019,
    **RESEARCH_EARLY_CLOSE_DAYS_2020,
    **RESEARCH_EARLY_CLOSE_DAYS_2021,
    **RESEARCH_EARLY_CLOSE_DAYS_2022,
    **RESEARCH_EARLY_CLOSE_DAYS_2023,
    **RESEARCH_EARLY_CLOSE_DAYS_2024,
    **RESEARCH_EARLY_CLOSE_DAYS_2025,
}


def research_regular_session_bounds(day: date) -> tuple[time, time] | None:
    """Open/close bounds for `day`, or `None` if the market does not open,
    extending `src.exchange_calendar.regular_session_bounds` backward to
    2018-01-01 for research use only.

    Delegates unchanged to the production function for any date already in
    its own coverage; answers from the merged 2018/2019/2020/2021/2022/
    2023/2024/2025 `RESEARCH_FULL_HOLIDAYS_*`/`RESEARCH_EARLY_CLOSE_DAYS_*`
    tables for 2018-2025 dates; raises `ResearchCalendarError` for anything
    before 2018-01-01 or after production's own `last_supported_date` --
    never a silent guess."""
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
    function so it can reach back to 2018. Raises `ResearchCalendarError`
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
    "RESEARCH_FULL_HOLIDAYS_2018",
    "RESEARCH_EARLY_CLOSE_DAYS_2018",
    "RESEARCH_FULL_HOLIDAYS_2019",
    "RESEARCH_EARLY_CLOSE_DAYS_2019",
    "RESEARCH_FULL_HOLIDAYS_2020",
    "RESEARCH_EARLY_CLOSE_DAYS_2020",
    "RESEARCH_FULL_HOLIDAYS_2021",
    "RESEARCH_EARLY_CLOSE_DAYS_2021",
    "RESEARCH_FULL_HOLIDAYS_2022",
    "RESEARCH_EARLY_CLOSE_DAYS_2022",
    "RESEARCH_FULL_HOLIDAYS_2023",
    "RESEARCH_EARLY_CLOSE_DAYS_2023",
    "RESEARCH_FULL_HOLIDAYS_2024",
    "RESEARCH_EARLY_CLOSE_DAYS_2024",
    "RESEARCH_FULL_HOLIDAYS_2025",
    "RESEARCH_EARLY_CLOSE_DAYS_2025",
    "research_regular_session_bounds",
    "research_trading_sessions_before",
]
