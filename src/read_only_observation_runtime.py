"""Real read-only broker observation runtime for Milestone 9.

`read_only_broker_state` defines *what a snapshot means*. This module is what
actually produces one from a real broker, and it is deliberately the narrowest
possible thing that can do so.

Three properties are structural rather than promised:

1. **The capability surface is a partition, not a filter.** Every advertised
   broker tool is classified in `MCP_TOOL_CLASSIFICATION`. Only
   `READ_ONLY_PERMITTED` may ever be invoked. An operation whose write
   capability is *ambiguous* is classified `AMBIGUOUS_CAPABILITY` and is
   prohibited exactly as strictly as a proven write.
2. **The reader has no write-shaped method.** `RestrictedReadOnlyReader`
   exposes three read methods and nothing else — no placement, no cancellation,
   no replacement, no strategy activation, no generic `call_tool` passthrough,
   and no path that accepts a tool name from a caller or from data. The three
   tool names are module-level literals.
3. **Nothing write-capable is imported.** This module imports no adapter, no
   client, no session, and no MCP module, so there is no write-capable object
   in reach to fall back to. A test asserts this by parsing the file.

Absent capability fails closed: a reader that cannot perform a required read
produces `UNAVAILABLE`, never an assumed-safe default.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from src.exchange_calendar import (
    SCHEDULE_METADATA,
    CalendarSession,
    classify as classify_calendar_session,
)
from src.live_order_state_model import normalize_state
from src.read_only_broker_state import (
    DEFAULT_FRESHNESS_POLICY,
    READ_ONLY_CALL_CEILINGS,
    BrokerStateAssessment,
    BrokerStateDecision,
    CashAvailability,
    MarketSession,
    ObservationOutcome,
    PositionClassification,
    ProtectionObservation,
    ReadAvailability,
    ReadOnlyBrokerSnapshot,
    ReadOnlyObservationPlan,
    SnapshotFreshness,
    SnapshotFreshnessPolicy,
    StateProvenance,
    approved_active_protection_states,
    decide_broker_state,
    proposed_state_promotion,
    validate_snapshot_facts,
)


EASTERN = ZoneInfo("America/New_York")


class ReadOnlyRuntimeError(RuntimeError):
    """Any refusal by this runtime. Never carries broker text."""


class ReadOnlyCapabilityError(ReadOnlyRuntimeError):
    """An operation outside the permitted read-only surface was requested."""


# --------------------------------------------------------------------------
# Capability classification of the entire advertised tool surface.
# --------------------------------------------------------------------------


class ToolCapability(str, Enum):
    """How an advertised broker operation is treated by this runtime."""

    #: Proven read-only and required by this checkpoint. May be invoked.
    READ_ONLY_PERMITTED = "READ_ONLY_PERMITTED"
    #: Read-only, but not needed to ground broker state. Not invoked.
    UNNECESSARY = "UNNECESSARY"
    #: Write capability is not *proven absent*. Treated as a write.
    AMBIGUOUS_CAPABILITY = "AMBIGUOUS_CAPABILITY"
    #: Proven to mutate broker-side state.
    WRITE_CAPABLE = "WRITE_CAPABLE"


#: Capabilities that may never be invoked by this runtime. Ambiguity is
#: grouped with proven writes on purpose: "we could not establish that this
#: is read-only" and "this writes" must lead to the same behaviour, because
#: the cost of being wrong is identical.
PROHIBITED_CAPABILITIES: frozenset[ToolCapability] = frozenset({
    ToolCapability.UNNECESSARY,
    ToolCapability.AMBIGUOUS_CAPABILITY,
    ToolCapability.WRITE_CAPABLE,
})


def _classify(names: Sequence[str], capability: ToolCapability) -> dict[str, ToolCapability]:
    return {name: capability for name in names}


#: Every tool advertised by the reviewed broker tool surface, classified.
#: This is a total partition: a tool absent from this mapping is refused, so
#: a newly advertised operation cannot be reached until it is reviewed here.
MCP_TOOL_CLASSIFICATION: Mapping[str, ToolCapability] = MappingProxyType({
    **_classify(
        (
            # Establishes that the commissioned account exists, is active, and
            # is the one this operator intends. Carries no balance field.
            "get_accounts",
            # Establishes held quantity by symbol.
            "get_equity_positions",
            # Establishes resting and terminal order state, including any
            # protective stop that already exists.
            "get_equity_orders",
        ),
        ToolCapability.READ_ONLY_PERMITTED,
    ),
    **_classify(
        (
            # Broker-side evaluation of an order payload. Non-committing by
            # description, but this repository has never proven that it leaves
            # no broker-side artifact, and a checkpoint whose entire claim is
            # "zero writes" cannot rest on a description.
            "review_equity_order",
            "review_option_order",
            # Executes a saved scan; may persist run state or last-run markers.
            "run_scan",
            # Touches an account-upgrade flow rather than plain account data.
            "get_option_level_upgrade_info",
        ),
        ToolCapability.AMBIGUOUS_CAPABILITY,
    ),
    **_classify(
        (
            "add_option_to_watchlist",
            "add_to_watchlist",
            "cancel_equity_order",
            "cancel_option_order",
            "create_scan",
            "create_watchlist",
            "follow_watchlist",
            "place_equity_order",
            "place_option_order",
            "remove_from_watchlist",
            "remove_option_from_watchlist",
            "unfollow_watchlist",
            "update_scan_config",
            "update_scan_filters",
            "update_watchlist",
        ),
        ToolCapability.WRITE_CAPABLE,
    ),
    **_classify(
        (
            "get_earnings_calendar",
            "get_earnings_results",
            "get_equity_fundamentals",
            "get_equity_historicals",
            "get_equity_price_book",
            "get_equity_quotes",
            "get_equity_tax_lots",
            "get_equity_technical_indicators",
            "get_equity_tradability",
            "get_financials",
            "get_index_quotes",
            "get_indexes",
            "get_option_chains",
            "get_option_instruments",
            "get_option_orders",
            "get_option_positions",
            "get_option_quotes",
            "get_option_watchlist",
            "get_pnl_trade_history",
            "get_popular_watchlists",
            "get_portfolio",
            "get_realized_pnl",
            "get_scanner_filter_specs",
            "get_scans",
            "get_watchlist_items",
            "get_watchlists",
            "search",
        ),
        ToolCapability.UNNECESSARY,
    ),
})


#: The only tool names this runtime may pass to a session, as literals.
PERMITTED_READ_TOOLS: tuple[str, ...] = (
    "get_accounts",
    "get_equity_positions",
    "get_equity_orders",
)

_ACCOUNTS_TOOL = "get_accounts"
_POSITIONS_TOOL = "get_equity_positions"
_ORDERS_TOOL = "get_equity_orders"


def permitted_read_tools() -> frozenset[str]:
    """Derived rather than restated, so the partition stays authoritative."""
    return frozenset(
        name
        for name, capability in MCP_TOOL_CLASSIFICATION.items()
        if capability is ToolCapability.READ_ONLY_PERMITTED
    )


def assert_read_only_tool(tool_name: Any) -> str:
    """Fail closed on anything that is not an explicitly permitted read."""
    if not isinstance(tool_name, str):
        raise ReadOnlyCapabilityError("tool name is not a string")
    capability = MCP_TOOL_CLASSIFICATION.get(tool_name)
    if capability is None:
        raise ReadOnlyCapabilityError("tool is not classified")
    if capability is not ToolCapability.READ_ONLY_PERMITTED:
        raise ReadOnlyCapabilityError("tool is outside the read-only surface")
    return tool_name


# --------------------------------------------------------------------------
# Why cash availability is not read here.
# --------------------------------------------------------------------------

#: `get_accounts` carries no cash, balance, or buying-power field — its record
#: is identity and status only. The tool that would carry one, `get_portfolio`,
#: is classified UNNECESSARY for this checkpoint and is not invoked: grounding
#: active protection does not require knowing the balance, and reading a
#: balance is one more sensitive value in reach for no decision benefit.
#:
#: So cash availability is reported UNKNOWN, honestly. `decide_broker_state`
#: does not gate on it, and UNKNOWN is never treated as sufficient.
CASH_AVAILABILITY_IS_NOT_READ = CashAvailability.UNKNOWN


# --------------------------------------------------------------------------
# Market session, delegated to the authoritative exchange calendar.
# --------------------------------------------------------------------------
#
# This module used to carry its own private copy of the holiday and
# early-close tables. Two copies of the same data is one copy too many: they
# were kept in step only by a temporary drift guard in the calendar's test
# module, which existed solely because this file is digest-pinned and could
# not be edited while the real-account checkpoint was held.
#
# That checkpoint has run, so the duplication is retired here. There is now
# exactly one table, in `src/exchange_calendar.py`, which validates itself at
# import and carries its own provenance and review date.

#: Where a `market_session` value comes from and what it does not cover.
#: Every field is derived from `SCHEDULE_METADATA` rather than restated, so a
#: calendar extension cannot leave a stale claim behind in evidence.
CALENDAR_SOURCE = "authoritative_static_exchange_calendar"
CALENDAR_SUPPORTED_RANGE = (
    f"{SCHEDULE_METADATA.supported_years[0]}-{SCHEDULE_METADATA.supported_years[-1]}"
)
CALENDAR_LIMITATION = (
    "no_authoritative_feed_expires_after_"
    f"{SCHEDULE_METADATA.last_supported_date.year}"
)

#: The last date the calendar can classify. Past it every classification is
#: UNKNOWN — the table cannot silently age into supporting a later year.
CALENDAR_EXPIRES_AFTER: date = SCHEDULE_METADATA.last_supported_date


#: Calendar session -> snapshot session. Pre- and post-market both collapse to
#: EXTENDED_HOURS because this snapshot does not distinguish them and inventing
#: a distinction it does not use would be one more thing to keep true.
_SESSION_BY_CALENDAR_SESSION: Mapping[CalendarSession, MarketSession] = (
    MappingProxyType({
        CalendarSession.REGULAR: MarketSession.REGULAR_HOURS,
        CalendarSession.PRE_MARKET: MarketSession.EXTENDED_HOURS,
        CalendarSession.POST_MARKET: MarketSession.EXTENDED_HOURS,
        CalendarSession.CLOSED: MarketSession.CLOSED,
        CalendarSession.UNKNOWN: MarketSession.UNKNOWN,
    })
)


def calendar_metadata() -> dict[str, str]:
    """Provenance of every `market_session` value this runtime produces."""
    return {
        "calendar_source": CALENDAR_SOURCE,
        "calendar_supported_range": CALENDAR_SUPPORTED_RANGE,
        "calendar_limitation": CALENDAR_LIMITATION,
        "calendar_expires_after": CALENDAR_EXPIRES_AFTER.isoformat(),
    }


def classify_market_session(moment: datetime) -> MarketSession:
    """Delegate to the authoritative calendar. No broker or market-data read.

    Every fail-closed property of the previous private table is preserved by
    the calendar and is stricter in one respect: a wall time that does not name
    a single instant across a DST fold is `UNKNOWN` here, where the old table
    would have classified it.

    Unsupported, expired, malformed, ambiguous, and naive inputs are all
    UNKNOWN. Weekends and full holidays are CLOSED. Half-days end the regular
    session at 13:00 ET and read CLOSED afterwards rather than EXTENDED_HOURS:
    post-close extended trading does exist on those days, but the table does
    not model its bounds, and reporting a session it cannot bound would be the
    same class of error the early-close gap was.

    A calendar session this mapping does not recognise is UNKNOWN, so a future
    `CalendarSession` member cannot reach a caller as a tradeable answer before
    it is reviewed here.
    """
    classification = classify_calendar_session(moment)
    return _SESSION_BY_CALENDAR_SESSION.get(
        classification.session, MarketSession.UNKNOWN
    )


# --------------------------------------------------------------------------
# Sanitized read results. Nothing raw crosses this boundary.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedAccount:
    identity_match: bool
    eligible_count: int


@dataclass(frozen=True)
class ObservedPosition:
    symbol: str
    quantity: Decimal


@dataclass(frozen=True)
class ObservedOrder:
    #: Broker order identifier. Held **in memory only**, for one purpose: to
    #: tell whether the same order was seen in both reads of a cycle. It is
    #: never persisted, never logged, never bucketed into evidence, and never
    #: reaches a snapshot — `ReadOnlyBrokerSnapshot` has no field capable of
    #: carrying it, and a test asserts it never appears on disk.
    identity: str
    symbol: str
    side: str
    order_type: str
    state: str
    is_stop: bool


def _mapping(value: Any, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadOnlyRuntimeError(message)
    return value


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ReadOnlyRuntimeError("broker text field is not a string")
    return value


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ReadOnlyRuntimeError("broker decimal field is not a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReadOnlyRuntimeError("broker decimal field is invalid") from exc
    if not parsed.is_finite():
        raise ReadOnlyRuntimeError("broker decimal field is non-finite")
    return parsed


def _envelope(result: Any) -> Mapping[str, Any]:
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise ReadOnlyRuntimeError("broker read was rejected")
    structured = getattr(result, "structuredContent", None)
    value = structured if structured is not None else getattr(result, "data", result)
    if isinstance(value, Mapping) and set(value) in ({"data"}, {"data", "guide"}):
        value = value["data"]
    payload = _mapping(value, "broker read response is not an object")
    if payload.get("next") not in (None, ""):
        raise ReadOnlyRuntimeError("pagination is prohibited")
    return payload


# --------------------------------------------------------------------------
# The capability-restricted reader.
# --------------------------------------------------------------------------


class ReadOnlyBrokerReader(Protocol):
    """The only broker capability this runtime knows how to ask for.

    Three reads. There is no placement, cancellation, replacement, strategy
    activation, or arbitrary-invocation member to implement, so a conforming
    object cannot offer the runtime a way to write.
    """

    async def read_accounts(self) -> ObservedAccount: ...

    async def read_positions(self) -> tuple[ObservedPosition, ...]: ...

    async def read_orders(self) -> tuple[ObservedOrder, ...]: ...


class RestrictedReadOnlyReader:
    """Wraps one session and can express exactly three calls.

    The wrapped session is held in `_session` and is **never exposed as a
    public attribute**. This is deliberate and was a review finding: while
    nothing in this runtime reaches through a reader to the session, a public
    handle would put a generic write-capable call one attribute access away
    from any future caller. A private name does not stop a determined caller
    — nothing in Python does — but it removes the accidental path and makes
    the intent unambiguous.

    It is a plain class rather than a dataclass so the private field cannot be
    reintroduced as a public init parameter or leak through `repr`.

    Tool names are module constants, not parameters and not data — there is no
    code path where a caller-supplied or response-supplied string becomes a
    tool name. Each permitted read may be performed at most `call_ceilings[tool]`
    times, so even a looping caller cannot amplify the surface.

    The ceiling is **per tool**, not uniform. `get_equity_orders` is allowed two
    calls so the cycle can confirm nothing moved between them; the other two
    reads stay at one and cannot gain a second call as a side effect.
    """

    __slots__ = ("_session", "account_number", "call_ceilings", "call_counts")

    def __init__(
        self,
        session: Any,
        account_number: str,
        call_ceilings: Mapping[str, int] = READ_ONLY_CALL_CEILINGS,
        call_counts: dict[str, int] | None = None,
    ) -> None:
        if session is None or not callable(getattr(session, "call_tool", None)):
            raise ReadOnlyRuntimeError("one injected session is required")
        if not isinstance(account_number, str) or not account_number.strip():
            raise ReadOnlyRuntimeError("one account identity is required")
        if not isinstance(call_ceilings, Mapping) or not call_ceilings:
            raise ReadOnlyRuntimeError("per-tool call ceilings are required")
        for tool, ceiling in call_ceilings.items():
            assert_read_only_tool(tool)
            if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 1:
                raise ReadOnlyRuntimeError(
                    "per-tool call ceiling must be a positive int"
                )
        self._session = session
        self.account_number = account_number
        self.call_ceilings: Mapping[str, int] = dict(call_ceilings)
        self.call_counts: dict[str, int] = dict(call_counts or {})

    def __repr__(self) -> str:
        # Never renders the session: a repr lands in logs and tracebacks.
        return (
            f"RestrictedReadOnlyReader(reads={sorted(self.call_counts)}, "
            f"call_ceilings={sorted(self.call_ceilings.items())})"
        )

    @property
    def write_attempts(self) -> int:
        """Structurally zero: no method here can reach a write tool."""
        return 0

    def _reserve(self, tool_name: str) -> None:
        """Charge one read against this tool's own ceiling, or refuse.

        A tool with no configured ceiling gets none — absent capability fails
        closed rather than defaulting to one free call.
        """
        allowed = assert_read_only_tool(tool_name)
        ceiling = self.call_ceilings.get(allowed)
        if ceiling is None:
            raise ReadOnlyCapabilityError("tool has no configured call ceiling")
        used = self.call_counts.get(allowed, 0)
        if used >= ceiling:
            raise ReadOnlyCapabilityError("read call ceiling reached")
        self.call_counts[allowed] = used + 1

    @staticmethod
    def _settle(result: Any) -> Mapping[str, Any]:
        return _envelope(result)

    async def read_accounts(self) -> ObservedAccount:
        # The tool name is a module-level constant at the call site, not a
        # parameter: there is no code path in which a caller-supplied or
        # response-supplied string reaches `call_tool`. The three read methods
        # below are the only call sites in this file.
        self._reserve(_ACCOUNTS_TOOL)
        try:
            result = await self._session.call_tool(_ACCOUNTS_TOOL, {})
        except Exception as exc:
            raise ReadOnlyRuntimeError("broker read failed") from exc
        payload = self._settle(result)
        rows = payload.get("accounts")
        if not isinstance(rows, list):
            raise ReadOnlyRuntimeError("accounts response is not an array")
        eligible: list[Mapping[str, Any]] = []
        for row in rows:
            record = _mapping(row, "account record is not an object")
            for key in ("agentic_allowed", "deactivated", "permanently_deactivated"):
                if not isinstance(record.get(key), bool):
                    raise ReadOnlyRuntimeError("account record is schema-incomplete")
            if not isinstance(record.get("account_number"), str):
                raise ReadOnlyRuntimeError("account record is schema-incomplete")
            if (
                record.get("agentic_allowed") is True
                and _text(record, "state") == "active"
                and record.get("deactivated") is not True
                and record.get("permanently_deactivated") is not True
            ):
                eligible.append(record)
        matched = sum(
            1 for record in eligible if record.get("account_number") == self.account_number
        )
        return ObservedAccount(
            identity_match=len(eligible) == 1 and matched == 1,
            eligible_count=len(eligible),
        )

    async def read_positions(self) -> tuple[ObservedPosition, ...]:
        self._reserve(_POSITIONS_TOOL)
        try:
            result = await self._session.call_tool(
                _POSITIONS_TOOL, {"account_number": self.account_number}
            )
        except Exception as exc:
            raise ReadOnlyRuntimeError("broker read failed") from exc
        payload = self._settle(result)
        rows = payload.get("positions")
        if not isinstance(rows, list):
            raise ReadOnlyRuntimeError("positions response is not an array")
        observed: list[ObservedPosition] = []
        for row in rows:
            record = _mapping(row, "position record is not an object")
            symbol = _text(record, "symbol").strip().upper()
            if not symbol:
                raise ReadOnlyRuntimeError("position symbol is empty")
            observed.append(ObservedPosition(symbol, _decimal(record.get("quantity"))))
        return tuple(observed)

    async def read_orders(self) -> tuple[ObservedOrder, ...]:
        self._reserve(_ORDERS_TOOL)
        try:
            result = await self._session.call_tool(
                _ORDERS_TOOL, {"account_number": self.account_number}
            )
        except Exception as exc:
            raise ReadOnlyRuntimeError("broker read failed") from exc
        payload = self._settle(result)
        rows = payload.get("orders")
        if not isinstance(rows, list):
            raise ReadOnlyRuntimeError("orders response is not an array")
        observed: list[ObservedOrder] = []
        for row in rows:
            record = _mapping(row, "order record is not an object")
            symbol = _text(record, "symbol").strip().upper()
            if not symbol:
                raise ReadOnlyRuntimeError("order symbol is empty")
            trigger = _text(record, "trigger").strip().lower()
            base_type = _text(record, "type").strip().lower()
            is_stop = trigger == "stop"
            identity = _text(record, "id").strip()
            if not identity:
                raise ReadOnlyRuntimeError("order identifier is empty")
            observed.append(ObservedOrder(
                identity=identity,
                symbol=symbol,
                side=_text(record, "side").strip().lower(),
                order_type=f"stop_{base_type}" if is_stop else base_type,
                state=_text(record, "state").strip().lower(),
                is_stop=is_stop,
            ))
        return tuple(observed)


# --------------------------------------------------------------------------
# Attribution.
# --------------------------------------------------------------------------

#: Order states that can still execute against the position. Anything not
#: listed as terminal is treated as potentially executable, which is the
#: conservative direction: an unrecognized state must not read as "harmless".
TERMINAL_ORDER_STATES: frozenset[str] = frozenset({
    "filled",
    "cancelled",
    "canceled",
    "rejected",
    "failed",
    "voided",
    "expired",
})


#: States this repository has seen named by the broker, whether or not they
#: are approved for protection. A state outside this set is reported as
#: UNKNOWN rather than quietly folded into "unapproved".
KNOWN_ORDER_STATES: frozenset[str] = frozenset({
    "new", "queued", "unconfirmed", "confirmed", "partially_filled",
    "pending_cancelled", "partially_filled_rest_cancelled",
}) | TERMINAL_ORDER_STATES


# --------------------------------------------------------------------------
# F2: confirming that the order set did not move mid-cycle.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderProjection:
    """The comparable shape of one orders read, attributed to one symbol.

    Deliberately a *projection*, never the raw payload. Two broker responses
    differ constantly in fields nothing here depends on — timestamps, cursors,
    fee breakdowns — and comparing them raw would report change on every
    cycle while telling a reviewer nothing. This compares only what a decision
    is actually drawn from.

    It holds order identifiers, so it is an in-memory value: it is never
    persisted, and the only thing derived from it that escapes is a single
    boolean.
    """

    #: (identifier, normalized state) for each related order. A set, so read
    #: ordering cannot masquerade as change.
    identities: frozenset[tuple[str, str]]
    executable_sell_count: int
    protective_candidate_count: int


def project_related_orders(
    symbol: str, orders: Sequence[ObservedOrder]
) -> OrderProjection:
    """Project one orders read down to what the decision depends on.

    Attribution is by exact normalized symbol, matching `attribute_observations`
    — so a change on an unrelated symbol is not this cycle's business and does
    not register as instability.
    """
    target = symbol.strip().upper()
    if not target:
        raise ReadOnlyRuntimeError("attribution symbol is empty")
    related = [record for record in orders if record.symbol == target]
    live = [record for record in related if record.state not in TERMINAL_ORDER_STATES]
    executable_sells = [record for record in live if record.side == "sell"]
    return OrderProjection(
        identities=frozenset(
            (record.identity, normalize_state(record.state)) for record in related
        ),
        executable_sell_count=len(executable_sells),
        protective_candidate_count=sum(
            1 for record in executable_sells if record.is_stop
        ),
    )


@dataclass(frozen=True)
class AttributionResult:
    position: PositionClassification
    related_open_order_count: int
    executable_related_sell_count: int
    protective_candidate_count: int
    protection: ProtectionObservation
    ambiguous: bool
    #: Set only when exactly one protective candidate existed.
    protective_state: str | None = None


def attribute_observations(
    *,
    symbol: str,
    expected_quantity: Decimal,
    positions: Sequence[ObservedPosition],
    orders: Sequence[ObservedOrder],
) -> AttributionResult:
    """Attribute state to one symbol strictly, or declare the result ambiguous.

    Attribution is by exact normalized symbol. Nothing is inferred from
    ordering, recency, quantity coincidence, or "the only order there is" —
    an unattributable observation is reported ambiguous rather than assigned
    to the symbol we happened to be asking about.
    """
    target = symbol.strip().upper()
    if not target:
        raise ReadOnlyRuntimeError("attribution symbol is empty")
    if not isinstance(expected_quantity, Decimal) or expected_quantity <= 0:
        raise ReadOnlyRuntimeError("expected quantity must be a positive Decimal")

    matching = [record for record in positions if record.symbol == target]
    if len(matching) > 1:
        # The broker reported the same symbol twice. Which one is current is
        # not knowable from here.
        return AttributionResult(
            position=PositionClassification.CONTRADICTORY,
            related_open_order_count=0,
            executable_related_sell_count=0,
            protective_candidate_count=0,
            protection=ProtectionObservation.NOT_OBSERVED,
            ambiguous=True,
        )
    if not matching:
        position = PositionClassification.FLAT
    else:
        quantity = matching[0].quantity
        if quantity < 0:
            position = PositionClassification.SHORT
        elif quantity == 0:
            position = PositionClassification.FLAT
        elif quantity == expected_quantity:
            position = PositionClassification.LONG_EXPECTED_QUANTITY
        else:
            position = PositionClassification.LONG_UNEXPECTED_QUANTITY

    related = [record for record in orders if record.symbol == target]
    live = [record for record in related if record.state not in TERMINAL_ORDER_STATES]
    executable_sells = [record for record in live if record.side == "sell"]
    protective = [record for record in executable_sells if record.is_stop]

    approved = approved_active_protection_states()
    protective_state: str | None = None
    if not protective:
        protection = ProtectionObservation.NOT_OBSERVED
    elif len(protective) > 1:
        protection = ProtectionObservation.AMBIGUOUS_CANDIDATES
    else:
        protective_state = normalize_state(protective[0].state)
        if protective_state in approved:
            protection = ProtectionObservation.OBSERVED_APPROVED_STATE
        elif protective_state in TERMINAL_ORDER_STATES:
            protection = ProtectionObservation.OBSERVED_TERMINAL
        elif protective_state in KNOWN_ORDER_STATES:
            protection = ProtectionObservation.OBSERVED_UNAPPROVED_STATE
        else:
            protection = ProtectionObservation.OBSERVED_UNKNOWN_STATE

    return AttributionResult(
        position=position,
        related_open_order_count=len(live),
        executable_related_sell_count=len(executable_sells),
        protective_candidate_count=len(protective),
        protection=protection,
        ambiguous=False,
        protective_state=protective_state,
    )


# --------------------------------------------------------------------------
# Bounded snapshot acquisition.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionResult:
    snapshot: ReadOnlyBrokerSnapshot
    assessment: BrokerStateAssessment
    attribution_ambiguous: bool
    #: The single protective candidate's normalized state, when exactly one
    #: existed. Never a broker identifier, price, or symbol.
    observed_protection_state: str | None = None


async def acquire_read_only_snapshot(
    reader: ReadOnlyBrokerReader,
    *,
    symbol: str,
    expected_quantity: Decimal,
    monotonic: Callable[[], float],
    now: Callable[[], datetime],
    freshness_policy: SnapshotFreshnessPolicy = DEFAULT_FRESHNESS_POLICY,
) -> AcquisitionResult:
    """One coherent cycle: account, positions, orders, confirm, session.

    Every read is bounded: one attempt each, and orders twice by design. There
    is no *retry* — a retry would widen the read span and produce a snapshot
    describing a moment that never existed, which is precisely what the
    freshness policy exists to reject. A failed component is recorded
    UNAVAILABLE and the cycle continues, so the decision sees the whole picture
    including the hole.

    The second orders read is a **confirmation**, not a retry: it runs after a
    successful first read and exists to answer "did this move while I was
    looking?". Detecting change and observing one coherent moment pull against
    each other — the fourth read widens the span the freshness policy caps —
    and the resolution is that the span cap still wins. Four reads that do not
    fit inside it are `SPAN_TOO_WIDE`, which is the correct answer.

    On detected change this reports and stops. It never re-reads until the
    numbers agree: that would consume an unbounded number of calls against a
    genuinely churning account, and deciding whether to back off and try again
    is the unattended scheduler's judgement, not an observation primitive's.
    """
    first = monotonic()
    session_start = classify_market_session(now())

    account_read = ReadAvailability.UNAVAILABLE
    position_read = ReadAvailability.UNAVAILABLE
    order_read = ReadAvailability.UNAVAILABLE

    identity_match = False
    positions: tuple[ObservedPosition, ...] = ()
    orders: tuple[ObservedOrder, ...] = ()
    order_set_changed = False

    # Only a *recognized* read failure degrades a component to UNAVAILABLE.
    # A capability violation and an unrecognized exception both escape: the
    # first means this runtime reached outside its surface, the second means
    # it has a bug, and neither may be quietly relabelled "the broker was
    # unavailable" — that label reads as a normal, expected condition.
    try:
        account = await reader.read_accounts()
        identity_match = account.identity_match
        account_read = (
            ReadAvailability.AVAILABLE if identity_match else ReadAvailability.UNAVAILABLE
        )
    except ReadOnlyCapabilityError:
        raise
    except ReadOnlyRuntimeError:
        account_read = ReadAvailability.UNAVAILABLE

    try:
        positions = tuple(await reader.read_positions())
        position_read = ReadAvailability.AVAILABLE
    except ReadOnlyCapabilityError:
        raise
    except ReadOnlyRuntimeError:
        position_read = ReadAvailability.UNAVAILABLE

    try:
        orders = tuple(await reader.read_orders())
        # The confirmation read. A failure here degrades the order component
        # to UNAVAILABLE rather than being quietly treated as "no change":
        # not knowing whether the order set moved is not the same as knowing
        # it did not, and only one of those is safe to act on.
        confirmation = tuple(await reader.read_orders())
        order_read = ReadAvailability.AVAILABLE
        order_set_changed = project_related_orders(
            symbol, orders
        ) != project_related_orders(symbol, confirmation)
    except ReadOnlyCapabilityError:
        raise
    except ReadOnlyRuntimeError:
        order_read = ReadAvailability.UNAVAILABLE
        orders = ()
        order_set_changed = False

    last = monotonic()
    session_end = classify_market_session(now())
    session_changed = session_start is not session_end
    if session_changed:
        market_session = MarketSession.CHANGED_DURING_ACQUISITION
        session_read = ReadAvailability.AVAILABLE
    elif session_start is MarketSession.UNKNOWN:
        market_session = MarketSession.UNKNOWN
        session_read = ReadAvailability.UNAVAILABLE
    else:
        market_session = session_start
        session_read = ReadAvailability.AVAILABLE

    if position_read is ReadAvailability.AVAILABLE and order_read is ReadAvailability.AVAILABLE:
        attribution = attribute_observations(
            symbol=symbol,
            expected_quantity=expected_quantity,
            positions=positions,
            orders=orders,
        )
    else:
        attribution = AttributionResult(
            position=PositionClassification.UNKNOWN,
            related_open_order_count=0,
            executable_related_sell_count=0,
            protective_candidate_count=0,
            protection=ProtectionObservation.NOT_OBSERVED,
            ambiguous=False,
        )

    snapshot = ReadOnlyBrokerSnapshot(
        account_read=account_read,
        position_read=position_read,
        order_read=order_read,
        session_read=session_read,
        cash_availability=CASH_AVAILABILITY_IS_NOT_READ,
        position=attribution.position,
        market_session=market_session,
        related_open_order_count=attribution.related_open_order_count,
        executable_related_sell_count=attribution.executable_related_sell_count,
        protective_candidate_count=attribution.protective_candidate_count,
        protection=attribution.protection,
        first_read_monotonic=first,
        last_read_monotonic=last,
        evaluated_monotonic=monotonic(),
        # Now populated from the actual A/B comparison, not from the market
        # session. Before F2 this field carried the session signal, which made
        # its name assert something the code never measured.
        order_state_changed_during_acquisition=order_set_changed,
        position_response_self_contradictory=attribution.ambiguous,
        market_session_changed_during_acquisition=session_changed,
    )
    return AcquisitionResult(
        snapshot=snapshot,
        assessment=decide_broker_state(snapshot, policy=freshness_policy),
        attribution_ambiguous=attribution.ambiguous,
        observed_protection_state=attribution.protective_state,
    )


# --------------------------------------------------------------------------
# Checkpoint outcomes.
# --------------------------------------------------------------------------


class CheckpointOutcome(str, Enum):
    """Every way the read-only checkpoint can end. There is no other exit."""

    #: A protective order existed and its state was recorded for later review.
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    #: The cycle succeeded and no protective order existed. A valid result.
    NO_PROTECTIVE_ORDER_OBSERVED = "NO_PROTECTIVE_ORDER_OBSERVED"
    #: More than one protective candidate; which one protects is not knowable.
    PROTECTION_AMBIGUOUS = "PROTECTION_AMBIGUOUS"
    #: State could not be attributed to the observed symbol.
    ATTRIBUTION_AMBIGUOUS = "ATTRIBUTION_AMBIGUOUS"
    #: A required read was unavailable or the account identity did not match.
    BROKER_READ_UNAVAILABLE = "BROKER_READ_UNAVAILABLE"
    #: The reads did not describe one coherent moment.
    SNAPSHOT_NOT_FRESH = "SNAPSHOT_NOT_FRESH"
    #: The operator declined, or did not affirmatively approve.
    DECLINED_BY_OPERATOR = "DECLINED_BY_OPERATOR"
    #: An authorization gate was malformed, reused, or out of policy.
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    #: Run identity or preconditions were rejected before any read.
    PRECONDITION_REJECTED = "PRECONDITION_REJECTED"
    #: The runtime refused an operation outside the read-only surface.
    CAPABILITY_VIOLATION = "CAPABILITY_VIOLATION"
    #: The broker session could not be opened, or did not close cleanly.
    #: Distinct from BROKER_READ_UNAVAILABLE on purpose: "no session" and
    #: "session but the read failed" call for different operator action.
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    #: An internal invariant failed. Never interpreted as a safe result.
    INTERNAL_INVARIANT_FAILURE = "INTERNAL_INVARIANT_FAILURE"


#: Outcomes representing a completed observation cycle. Note that *neither*
#: approves a state: recording an observation is not approving it.
COMPLETED_OUTCOMES: frozenset[CheckpointOutcome] = frozenset({
    CheckpointOutcome.OBSERVATION_RECORDED,
    CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED,
})


# --------------------------------------------------------------------------
# Authorization.
# --------------------------------------------------------------------------

READ_ONLY_AUTHORIZATION_FLAG = "AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION"
ACKNOWLEDGMENT_STAGE = "acknowledgment"
OBSERVATION_STAGE = "observation"
AUTHORIZATION_STAGES: tuple[str, ...] = (ACKNOWLEDGMENT_STAGE, OBSERVATION_STAGE)

ACKNOWLEDGMENT_PROMPT = "Confirm this checkpoint performs broker reads only? [y/N] "
OBSERVATION_PROMPT = "Authorize the read-only observation cycle? [y/N] "

#: The only inputs that approve. Everything else — empty, whitespace, "yes",
#: "Y ", a stray newline, EOF — declines. Default-decline is the point.
APPROVAL_INPUTS: frozenset[str] = frozenset({"y", "Y"})


def is_approval(supplied: Any) -> bool:
    return isinstance(supplied, str) and supplied in APPROVAL_INPUTS


@dataclass
class PreResolvedAuthorization:
    """Replays decisions that were already made, once each.

    This exists so a launcher can obtain interactive approval **before**
    opening a broker session. Previously the session was opened first and the
    operator was prompted inside it, which meant a decline had already
    authenticated a session it never used — avoidable, and a review finding.

    It records decisions; it cannot manufacture one. A stage with no recorded
    decision declines.
    """

    decisions: Mapping[str, bool]
    _used: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        for stage in self.decisions:
            if stage not in AUTHORIZATION_STAGES:
                raise ReadOnlyRuntimeError("authorization stage is invalid")

    @property
    def all_approved(self) -> bool:
        return all(
            self.decisions.get(stage) is True for stage in AUTHORIZATION_STAGES
        )

    def __call__(self, stage: str) -> bool:
        if stage not in AUTHORIZATION_STAGES:
            raise ReadOnlyRuntimeError("authorization stage is invalid")
        if stage in self._used:
            raise ReadOnlyRuntimeError("authorization is not reusable")
        self._used.add(stage)
        return self.decisions.get(stage) is True


@dataclass
class ReadOnlyAuthorizationGate:
    """Two independent, single-use, default-decline gates.

    Each stage may be asked exactly once. A second request for the same stage
    is a rejection, not a re-prompt — otherwise a caller could retry until it
    got the answer it wanted.
    """

    secret_reader: Callable[[str], str]
    _used: set[str] = field(default_factory=set, init=False)

    def __call__(self, stage: str) -> bool:
        if stage not in AUTHORIZATION_STAGES:
            raise ReadOnlyRuntimeError("authorization stage is invalid")
        if stage in self._used:
            raise ReadOnlyRuntimeError("authorization is not reusable")
        self._used.add(stage)
        prompt = (
            ACKNOWLEDGMENT_PROMPT
            if stage == ACKNOWLEDGMENT_STAGE
            else OBSERVATION_PROMPT
        )
        try:
            supplied = self.secret_reader(prompt)
        except Exception:
            # An unreadable prompt is a decline. It is never an approval.
            return False
        return is_approval(supplied)


# --------------------------------------------------------------------------
# Sanitized evidence.
# --------------------------------------------------------------------------

#: Superset of the commissioning term list. A test asserts it stays a
#: superset, so the read-only path can never become the laxer of the two.
PROHIBITED_EVIDENCE_TERMS: tuple[str, ...] = (
    "account_number", "fingerprint", "order_id", "ref_id", "balance",
    "buying_power", "fill_price", "stop_price", "raw_response", "token",
    "cookie", "header", "oauth", "credential", "symbol", "quantity",
    "price", "instrument", "nickname",
)


def validate_read_only_evidence(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(term in normalized for term in PROHIBITED_EVIDENCE_TERMS):
                raise ReadOnlyRuntimeError(f"prohibited evidence field at {path}")
            validate_read_only_evidence(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            validate_read_only_evidence(child, f"{path}[{index}]")
        return
    if value is not None and not isinstance(value, (str, bool, int, float)):
        raise ReadOnlyRuntimeError(f"unsupported evidence scalar at {path}")


@dataclass
class ReadOnlyObservationEvidence:
    """What a checkpoint run records. Counters are asserted, not narrated."""

    run_id: str
    outcome: str = CheckpointOutcome.INTERNAL_INVARIANT_FAILURE.value
    decision: str = BrokerStateDecision.UNKNOWN_BROKER_STATE.value
    freshness: str = SnapshotFreshness.UNKNOWN.value
    snapshot_facts: dict[str, str] = field(default_factory=dict)
    observed_protection_state: str = "NONE"
    proposed_provenance: str = "NONE"
    read_call_counts: dict[str, int] = field(default_factory=dict)
    approved_active_protection_state_count: int = 0
    #: Provenance of `snapshot_facts["market_session"]`, so a reviewer never
    #: has to infer where a session classification came from.
    calendar_source: str = CALENDAR_SOURCE
    calendar_supported_range: str = CALENDAR_SUPPORTED_RANGE
    calendar_limitation: str = CALENDAR_LIMITATION
    calendar_expires_after: str = CALENDAR_EXPIRES_AFTER.isoformat()
    write_counters: dict[str, int] = field(default_factory=lambda: {
        "placement_writes": 0,
        "cancellation_writes": 0,
        "replacement_writes": 0,
        "strategy_activations": 0,
    })
    operator_action_required: str = "NONE"


#: The value recorded when the broker names a state this repository has never
#: seen. Every other evidence field is a fixed enum, count bucket, or flag;
#: without this, an arbitrary broker string would be the one free-form channel
#: into a persisted file a human is expected to read and act on.
UNRECOGNIZED_ORDER_STATE = "UNRECOGNIZED"


def recordable_order_state(state: str | None) -> str:
    """Constrain an observed state to the known vocabulary, or say so.

    Approved states are folded in dynamically so that promoting a state later
    cannot make it unrecordable here.
    """
    if not isinstance(state, str) or not state:
        return "NONE"
    normalized = normalize_state(state)
    permitted = KNOWN_ORDER_STATES | approved_active_protection_states()
    if normalized in permitted:
        return normalized
    return UNRECOGNIZED_ORDER_STATE


#: The counters that must be zero for a run to be a read-only run at all.
REQUIRED_ZERO_COUNTERS: tuple[str, ...] = (
    "placement_writes",
    "cancellation_writes",
    "replacement_writes",
    "strategy_activations",
)


def assert_zero_write_evidence(evidence: ReadOnlyObservationEvidence) -> None:
    counters = evidence.write_counters
    if set(counters) != set(REQUIRED_ZERO_COUNTERS):
        raise ReadOnlyRuntimeError("write counter set is not the required set")
    for name in REQUIRED_ZERO_COUNTERS:
        value = counters[name]
        if not isinstance(value, int) or isinstance(value, bool) or value != 0:
            raise ReadOnlyRuntimeError(f"{name} must be exactly zero")
    for tool in evidence.read_call_counts:
        assert_read_only_tool(tool)


class ReadOnlyEvidenceStore:
    """One run, one directory, written once, atomically.

    A partial file is never left behind for a reviewer to mistake for a
    result: the payload is written to a temporary name and renamed into
    place, and a run ID that already has a directory is refused rather than
    appended to.
    """

    filename = "read_only_observation_evidence.json"

    def __init__(self, root: Path, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
            raise ReadOnlyRuntimeError("run ID is not a safe path component")
        if any(character in run_id for character in "/\\:*?\"<>|") or run_id != run_id.strip():
            raise ReadOnlyRuntimeError("run ID is not a safe path component")
        self.root = root
        self.run_id = run_id
        self.completed = root / run_id

    def start(self) -> None:
        if self.completed.exists():
            raise ReadOnlyRuntimeError("run ID already used")
        self.root.mkdir(parents=True, exist_ok=True)
        self.completed.mkdir()

    def finalize(self, evidence: ReadOnlyObservationEvidence) -> Path:
        assert_zero_write_evidence(evidence)
        payload = asdict(evidence)
        validate_read_only_evidence(payload)
        validate_snapshot_facts(evidence.snapshot_facts)
        target = self.completed / self.filename
        temporary = self.completed / f"{self.filename}.tmp"
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target


# --------------------------------------------------------------------------
# The checkpoint runner.
# --------------------------------------------------------------------------


#: How this run is authorized. Attended means a person answers two prompts;
#: unattended means an operator issued a manifest in advance. Unattended is
#: the approved destination, so "no operator at the keyboard" must be a
#: first-class, validated mode — not a check quietly skipped.
ATTENDED_MODE = "INTERACTIVE_ATTENDED"
UNATTENDED_MODE = "MANIFEST_UNATTENDED"
AUTHORIZATION_MODES: frozenset[str] = frozenset({ATTENDED_MODE, UNATTENDED_MODE})


@dataclass(frozen=True)
class ReadOnlyCheckpointRequest:
    run_id: str
    account_number: str
    symbol: str
    expected_quantity: Decimal
    evidence_root: Path
    authorization_flag: str
    operator_present: bool
    authorization_mode: str = ATTENDED_MODE
    plan: ReadOnlyObservationPlan = field(default_factory=ReadOnlyObservationPlan)

    def validate(self) -> None:
        if self.authorization_flag != READ_ONLY_AUTHORIZATION_FLAG:
            raise ReadOnlyRuntimeError("read-only authorization flag is absent")
        if self.authorization_mode not in AUTHORIZATION_MODES:
            raise ReadOnlyRuntimeError("authorization mode is invalid")
        if self.authorization_mode == ATTENDED_MODE and not self.operator_present:
            # Only the attended mode needs a person: its approval *is* the
            # person. The unattended mode's approval is a manifest an operator
            # issued earlier, validated and consumed before any read.
            raise ReadOnlyRuntimeError("attended mode requires an operator present")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ReadOnlyRuntimeError("one observed symbol is required")
        if not isinstance(self.expected_quantity, Decimal) or self.expected_quantity <= 0:
            raise ReadOnlyRuntimeError("expected quantity must be a positive Decimal")
        if not isinstance(self.account_number, str) or not self.account_number.strip():
            raise ReadOnlyRuntimeError("one account identity is required")
        if set(self.plan.tools) != set(PERMITTED_READ_TOOLS):
            raise ReadOnlyRuntimeError("plan tools are not the permitted read set")
        for name in ("order_placements", "order_cancellations", "order_replacements"):
            if getattr(self.plan, name) != 0:
                raise ReadOnlyRuntimeError(f"{name} must be zero")
        if self.plan.manufactures_observation:
            raise ReadOnlyRuntimeError("an observation may never be manufactured")


@dataclass(frozen=True)
class CheckpointResult:
    outcome: CheckpointOutcome
    evidence: ReadOnlyObservationEvidence
    evidence_path: Path | None


def _classify_outcome(result: AcquisitionResult) -> CheckpointOutcome:
    """Availability, then coherence, then attribution, then interpretation.

    The order is the safety property. Reading a protection state out of an
    unavailable, stale, or unattributable snapshot would be worse than
    reporting nothing, because it would look like a result.
    """
    decision = result.assessment.decision
    if decision in (
        BrokerStateDecision.BROKER_READ_UNAVAILABLE,
        BrokerStateDecision.INCOMPLETE_BROKER_STATE,
    ):
        return CheckpointOutcome.BROKER_READ_UNAVAILABLE
    if (
        decision is BrokerStateDecision.STALE_BROKER_STATE
        or result.assessment.freshness is not SnapshotFreshness.FRESH
    ):
        return CheckpointOutcome.SNAPSHOT_NOT_FRESH
    if result.attribution_ambiguous:
        return CheckpointOutcome.ATTRIBUTION_AMBIGUOUS
    protection = result.snapshot.protection
    if protection is ProtectionObservation.AMBIGUOUS_CANDIDATES:
        return CheckpointOutcome.PROTECTION_AMBIGUOUS
    if protection is ProtectionObservation.NOT_OBSERVED:
        return CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    return CheckpointOutcome.OBSERVATION_RECORDED


def _propose_provenance(result: AcquisitionResult, outcome: CheckpointOutcome) -> str:
    """The most one sighting earns. Never approval — asserted, not trusted."""
    if outcome is not CheckpointOutcome.OBSERVATION_RECORDED:
        return "NONE"
    state = result.observed_protection_state
    if state is None:
        return "NONE"
    try:
        provenance = proposed_state_promotion(
            ObservationOutcome.OBSERVED_PENDING_REVIEW, state
        )
    except Exception:
        # An unrecognized state is recorded as observed, not promoted.
        return "NONE"
    if provenance is StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION:
        raise ReadOnlyRuntimeError("observation may never yield approval")
    return provenance.value


def record_session_failure(
    request: ReadOnlyCheckpointRequest,
    *,
    phase: str,
    existing: CheckpointResult | None = None,
) -> CheckpointResult:
    """Turn a session-open or session-close failure into a recorded outcome.

    No traceback, no transport text, no exception message — only the phase,
    which is one of two fixed strings. Evidence is written when the run
    identity is still unused (a failed open). After a completed checkpoint the
    evidence file already exists and is immutable by design, so a close
    failure keeps the observation that was recorded and reports the failure
    through the outcome and exit code instead of rewriting history.
    """
    if phase not in {"open", "close"}:
        raise ReadOnlyRuntimeError("session failure phase is invalid")
    action = "INVESTIGATE_SESSION_OPEN" if phase == "open" else "INVESTIGATE_SESSION_CLOSE"

    if existing is not None:
        existing.evidence.operator_action_required = action
        return CheckpointResult(
            CheckpointOutcome.SESSION_UNAVAILABLE,
            existing.evidence,
            existing.evidence_path,
        )

    evidence = ReadOnlyObservationEvidence(run_id=str(request.run_id))
    evidence.outcome = CheckpointOutcome.SESSION_UNAVAILABLE.value
    evidence.operator_action_required = action
    evidence.approved_active_protection_state_count = len(
        approved_active_protection_states()
    )
    try:
        store = ReadOnlyEvidenceStore(request.evidence_root, request.run_id)
        store.start()
        path = store.finalize(evidence)
    except Exception:
        # A run identity already spent, or an unwritable evidence root. The
        # outcome still stands; it simply has nowhere durable to live.
        return CheckpointResult(CheckpointOutcome.SESSION_UNAVAILABLE, evidence, None)
    return CheckpointResult(CheckpointOutcome.SESSION_UNAVAILABLE, evidence, path)


async def run_read_only_observation_checkpoint(
    request: ReadOnlyCheckpointRequest,
    *,
    reader_factory: Callable[[], ReadOnlyBrokerReader],
    authorization: ReadOnlyAuthorizationGate,
    monotonic: Callable[[], float],
    now: Callable[[], datetime],
    freshness_policy: SnapshotFreshnessPolicy = DEFAULT_FRESHNESS_POLICY,
) -> CheckpointResult:
    """Run one attended read-only observation cycle and record what it saw.

    This function performs reads and writes evidence to disk. It performs no
    broker write of any kind, and it cannot: the reader it is handed exposes
    only read methods, and every tool name it can reach is a literal in
    `PERMITTED_READ_TOOLS`.

    It approves nothing. `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is not
    read from, written to, or influenced here — promotion to approved is a
    later human decision made against the recorded evidence.
    """
    evidence = ReadOnlyObservationEvidence(run_id=str(request.run_id))
    evidence.approved_active_protection_state_count = len(
        approved_active_protection_states()
    )

    def finish(
        outcome: CheckpointOutcome,
        store: ReadOnlyEvidenceStore | None,
        *,
        action: str = "NONE",
    ) -> CheckpointResult:
        evidence.outcome = outcome.value
        evidence.operator_action_required = action
        if store is None:
            return CheckpointResult(outcome, evidence, None)
        try:
            path = store.finalize(evidence)
        except Exception:
            evidence.outcome = CheckpointOutcome.INTERNAL_INVARIANT_FAILURE.value
            return CheckpointResult(
                CheckpointOutcome.INTERNAL_INVARIANT_FAILURE, evidence, None
            )
        return CheckpointResult(outcome, evidence, path)

    try:
        request.validate()
        store = ReadOnlyEvidenceStore(request.evidence_root, request.run_id)
        store.start()
    except Exception:
        return finish(
            CheckpointOutcome.PRECONDITION_REJECTED, None, action="REVIEW_REQUEST"
        )

    try:
        if not authorization(ACKNOWLEDGMENT_STAGE):
            return finish(CheckpointOutcome.DECLINED_BY_OPERATOR, store)
        if not authorization(OBSERVATION_STAGE):
            return finish(CheckpointOutcome.DECLINED_BY_OPERATOR, store)
    except Exception:
        return finish(CheckpointOutcome.AUTHORIZATION_REJECTED, store)

    try:
        reader = reader_factory()
        result = await acquire_read_only_snapshot(
            reader,
            symbol=request.symbol,
            expected_quantity=request.expected_quantity,
            monotonic=monotonic,
            now=now,
            freshness_policy=freshness_policy,
        )
    except ReadOnlyCapabilityError:
        return finish(
            CheckpointOutcome.CAPABILITY_VIOLATION, store, action="INVESTIGATE"
        )
    except Exception:
        return finish(
            CheckpointOutcome.INTERNAL_INVARIANT_FAILURE, store, action="INVESTIGATE"
        )

    counts = dict(getattr(reader, "call_counts", {}) or {})
    try:
        for tool in counts:
            assert_read_only_tool(tool)
    except ReadOnlyCapabilityError:
        return finish(
            CheckpointOutcome.CAPABILITY_VIOLATION, store, action="INVESTIGATE"
        )

    outcome = _classify_outcome(result)
    evidence.decision = result.assessment.decision.value
    evidence.freshness = result.assessment.freshness.value
    evidence.snapshot_facts = result.snapshot.sanitized_facts()
    evidence.read_call_counts = counts
    if outcome is CheckpointOutcome.OBSERVATION_RECORDED:
        evidence.observed_protection_state = recordable_order_state(
            result.observed_protection_state
        )
    try:
        evidence.proposed_provenance = _propose_provenance(result, outcome)
    except ReadOnlyRuntimeError:
        return finish(
            CheckpointOutcome.INTERNAL_INVARIANT_FAILURE, store, action="INVESTIGATE"
        )
    return finish(
        outcome,
        store,
        action="NONE" if outcome in COMPLETED_OUTCOMES else "REVIEW_OBSERVATION",
    )


__all__ = [
    "ACKNOWLEDGMENT_PROMPT",
    "ACKNOWLEDGMENT_STAGE",
    "APPROVAL_INPUTS",
    "ATTENDED_MODE",
    "AUTHORIZATION_MODES",
    "AUTHORIZATION_STAGES",
    "UNATTENDED_MODE",
    "AcquisitionResult",
    "AttributionResult",
    "COMPLETED_OUTCOMES",
    "CheckpointOutcome",
    "CheckpointResult",
    "ReadOnlyCheckpointRequest",
    "run_read_only_observation_checkpoint",
    "EASTERN",
    "KNOWN_ORDER_STATES",
    "MCP_TOOL_CLASSIFICATION",
    "OBSERVATION_PROMPT",
    "OBSERVATION_STAGE",
    "ObservedAccount",
    "ObservedOrder",
    "ObservedPosition",
    "OrderProjection",
    "project_related_orders",
    "CALENDAR_EXPIRES_AFTER",
    "CALENDAR_LIMITATION",
    "CALENDAR_SOURCE",
    "CALENDAR_SUPPORTED_RANGE",
    "PERMITTED_READ_TOOLS",
    "PROHIBITED_CAPABILITIES",
    "PreResolvedAuthorization",
    "UNRECOGNIZED_ORDER_STATE",
    "calendar_metadata",
    "record_session_failure",
    "recordable_order_state",
    "PROHIBITED_EVIDENCE_TERMS",
    "READ_ONLY_AUTHORIZATION_FLAG",
    "REQUIRED_ZERO_COUNTERS",
    "ReadOnlyAuthorizationGate",
    "ReadOnlyBrokerReader",
    "ReadOnlyCapabilityError",
    "ReadOnlyEvidenceStore",
    "ReadOnlyObservationEvidence",
    "ReadOnlyRuntimeError",
    "RestrictedReadOnlyReader",
    "TERMINAL_ORDER_STATES",
    "ToolCapability",
    "acquire_read_only_snapshot",
    "assert_read_only_tool",
    "assert_zero_write_evidence",
    "attribute_observations",
    "classify_market_session",
    "is_approval",
    "permitted_read_tools",
    "validate_read_only_evidence",
]
