"""Authoritative US equity exchange calendar — static, tracked, fail-closed.

This module answers one question: **at this instant, was the regular session
open?** It answers it from a curated table rather than a feed, and it refuses
to answer at all whenever the table cannot support a confident answer.

"Authoritative" here means *cited and reviewable*, not *fetched*. There is no
network call at runtime and none was made to build it: the schedule is derived
from the published NYSE holiday and early-close calendar, and carries the
provenance needed for a human to check it against that source. A table nobody
can audit is not authoritative no matter where it came from.

Three properties are deliberate:

1. **Coverage is finite and stated.** 2026-01-01 through 2027-12-31. A date
   outside that range is `UNKNOWN`, never a guess. The table cannot age into
   silent wrongness.
2. **One load-bearing boolean.** `SessionClassification.is_regular_session` is
   true *only* for a confirmed regular session. Every unknown, closed,
   pre-market, and post-market result is false, so a caller that checks nothing
   else still fails closed.
3. **After an early close the answer is CLOSED, not POST_MARKET.** Half-day
   extended trading does exist; this table does not model its bounds, and
   reporting a session it cannot bound is the same error as reporting a
   half-day afternoon as regular hours.

This module is independent by construction. It touches no broker session, no
authorization manifest, no checkpoint, no strategy flag, and no order path, and
it imports nothing from the checkpoint implementation-digest files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")


class ExchangeCalendarError(RuntimeError):
    """The schedule itself is invalid. Raised by validation, never by lookup."""


# --------------------------------------------------------------------------
# Result model.
# --------------------------------------------------------------------------


class CalendarSession(str, Enum):
    """Named `CalendarSession`, not `MarketSession`, to stay distinguishable
    from the snapshot enum in `read_only_broker_state` — these describe
    different things and must never be confused at a call site."""

    REGULAR = "REGULAR"
    PRE_MARKET = "PRE_MARKET"
    POST_MARKET = "POST_MARKET"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class DayKind(str, Enum):
    TRADING_DAY = "TRADING_DAY"
    EARLY_CLOSE_DAY = "EARLY_CLOSE_DAY"
    FULL_HOLIDAY = "FULL_HOLIDAY"
    WEEKEND = "WEEKEND"
    UNSUPPORTED = "UNSUPPORTED"


class ClassificationReason(str, Enum):
    """Why the answer is what it is. Every non-OK value fails closed."""

    OK = "OK"
    WEEKEND = "WEEKEND"
    FULL_HOLIDAY = "FULL_HOLIDAY"
    BEFORE_PRE_MARKET = "BEFORE_PRE_MARKET"
    AFTER_POST_MARKET = "AFTER_POST_MARKET"
    AFTER_EARLY_CLOSE = "AFTER_EARLY_CLOSE"
    DATE_OUTSIDE_SUPPORTED_RANGE = "DATE_OUTSIDE_SUPPORTED_RANGE"
    SCHEDULE_EXPIRED = "SCHEDULE_EXPIRED"
    SCHEDULE_MALFORMED = "SCHEDULE_MALFORMED"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    AMBIGUOUS_LOCAL_TIME = "AMBIGUOUS_LOCAL_TIME"
    NOT_A_DATETIME = "NOT_A_DATETIME"


#: Reasons that mean "this module could not determine the session". Kept
#: explicit so a caller can distinguish "closed" from "do not know".
UNKNOWN_REASONS: frozenset[ClassificationReason] = frozenset({
    ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE,
    ClassificationReason.SCHEDULE_EXPIRED,
    ClassificationReason.SCHEDULE_MALFORMED,
    ClassificationReason.NAIVE_TIMESTAMP,
    ClassificationReason.AMBIGUOUS_LOCAL_TIME,
    ClassificationReason.NOT_A_DATETIME,
})


@dataclass(frozen=True)
class SessionClassification:
    session: CalendarSession
    day_kind: DayKind
    reason: ClassificationReason
    is_regular_session: bool

    def __post_init__(self) -> None:
        # The invariant the whole module exists to guarantee. Enforced on the
        # object rather than trusted at each construction site.
        if self.is_regular_session and (
            self.session is not CalendarSession.REGULAR
            or self.reason is not ClassificationReason.OK
        ):
            raise ExchangeCalendarError(
                "is_regular_session may only accompany a confirmed regular session"
            )
        if self.session is CalendarSession.UNKNOWN and self.is_regular_session:
            raise ExchangeCalendarError("an unknown session is never tradeable")


def _unknown(reason: ClassificationReason) -> SessionClassification:
    return SessionClassification(
        session=CalendarSession.UNKNOWN,
        day_kind=DayKind.UNSUPPORTED,
        reason=reason,
        is_regular_session=False,
    )


# --------------------------------------------------------------------------
# Session boundaries.
# --------------------------------------------------------------------------

PRE_MARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_REGULAR_CLOSE = time(13, 0)
POST_MARKET_CLOSE = time(20, 0)


# --------------------------------------------------------------------------
# The schedule. Static, tracked, and citable.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleMetadata:
    """Provenance and maintenance state of the table below.

    `last_supported_date` and `review_due` are deliberately different things:

    * `last_supported_date` is **coverage** — past it, classification is
      impossible and returns UNKNOWN.
    * `review_due` is **maintenance** — a date by which a human should
      re-verify the table against the published source and extend it. It is
      set comfortably *before* coverage lapses so the warning arrives while
      there is still time to act, not after the calendar has already gone
      blind.

    A schedule can therefore be simultaneously valid for lookups and overdue
    for review, which is exactly the state that should prompt work.
    """

    source: str
    version: str
    supported_years: tuple[int, ...]
    first_supported_date: date
    last_supported_date: date
    verified_on: date
    verified_by: str
    review_due: date


SCHEDULE_METADATA = ScheduleMetadata(
    source="NYSE published holiday and early-close schedule",
    version="2026.1",
    supported_years=(2026, 2027),
    first_supported_date=date(2026, 1, 1),
    last_supported_date=date(2027, 12, 31),
    verified_on=date(2026, 8, 1),
    verified_by="operator review, PR #44 (F3 early-close correction)",
    review_due=date(2027, 6, 30),
)


#: Full-day market closures. Observance follows NYSE practice: a holiday on a
#: Saturday is observed the preceding Friday, one on a Sunday the following
#: Monday — with New Year's Day the standing exception, which is why there is
#: no entry for a Friday before a Saturday 1 January.
FULL_HOLIDAYS: frozenset[date] = frozenset({
    # 2026
    date(2026, 1, 1),    # New Year's Day (Thu)
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Washington's Birthday
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth (Fri)
    date(2026, 7, 3),    # Independence Day observed (4 Jul is Sat)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas (Fri)
    # 2027
    date(2027, 1, 1),    # New Year's Day (Fri)
    date(2027, 1, 18),   # Martin Luther King Jr. Day
    date(2027, 2, 15),   # Washington's Birthday
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth observed (19 Jun is Sat)
    date(2027, 7, 5),    # Independence Day observed (4 Jul is Sun)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas observed (25 Dec is Sat)
})


#: Half-days: the regular session ends at 13:00 ET.
#:
#: Note what is *absent* and why. There is no 2026-07-02 or 2027-07-02 entry:
#: the early close before Independence Day applies when 4 July is a weekday,
#: and in both years it is not. There is no 2027-12-23 entry either, because
#: Christmas Eve 2027 is itself the observed full holiday above. Omissions
#: here are decisions, not gaps.
EARLY_CLOSE_DAYS: Mapping[date, time] = MappingProxyType({
    date(2026, 11, 27): EARLY_REGULAR_CLOSE,  # day after Thanksgiving
    date(2026, 12, 24): EARLY_REGULAR_CLOSE,  # Christmas Eve (Thu)
    date(2027, 11, 26): EARLY_REGULAR_CLOSE,  # day after Thanksgiving
})


# --------------------------------------------------------------------------
# Validation.
# --------------------------------------------------------------------------


def validate_schedule(
    *,
    metadata: ScheduleMetadata = SCHEDULE_METADATA,
    holidays: frozenset[date] = FULL_HOLIDAYS,
    early_closes: Mapping[date, time] = EARLY_CLOSE_DAYS,
) -> None:
    """Reject a schedule that cannot be trusted, loudly and at the source.

    Called at import so a malformed table is a startup failure rather than a
    wrong answer discovered later.
    """
    if metadata.first_supported_date > metadata.last_supported_date:
        raise ExchangeCalendarError("supported range starts after it ends")
    if metadata.review_due < metadata.verified_on:
        raise ExchangeCalendarError("review is due before the schedule was verified")
    if metadata.review_due > metadata.last_supported_date:
        raise ExchangeCalendarError(
            "review must fall due before coverage lapses, not after"
        )
    if not metadata.supported_years:
        raise ExchangeCalendarError("no supported years declared")
    declared = set(metadata.supported_years)
    if declared != {
        year
        for year in range(
            metadata.first_supported_date.year, metadata.last_supported_date.year + 1
        )
    }:
        raise ExchangeCalendarError("supported years disagree with the supported range")

    for label, days in (("holiday", holidays), ("early close", set(early_closes))):
        for day in days:
            if not isinstance(day, date) or isinstance(day, datetime):
                raise ExchangeCalendarError(f"{label} entry is not a plain date")
            if not (
                metadata.first_supported_date <= day <= metadata.last_supported_date
            ):
                raise ExchangeCalendarError(f"{label} falls outside the supported range")
            if day.weekday() >= 5:
                raise ExchangeCalendarError(f"{label} falls on a weekend")

    overlap = holidays & set(early_closes)
    if overlap:
        raise ExchangeCalendarError("a date cannot be both a holiday and an early close")

    for day, closing in early_closes.items():
        if not isinstance(closing, time):
            raise ExchangeCalendarError("early close time is not a time")
        if not REGULAR_OPEN < closing < REGULAR_CLOSE:
            raise ExchangeCalendarError(
                "early close must fall between the regular open and close"
            )


validate_schedule()


# --------------------------------------------------------------------------
# Maintenance and expiry.
# --------------------------------------------------------------------------


def schedule_metadata() -> ScheduleMetadata:
    return SCHEDULE_METADATA


def schedule_is_expired(
    today: date, *, metadata: ScheduleMetadata = SCHEDULE_METADATA
) -> bool:
    """True once the table can no longer classify the present day."""
    return today > metadata.last_supported_date


def schedule_review_is_overdue(
    today: date, *, metadata: ScheduleMetadata = SCHEDULE_METADATA
) -> bool:
    """True once a human should re-verify and extend the table.

    Distinct from expiry on purpose: this fires while the calendar still
    works, which is the only time acting on it is cheap.
    """
    return today > metadata.review_due


def supported_range() -> tuple[date, date]:
    return (
        SCHEDULE_METADATA.first_supported_date,
        SCHEDULE_METADATA.last_supported_date,
    )


def regular_session_bounds(day: date) -> tuple[time, time] | None:
    """Open and close for a trading day, or None if the market does not open."""
    if not isinstance(day, date) or isinstance(day, datetime):
        return None
    if not (
        SCHEDULE_METADATA.first_supported_date
        <= day
        <= SCHEDULE_METADATA.last_supported_date
    ):
        return None
    if day.weekday() >= 5 or day in FULL_HOLIDAYS:
        return None
    return (REGULAR_OPEN, EARLY_CLOSE_DAYS.get(day, REGULAR_CLOSE))


# --------------------------------------------------------------------------
# Classification.
# --------------------------------------------------------------------------


def _classify_wall_clock(day: date, clock: time) -> SessionClassification:
    """Pure wall-clock classification for a date already known to be covered."""
    if day.weekday() >= 5:
        return SessionClassification(
            CalendarSession.CLOSED, DayKind.WEEKEND,
            ClassificationReason.WEEKEND, False,
        )
    if day in FULL_HOLIDAYS:
        return SessionClassification(
            CalendarSession.CLOSED, DayKind.FULL_HOLIDAY,
            ClassificationReason.FULL_HOLIDAY, False,
        )

    early = EARLY_CLOSE_DAYS.get(day)
    kind = DayKind.EARLY_CLOSE_DAY if early else DayKind.TRADING_DAY
    closing = early or REGULAR_CLOSE

    if clock < PRE_MARKET_OPEN:
        return SessionClassification(
            CalendarSession.CLOSED, kind, ClassificationReason.BEFORE_PRE_MARKET, False
        )
    if clock < REGULAR_OPEN:
        return SessionClassification(
            CalendarSession.PRE_MARKET, kind, ClassificationReason.OK, False
        )
    if clock < closing:
        return SessionClassification(
            CalendarSession.REGULAR, kind, ClassificationReason.OK, True
        )
    if early is not None:
        # Half-day extended trading exists; this table does not model its
        # bounds. Reporting a session it cannot bound would repeat the very
        # error that made half-day afternoons look like regular hours.
        return SessionClassification(
            CalendarSession.CLOSED, kind, ClassificationReason.AFTER_EARLY_CLOSE, False
        )
    if clock < POST_MARKET_CLOSE:
        return SessionClassification(
            CalendarSession.POST_MARKET, kind, ClassificationReason.OK, False
        )
    return SessionClassification(
        CalendarSession.CLOSED, kind, ClassificationReason.AFTER_POST_MARKET, False
    )


def classify(
    moment: datetime, *, today: date | None = None
) -> SessionClassification:
    """Classify one instant. Never guesses, never opens the market by default.

    `today` exists so expiry can be evaluated against a supplied clock rather
    than the wall clock, which keeps this function testable and keeps the
    expiry check honest in a frozen-time context.
    """
    if not isinstance(moment, datetime):
        return _unknown(ClassificationReason.NOT_A_DATETIME)
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        # A naive timestamp is ambiguous by exactly the amount that decides
        # whether the market was open.
        return _unknown(ClassificationReason.NAIVE_TIMESTAMP)

    eastern = moment.astimezone(EASTERN)

    # A wall time whose UTC offset differs between DST folds does not name a
    # single instant. These windows never overlap any session, so refusing
    # them costs nothing and keeps the rule simple: this module does not
    # classify an instant it cannot pin down.
    if eastern.replace(fold=0).utcoffset() != eastern.replace(fold=1).utcoffset():
        return _unknown(ClassificationReason.AMBIGUOUS_LOCAL_TIME)

    day = eastern.date()
    if not (
        SCHEDULE_METADATA.first_supported_date
        <= day
        <= SCHEDULE_METADATA.last_supported_date
    ):
        return _unknown(ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE)
    if today is not None and schedule_is_expired(today):
        return _unknown(ClassificationReason.SCHEDULE_EXPIRED)

    return _classify_wall_clock(day, eastern.time())


def is_regular_session(moment: datetime, *, today: date | None = None) -> bool:
    """The one question most callers need. False whenever unsure."""
    return classify(moment, today=today).is_regular_session


__all__ = [
    "EARLY_CLOSE_DAYS",
    "EARLY_REGULAR_CLOSE",
    "EASTERN",
    "FULL_HOLIDAYS",
    "POST_MARKET_CLOSE",
    "PRE_MARKET_OPEN",
    "REGULAR_CLOSE",
    "REGULAR_OPEN",
    "SCHEDULE_METADATA",
    "UNKNOWN_REASONS",
    "CalendarSession",
    "ClassificationReason",
    "DayKind",
    "ExchangeCalendarError",
    "ScheduleMetadata",
    "SessionClassification",
    "classify",
    "is_regular_session",
    "regular_session_bounds",
    "schedule_is_expired",
    "schedule_metadata",
    "schedule_review_is_overdue",
    "supported_range",
    "validate_schedule",
]
