"""Real read-only observation runtime (Milestone 9).

Every test is fake-only. Nothing here opens a session, reaches a broker, or
touches the network — and the module under test is proven, by parsing it, to
contain no path that could.

The properties that matter most, in order:

1. no write tool can be reached, however the runtime is driven;
2. an operation whose write capability is merely *ambiguous* is refused as
   strictly as a proven write;
3. no observation, however clean, approves a protection state.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import pathlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.read_only_observation_runtime as runtime
from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
from src.one_share_commissioning import (
    PROHIBITED_EVIDENCE_TERMS as COMMISSIONING_PROHIBITED_TERMS,
)
from src.read_only_broker_state import (
    BrokerStateDecision,
    CashAvailability,
    MarketSession,
    PositionClassification,
    ProtectionObservation,
    ReadAvailability,
    SnapshotFreshness,
    StateProvenance,
)
from src.read_only_observation_runtime import (
    EASTERN,
    MCP_TOOL_CLASSIFICATION,
    PERMITTED_READ_TOOLS,
    PROHIBITED_EVIDENCE_TERMS,
    REQUIRED_ZERO_COUNTERS,
    ACKNOWLEDGMENT_STAGE,
    OBSERVATION_STAGE,
    READ_ONLY_AUTHORIZATION_FLAG,
    CheckpointOutcome,
    ObservedOrder,
    ObservedPosition,
    ReadOnlyAuthorizationGate,
    ReadOnlyCapabilityError,
    ReadOnlyCheckpointRequest,
    ReadOnlyEvidenceStore,
    ReadOnlyObservationEvidence,
    ReadOnlyRuntimeError,
    RestrictedReadOnlyReader,
    ToolCapability,
    acquire_read_only_snapshot,
    assert_read_only_tool,
    assert_zero_write_evidence,
    attribute_observations,
    classify_market_session,
    is_approval,
    permitted_read_tools,
    run_read_only_observation_checkpoint,
    validate_read_only_evidence,
)

MODULE_PATH = pathlib.Path(runtime.__file__)
LAUNCHER_PATH = MODULE_PATH.parent.parent / "read_only_observation_checkpoint_launcher.py"

ACCOUNT = "ACCT-FAKE-1"
SYMBOL = "AAPL"


# --------------------------------------------------------------------------
# Fakes. None of these is a broker; all are local dictionaries.
# --------------------------------------------------------------------------


def account_row(number: str = ACCOUNT, **overrides: object) -> dict[str, object]:
    row = {
        "account_number": number,
        "state": "active",
        "agentic_allowed": True,
        "deactivated": False,
        "permanently_deactivated": False,
    }
    row.update(overrides)
    return row


def order_row(**overrides: object) -> dict[str, object]:
    row = {
        "symbol": SYMBOL,
        "side": "sell",
        "type": "market",
        "trigger": "stop",
        "state": "confirmed",
    }
    row.update(overrides)
    return row


class FakeSession:
    """Records every tool name it is asked for. Returns canned payloads."""

    def __init__(self, payloads: dict[str, object] | None = None) -> None:
        self.payloads = payloads or {}
        self.requested: list[str] = []
        self.failures: set[str] = set()

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.requested.append(name)
        if name in self.failures:
            raise RuntimeError("broker unavailable")
        return SimpleNamespace(structuredContent={"data": self.payloads.get(name, {})})


def default_payloads(
    *,
    positions: list[dict[str, object]] | None = None,
    orders: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "get_accounts": {"accounts": [account_row()]},
        "get_equity_positions": {"positions": positions if positions is not None else []},
        "get_equity_orders": {"orders": orders if orders is not None else []},
    }


class StubReader:
    """A reader that returns fixed observations, or fails a chosen read."""

    def __init__(self, *, account=True, positions=(), orders=(), fail: set[str] = frozenset()):
        self._account = account
        self._positions = tuple(positions)
        self._orders = tuple(orders)
        self._fail = set(fail)
        self.call_counts: dict[str, int] = {}

    async def read_accounts(self):
        self.call_counts["get_accounts"] = self.call_counts.get("get_accounts", 0) + 1
        if "accounts" in self._fail:
            raise ReadOnlyRuntimeError("broker read failed")
        return runtime.ObservedAccount(identity_match=self._account, eligible_count=1)

    async def read_positions(self):
        self.call_counts["get_equity_positions"] = (
            self.call_counts.get("get_equity_positions", 0) + 1
        )
        if "positions" in self._fail:
            raise ReadOnlyRuntimeError("broker read failed")
        return self._positions

    async def read_orders(self):
        self.call_counts["get_equity_orders"] = (
            self.call_counts.get("get_equity_orders", 0) + 1
        )
        if "orders" in self._fail:
            raise ReadOnlyRuntimeError("broker read failed")
        return self._orders


class Ticks:
    """Deterministic monotonic clock."""

    def __init__(self, step: float = 0.1) -> None:
        self.value = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


REGULAR_HOURS = datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN)


def approving_gate() -> ReadOnlyAuthorizationGate:
    return ReadOnlyAuthorizationGate(secret_reader=lambda prompt: "y")


def make_request(tmp_path, run_id: str = "m9-obs-0001") -> ReadOnlyCheckpointRequest:
    return ReadOnlyCheckpointRequest(
        run_id=run_id,
        account_number=ACCOUNT,
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        evidence_root=tmp_path / "evidence",
        authorization_flag=READ_ONLY_AUTHORIZATION_FLAG,
        operator_present=True,
    )


def run_checkpoint(request, reader, *, gate=None, step: float = 0.1):
    return asyncio.run(
        run_read_only_observation_checkpoint(
            request,
            reader_factory=lambda: reader,
            authorization=gate or approving_gate(),
            monotonic=Ticks(step),
            now=lambda: REGULAR_HOURS,
        )
    )


# --------------------------------------------------------------------------
# 1. The capability partition.
# --------------------------------------------------------------------------


def test_every_advertised_tool_is_classified_exactly_once():
    names = list(MCP_TOOL_CLASSIFICATION)
    assert len(names) == len(set(names))
    assert all(isinstance(value, ToolCapability) for value in MCP_TOOL_CLASSIFICATION.values())


def test_only_three_tools_are_permitted_and_the_tuple_agrees_with_the_partition():
    assert permitted_read_tools() == frozenset(PERMITTED_READ_TOOLS)
    assert permitted_read_tools() == {
        "get_accounts",
        "get_equity_positions",
        "get_equity_orders",
    }


@pytest.mark.parametrize(
    "tool",
    [
        "place_equity_order",
        "cancel_equity_order",
        "place_option_order",
        "cancel_option_order",
        "create_watchlist",
        "update_scan_config",
        "add_to_watchlist",
        "remove_from_watchlist",
        "follow_watchlist",
        "unfollow_watchlist",
    ],
)
def test_known_mutating_tools_are_classified_write_capable(tool):
    assert MCP_TOOL_CLASSIFICATION[tool] is ToolCapability.WRITE_CAPABLE


@pytest.mark.parametrize(
    "tool", ["review_equity_order", "review_option_order", "run_scan"]
)
def test_unproven_tools_are_ambiguous_not_permitted(tool):
    """Ambiguity is not a middle ground. It is refused like a write."""
    assert MCP_TOOL_CLASSIFICATION[tool] is ToolCapability.AMBIGUOUS_CAPABILITY
    with pytest.raises(ReadOnlyCapabilityError):
        assert_read_only_tool(tool)


def test_review_equity_order_is_refused_even_though_commissioning_allows_it():
    """The write path may use review; a zero-write checkpoint may not.

    This is a deliberate divergence, not an oversight: commissioning accepts
    review because it is about to write anyway, and this checkpoint's entire
    claim is that it never does.
    """
    from src.robinhood_commissioning_adapter import ALLOWED_COMMISSIONING_TOOLS

    assert "review_equity_order" in ALLOWED_COMMISSIONING_TOOLS
    assert "review_equity_order" not in permitted_read_tools()


def test_every_non_permitted_classified_tool_is_refused():
    refused = 0
    for name, capability in MCP_TOOL_CLASSIFICATION.items():
        if capability is ToolCapability.READ_ONLY_PERMITTED:
            assert assert_read_only_tool(name) == name
            continue
        with pytest.raises(ReadOnlyCapabilityError):
            assert_read_only_tool(name)
        refused += 1
    assert refused == len(MCP_TOOL_CLASSIFICATION) - 3


def test_an_unclassified_tool_is_refused_rather_than_defaulted():
    """A newly advertised operation cannot be reached before review."""
    with pytest.raises(ReadOnlyCapabilityError):
        assert_read_only_tool("replace_equity_order")
    with pytest.raises(ReadOnlyCapabilityError):
        assert_read_only_tool("some_future_tool")


@pytest.mark.parametrize("value", [None, 1, b"get_accounts", ["get_accounts"], object()])
def test_a_non_string_tool_name_is_refused(value):
    with pytest.raises(ReadOnlyCapabilityError):
        assert_read_only_tool(value)


def test_the_permitted_set_matches_the_plan_designed_in_the_offline_module():
    from src.read_only_broker_state import READ_ONLY_CHECKPOINT_TOOLS

    assert frozenset(READ_ONLY_CHECKPOINT_TOOLS) == permitted_read_tools()


# --------------------------------------------------------------------------
# 2. Structural zero-write proof.
# --------------------------------------------------------------------------


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_runtime_defines_no_write_shaped_method():
    """Not "does not call a write" — has no member that could express one."""
    forbidden = ("place", "cancel", "replace", "submit", "activate", "modify")
    for node in ast.walk(_tree(MODULE_PATH)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            assert not any(word in lowered for word in forbidden), node.name


def test_the_reader_exposes_exactly_three_broker_capabilities():
    public = {
        name
        for name in dir(RestrictedReadOnlyReader)
        if not name.startswith("_")
        and callable(getattr(RestrictedReadOnlyReader, name, None))
    }
    assert public == {"read_accounts", "read_positions", "read_orders"}


def test_the_reader_has_no_generic_invocation_passthrough():
    reader = RestrictedReadOnlyReader(session=FakeSession(), account_number=ACCOUNT)
    assert not hasattr(reader, "call_tool")
    assert not hasattr(reader, "invoke")
    assert reader.write_attempts == 0


def test_write_tool_names_appear_only_inside_the_classification_literal():
    """The only textual matches are denylist entries, not call sites."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    classification_source = inspect.getsource(runtime).split(
        "PERMITTED_READ_TOOLS: tuple[str, ...]"
    )[0]
    for name, capability in MCP_TOOL_CLASSIFICATION.items():
        if capability is ToolCapability.READ_ONLY_PERMITTED:
            continue
        assert source.count(f'"{name}"') == classification_source.count(f'"{name}"')


def test_the_runtime_imports_nothing_write_capable():
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator", "commissioning")
    for node in ast.walk(_tree(MODULE_PATH)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not any(word in name.lower() for word in forbidden), name


def test_the_runtime_has_exactly_three_call_sites_all_naming_a_read_literally():
    """No dynamic tool-name execution anywhere in the module.

    The repository-wide guard already forbids dynamic call sites; this pins
    the stronger local property — three sites, each resolving to a permitted
    read at parse time.
    """
    from tests.call_site_guard import LITERAL, analyze_source

    sites = analyze_source(
        MODULE_PATH.read_text(encoding="utf-8"),
        path="src/read_only_observation_runtime.py",
    )
    assert len(sites) == 3
    assert {site.kind for site in sites} == {LITERAL}
    assert {site.tool for site in sites} == set(PERMITTED_READ_TOOLS)


def test_the_launcher_never_constructs_a_write_capable_adapter():
    source = LAUNCHER_PATH.read_text(encoding="utf-8")
    assert "RobinhoodCommissioningAdapter" not in source
    assert "place_equity_order" not in source
    assert "cancel_equity_order" not in source
    assert "RestrictedReadOnlyReader" in source


def test_the_launcher_does_not_reach_transport_code_at_import_time():
    """Importing the launcher must be safe; the session import is deferred."""
    module_level = {
        node.module
        for node in _tree(LAUNCHER_PATH).body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("session" in name for name in module_level)


# --------------------------------------------------------------------------
# 3. The restricted reader against a fake session.
# --------------------------------------------------------------------------


def test_the_reader_requires_a_session_and_an_account():
    with pytest.raises(ReadOnlyRuntimeError):
        RestrictedReadOnlyReader(session=None, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        RestrictedReadOnlyReader(session=object(), account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        RestrictedReadOnlyReader(session=FakeSession(), account_number="  ")


@pytest.mark.parametrize("ceiling", [0, -1, True, 1.5, "1"])
def test_the_reader_rejects_a_nonsensical_call_ceiling(ceiling):
    with pytest.raises(ReadOnlyRuntimeError):
        RestrictedReadOnlyReader(
            session=FakeSession(), account_number=ACCOUNT, maximum_calls_per_tool=ceiling
        )


def test_the_reader_asks_only_for_permitted_tools():
    session = FakeSession(default_payloads())
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    asyncio.run(reader.read_accounts())
    asyncio.run(reader.read_positions())
    asyncio.run(reader.read_orders())
    assert set(session.requested) == set(PERMITTED_READ_TOOLS)
    assert len(session.requested) == 3


def test_each_read_is_bounded_to_one_call():
    session = FakeSession(default_payloads())
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    asyncio.run(reader.read_accounts())
    with pytest.raises(ReadOnlyCapabilityError):
        asyncio.run(reader.read_accounts())
    assert session.requested == ["get_accounts"]


def test_identity_mismatch_is_reported_not_raised():
    session = FakeSession({"get_accounts": {"accounts": [account_row("OTHER")]}})
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    observed = asyncio.run(reader.read_accounts())
    assert observed.identity_match is False


def test_more_than_one_eligible_account_is_not_a_match():
    session = FakeSession(
        {"get_accounts": {"accounts": [account_row(), account_row("SECOND")]}}
    )
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    assert asyncio.run(reader.read_accounts()).identity_match is False


def test_a_schema_incomplete_account_record_is_refused():
    session = FakeSession({"get_accounts": {"accounts": [{"account_number": ACCOUNT}]}})
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        asyncio.run(reader.read_accounts())


def test_a_rejected_broker_read_is_refused():
    class Rejecting(FakeSession):
        async def call_tool(self, name, arguments):
            self.requested.append(name)
            return SimpleNamespace(is_error=True)

    reader = RestrictedReadOnlyReader(session=Rejecting(), account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        asyncio.run(reader.read_accounts())


def test_pagination_is_refused_rather_than_partially_read():
    session = FakeSession()
    session.payloads["get_equity_orders"] = {"orders": [], "next": "cursor"}
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        asyncio.run(reader.read_orders())


def test_transport_failure_does_not_leak_broker_text():
    session = FakeSession(default_payloads())
    session.failures.add("get_equity_positions")
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError) as caught:
        asyncio.run(reader.read_positions())
    assert "broker unavailable" not in str(caught.value)


def test_orders_are_parsed_into_sanitized_records():
    session = FakeSession(
        default_payloads(orders=[order_row(), order_row(trigger="immediate", type="limit")])
    )
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    orders = asyncio.run(reader.read_orders())
    assert orders[0] == ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)
    assert orders[1].order_type == "limit"
    assert orders[1].is_stop is False


def test_a_position_with_a_blank_symbol_is_refused():
    session = FakeSession(default_payloads(positions=[{"symbol": "  ", "quantity": "1"}]))
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        asyncio.run(reader.read_positions())


@pytest.mark.parametrize("quantity", ["", "abc", "NaN", "Infinity", None, 1])
def test_a_non_decimal_quantity_is_refused(quantity):
    session = FakeSession(
        default_payloads(positions=[{"symbol": SYMBOL, "quantity": quantity}])
    )
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    with pytest.raises(ReadOnlyRuntimeError):
        asyncio.run(reader.read_positions())


# --------------------------------------------------------------------------
# 4. Market session, from the calendar equivalent.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment,expected",
    [
        (datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN), MarketSession.REGULAR_HOURS),
        (datetime(2026, 8, 3, 9, 29, tzinfo=EASTERN), MarketSession.EXTENDED_HOURS),
        (datetime(2026, 8, 3, 16, 0, tzinfo=EASTERN), MarketSession.EXTENDED_HOURS),
        (datetime(2026, 8, 3, 3, 59, tzinfo=EASTERN), MarketSession.CLOSED),
        (datetime(2026, 8, 3, 20, 0, tzinfo=EASTERN), MarketSession.CLOSED),
        (datetime(2026, 8, 1, 10, 0, tzinfo=EASTERN), MarketSession.CLOSED),
        (datetime(2026, 12, 25, 10, 0, tzinfo=EASTERN), MarketSession.CLOSED),
        (datetime(2026, 7, 3, 10, 0, tzinfo=EASTERN), MarketSession.CLOSED),
    ],
)
def test_market_session_classification(moment, expected):
    assert classify_market_session(moment) is expected


def test_a_year_outside_the_calendar_table_is_unknown_not_open():
    """Beyond the modelled years the answer is UNKNOWN, never 'probably open'."""
    assert (
        classify_market_session(datetime(2030, 8, 5, 10, 0, tzinfo=EASTERN))
        is MarketSession.UNKNOWN
    )


@pytest.mark.parametrize("value", [datetime(2026, 8, 3, 10, 0), None, "10:00"])
def test_an_unusable_timestamp_is_unknown(value):
    assert classify_market_session(value) is MarketSession.UNKNOWN


# --------------------------------------------------------------------------
# 5. Attribution.
# --------------------------------------------------------------------------


def test_flat_with_no_orders():
    result = attribute_observations(
        symbol=SYMBOL, expected_quantity=Decimal("1"), positions=[], orders=[]
    )
    assert result.position is PositionClassification.FLAT
    assert result.protection is ProtectionObservation.NOT_OBSERVED
    assert result.ambiguous is False


def test_another_symbols_position_is_not_attributed_to_this_one():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[ObservedPosition("MSFT", Decimal("5"))],
        orders=[ObservedOrder("MSFT", "sell", "stop_market", "confirmed", True)],
    )
    assert result.position is PositionClassification.FLAT
    assert result.related_open_order_count == 0
    assert result.protective_candidate_count == 0


def test_a_duplicated_symbol_is_contradictory_and_ambiguous():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[
            ObservedPosition(SYMBOL, Decimal("1")),
            ObservedPosition(SYMBOL, Decimal("2")),
        ],
        orders=[],
    )
    assert result.position is PositionClassification.CONTRADICTORY
    assert result.ambiguous is True


@pytest.mark.parametrize(
    "quantity,expected",
    [
        (Decimal("1"), PositionClassification.LONG_EXPECTED_QUANTITY),
        (Decimal("2"), PositionClassification.LONG_UNEXPECTED_QUANTITY),
        (Decimal("0"), PositionClassification.FLAT),
        (Decimal("-1"), PositionClassification.SHORT),
    ],
)
def test_quantity_classification(quantity, expected):
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[ObservedPosition(SYMBOL, quantity)],
        orders=[],
    )
    assert result.position is expected


def test_terminal_orders_are_not_counted_as_live():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[],
        orders=[
            ObservedOrder(SYMBOL, "sell", "stop_market", "filled", True),
            ObservedOrder(SYMBOL, "sell", "stop_market", "cancelled", True),
        ],
    )
    assert result.related_open_order_count == 0
    assert result.protective_candidate_count == 0
    assert result.protection is ProtectionObservation.NOT_OBSERVED


def test_an_unrecognized_state_counts_as_live_not_harmless():
    """Conservative direction: unknown must not read as 'already done'."""
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[ObservedPosition(SYMBOL, Decimal("1"))],
        orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "wibble", True)],
    )
    assert result.related_open_order_count == 1
    assert result.protective_candidate_count == 1
    assert result.protection is ProtectionObservation.OBSERVED_UNKNOWN_STATE


def test_two_protective_candidates_are_ambiguous():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[ObservedPosition(SYMBOL, Decimal("1"))],
        orders=[
            ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True),
            ObservedOrder(SYMBOL, "sell", "stop_market", "queued", True),
        ],
    )
    assert result.protection is ProtectionObservation.AMBIGUOUS_CANDIDATES
    assert result.protective_state is None


def test_a_working_stop_is_unapproved_while_nothing_is_approved():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[ObservedPosition(SYMBOL, Decimal("1"))],
        orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
    )
    assert result.protection is ProtectionObservation.OBSERVED_UNAPPROVED_STATE
    assert result.protective_state == "confirmed"


def test_a_non_stop_sell_is_executable_but_not_protective():
    result = attribute_observations(
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        positions=[],
        orders=[ObservedOrder(SYMBOL, "sell", "market", "confirmed", False)],
    )
    assert result.executable_related_sell_count == 1
    assert result.protective_candidate_count == 0


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1"), 1, "1", None])
def test_attribution_requires_a_positive_decimal_expectation(quantity):
    with pytest.raises(ReadOnlyRuntimeError):
        attribute_observations(
            symbol=SYMBOL, expected_quantity=quantity, positions=[], orders=[]
        )


def test_attribution_requires_a_symbol():
    with pytest.raises(ReadOnlyRuntimeError):
        attribute_observations(
            symbol="  ", expected_quantity=Decimal("1"), positions=[], orders=[]
        )


# --------------------------------------------------------------------------
# 6. Snapshot acquisition.
# --------------------------------------------------------------------------


def acquire(reader, *, step: float = 0.1):
    return asyncio.run(
        acquire_read_only_snapshot(
            reader,
            symbol=SYMBOL,
            expected_quantity=Decimal("1"),
            monotonic=Ticks(step),
            now=lambda: REGULAR_HOURS,
        )
    )


def test_a_complete_cycle_produces_a_fresh_flat_snapshot():
    result = acquire(StubReader())
    assert result.snapshot.is_complete
    assert result.assessment.freshness is SnapshotFreshness.FRESH
    assert result.assessment.decision is BrokerStateDecision.VERIFIED_FLAT


def test_cash_is_reported_unknown_because_it_is_never_read():
    """`get_accounts` carries no balance field and `get_portfolio` is refused."""
    result = acquire(StubReader())
    assert result.snapshot.cash_availability is CashAvailability.UNKNOWN
    assert MCP_TOOL_CLASSIFICATION["get_portfolio"] is ToolCapability.UNNECESSARY


@pytest.mark.parametrize("component", ["accounts", "positions", "orders"])
def test_one_failed_read_makes_the_whole_snapshot_unusable(component):
    result = acquire(StubReader(fail={component}))
    assert not result.snapshot.is_complete
    assert result.assessment.decision in {
        BrokerStateDecision.BROKER_READ_UNAVAILABLE,
        BrokerStateDecision.INCOMPLETE_BROKER_STATE,
    }
    assert not result.assessment.is_safe_to_proceed


def test_an_identity_mismatch_marks_the_account_read_unavailable():
    result = acquire(StubReader(account=False))
    assert result.snapshot.account_read is ReadAvailability.UNAVAILABLE
    assert not result.assessment.is_safe_to_proceed


def test_a_wide_read_span_is_rejected_as_incoherent():
    result = acquire(StubReader(), step=2.5)
    assert result.assessment.freshness in {
        SnapshotFreshness.SPAN_TOO_WIDE,
        SnapshotFreshness.STALE,
    }
    assert not result.assessment.is_safe_to_proceed


def test_a_session_change_during_acquisition_is_recorded_and_unsafe():
    moments = iter([
        datetime(2026, 8, 3, 9, 29, 59, tzinfo=EASTERN),
        datetime(2026, 8, 3, 9, 30, 1, tzinfo=EASTERN),
    ])
    result = asyncio.run(
        acquire_read_only_snapshot(
            StubReader(),
            symbol=SYMBOL,
            expected_quantity=Decimal("1"),
            monotonic=Ticks(),
            now=lambda: next(moments),
        )
    )
    assert result.snapshot.market_session is MarketSession.CHANGED_DURING_ACQUISITION
    assert result.snapshot.order_state_changed_during_acquisition is True


def test_an_uncovered_calendar_year_makes_the_session_read_unavailable():
    result = asyncio.run(
        acquire_read_only_snapshot(
            StubReader(),
            symbol=SYMBOL,
            expected_quantity=Decimal("1"),
            monotonic=Ticks(),
            now=lambda: datetime(2030, 8, 5, 10, 0, tzinfo=EASTERN),
        )
    )
    assert result.snapshot.session_read is ReadAvailability.UNAVAILABLE
    assert not result.assessment.is_safe_to_proceed


def test_a_held_position_with_a_stop_is_never_verified_protection_today():
    result = acquire(
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
        )
    )
    assert result.assessment.decision is not BrokerStateDecision.VERIFIED_ACTIVE_PROTECTION
    assert not result.assessment.is_safe_to_proceed


def test_acquisition_reads_each_permitted_tool_exactly_once():
    reader = StubReader()
    acquire(reader)
    assert reader.call_counts == {
        "get_accounts": 1,
        "get_equity_positions": 1,
        "get_equity_orders": 1,
    }


# --------------------------------------------------------------------------
# 7. Authorization.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied", ["", " ", "n", "N", "yes", "YES", "Y ", " y", "\n", "1", "true", None, 1]
)
def test_only_a_bare_y_approves(supplied):
    assert is_approval(supplied) is False


@pytest.mark.parametrize("supplied", ["y", "Y"])
def test_the_two_approving_inputs(supplied):
    assert is_approval(supplied) is True


def test_each_stage_may_be_asked_only_once():
    gate = approving_gate()
    assert gate(ACKNOWLEDGMENT_STAGE) is True
    with pytest.raises(ReadOnlyRuntimeError):
        gate(ACKNOWLEDGMENT_STAGE)


def test_an_unknown_stage_is_refused():
    with pytest.raises(ReadOnlyRuntimeError):
        approving_gate()("entry")


def test_an_unreadable_prompt_declines_rather_than_approves():
    def explode(prompt: str) -> str:
        raise OSError("no console")

    gate = ReadOnlyAuthorizationGate(secret_reader=explode)
    assert gate(ACKNOWLEDGMENT_STAGE) is False


def test_the_two_stages_are_independent():
    answers = iter(["y", "n"])
    gate = ReadOnlyAuthorizationGate(secret_reader=lambda prompt: next(answers))
    assert gate(ACKNOWLEDGMENT_STAGE) is True
    assert gate(OBSERVATION_STAGE) is False


# --------------------------------------------------------------------------
# 8. Evidence.
# --------------------------------------------------------------------------


def test_the_default_write_counters_are_the_required_zero_set():
    evidence = ReadOnlyObservationEvidence(run_id="r")
    assert set(evidence.write_counters) == set(REQUIRED_ZERO_COUNTERS)
    assert_zero_write_evidence(evidence)


@pytest.mark.parametrize("counter", REQUIRED_ZERO_COUNTERS)
def test_any_non_zero_write_counter_is_refused(counter):
    evidence = ReadOnlyObservationEvidence(run_id="r")
    evidence.write_counters[counter] = 1
    with pytest.raises(ReadOnlyRuntimeError):
        assert_zero_write_evidence(evidence)


def test_a_missing_or_extra_counter_is_refused():
    evidence = ReadOnlyObservationEvidence(run_id="r")
    evidence.write_counters.pop("placement_writes")
    with pytest.raises(ReadOnlyRuntimeError):
        assert_zero_write_evidence(evidence)
    evidence = ReadOnlyObservationEvidence(run_id="r")
    evidence.write_counters["extra_writes"] = 0
    with pytest.raises(ReadOnlyRuntimeError):
        assert_zero_write_evidence(evidence)


def test_a_recorded_call_to_a_non_permitted_tool_is_refused():
    evidence = ReadOnlyObservationEvidence(run_id="r")
    evidence.read_call_counts = {"place_equity_order": 1}
    with pytest.raises(ReadOnlyCapabilityError):
        assert_zero_write_evidence(evidence)


def test_the_prohibited_term_list_is_a_superset_of_the_commissioning_one():
    """The read-only path may be stricter. It may never be laxer."""
    assert set(COMMISSIONING_PROHIBITED_TERMS) <= set(PROHIBITED_EVIDENCE_TERMS)


@pytest.mark.parametrize(
    "payload",
    [
        {"account_number": "X"},
        {"symbol": "AAPL"},
        {"stop_price": "1"},
        {"order_id": "1"},
        {"nested": [{"buying_power": 1}]},
        {"token": "t"},
    ],
)
def test_privacy_sensitive_evidence_is_refused(payload):
    with pytest.raises(ReadOnlyRuntimeError):
        validate_read_only_evidence(payload)


def test_an_unsupported_evidence_scalar_is_refused():
    with pytest.raises(ReadOnlyRuntimeError):
        validate_read_only_evidence({"when": datetime.now()})


def test_evidence_is_written_once_atomically(tmp_path):
    store = ReadOnlyEvidenceStore(tmp_path / "root", "run-1")
    store.start()
    evidence = ReadOnlyObservationEvidence(run_id="run-1")
    path = store.finalize(evidence)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["write_counters"] == {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    assert not list(path.parent.glob("*.tmp"))


def test_a_reused_run_id_is_refused(tmp_path):
    store = ReadOnlyEvidenceStore(tmp_path / "root", "run-1")
    store.start()
    with pytest.raises(ReadOnlyRuntimeError):
        ReadOnlyEvidenceStore(tmp_path / "root", "run-1").start()


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b", "a\\b", " x", "x ", "a:b"])
def test_an_unsafe_run_id_is_refused(tmp_path, run_id):
    with pytest.raises(ReadOnlyRuntimeError):
        ReadOnlyEvidenceStore(tmp_path / "root", run_id)


# --------------------------------------------------------------------------
# 9. The checkpoint runner.
# --------------------------------------------------------------------------


def test_a_clean_cycle_with_no_stop_reports_that_and_stops(tmp_path):
    result = run_checkpoint(make_request(tmp_path), StubReader())
    assert result.outcome is CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    assert result.evidence.proposed_provenance == "NONE"
    assert result.evidence_path is not None


def test_an_existing_stop_is_recorded_but_never_approved(tmp_path):
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
        ),
    )
    assert result.outcome is CheckpointOutcome.OBSERVATION_RECORDED
    assert result.evidence.observed_protection_state == "confirmed"
    assert (
        result.evidence.proposed_provenance
        == StateProvenance.READ_ONLY_LIVE_OBSERVATION.value
    )
    assert result.evidence.proposed_provenance != (
        StateProvenance.APPROVED_FOR_ACTIVE_PROTECTION.value
    )


def test_running_the_checkpoint_does_not_change_the_approved_mapping(tmp_path):
    before = frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES)
    run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
        ),
    )
    assert frozenset(ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES) == before
    assert before == frozenset()


def test_two_stops_report_protection_ambiguous(tmp_path):
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[
                ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True),
                ObservedOrder(SYMBOL, "sell", "stop_market", "queued", True),
            ],
        ),
    )
    assert result.outcome is CheckpointOutcome.PROTECTION_AMBIGUOUS
    assert result.evidence.operator_action_required == "REVIEW_OBSERVATION"


def test_a_duplicated_position_reports_attribution_ambiguous(tmp_path):
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[
                ObservedPosition(SYMBOL, Decimal("1")),
                ObservedPosition(SYMBOL, Decimal("2")),
            ]
        ),
    )
    assert result.outcome is CheckpointOutcome.ATTRIBUTION_AMBIGUOUS


def test_a_failed_read_reports_unavailable(tmp_path):
    result = run_checkpoint(make_request(tmp_path), StubReader(fail={"orders"}))
    assert result.outcome is CheckpointOutcome.BROKER_READ_UNAVAILABLE


def test_a_smeared_cycle_reports_not_fresh(tmp_path):
    result = run_checkpoint(make_request(tmp_path), StubReader(), step=2.5)
    assert result.outcome is CheckpointOutcome.SNAPSHOT_NOT_FRESH


def test_a_declined_prompt_stops_before_any_read(tmp_path):
    reader = StubReader()
    gate = ReadOnlyAuthorizationGate(secret_reader=lambda prompt: "n")
    result = run_checkpoint(make_request(tmp_path), reader, gate=gate)
    assert result.outcome is CheckpointOutcome.DECLINED_BY_OPERATOR
    assert reader.call_counts == {}


def test_declining_the_second_prompt_also_stops_before_any_read(tmp_path):
    answers = iter(["y", "n"])
    reader = StubReader()
    gate = ReadOnlyAuthorizationGate(secret_reader=lambda prompt: next(answers))
    result = run_checkpoint(make_request(tmp_path), reader, gate=gate)
    assert result.outcome is CheckpointOutcome.DECLINED_BY_OPERATOR
    assert reader.call_counts == {}


def test_a_broken_authorization_gate_is_rejected_not_assumed(tmp_path):
    class Broken:
        def __call__(self, stage):
            raise ReadOnlyRuntimeError("gate failed")

    result = run_checkpoint(make_request(tmp_path), StubReader(), gate=Broken())
    assert result.outcome is CheckpointOutcome.AUTHORIZATION_REJECTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorization_flag", "SOMETHING_ELSE"),
        ("operator_present", False),
        ("symbol", "  "),
        ("expected_quantity", Decimal("0")),
        ("account_number", ""),
    ],
)
def test_a_bad_request_is_rejected_before_anything_happens(tmp_path, field, value):
    from dataclasses import replace

    reader = StubReader()
    request = replace(make_request(tmp_path), **{field: value})
    result = run_checkpoint(request, reader)
    assert result.outcome is CheckpointOutcome.PRECONDITION_REJECTED
    assert result.evidence_path is None
    assert reader.call_counts == {}


def test_a_reused_run_id_is_rejected_as_a_precondition(tmp_path):
    request = make_request(tmp_path)
    run_checkpoint(request, StubReader())
    second = run_checkpoint(request, StubReader())
    assert second.outcome is CheckpointOutcome.PRECONDITION_REJECTED


def test_a_capability_violation_is_reported_as_such(tmp_path):
    class Violating(StubReader):
        async def read_orders(self):
            raise ReadOnlyCapabilityError("tool is outside the read-only surface")

    result = run_checkpoint(make_request(tmp_path), Violating())
    assert result.outcome is CheckpointOutcome.CAPABILITY_VIOLATION
    assert result.evidence.operator_action_required == "INVESTIGATE"


def test_an_unexpected_failure_is_an_invariant_failure_not_a_result(tmp_path):
    class Exploding(StubReader):
        async def read_accounts(self):
            raise ZeroDivisionError("bug")

    result = run_checkpoint(make_request(tmp_path), Exploding())
    assert result.outcome is CheckpointOutcome.INTERNAL_INVARIANT_FAILURE


def test_every_outcome_is_a_distinct_value_and_only_two_are_completions():
    values = [outcome.value for outcome in CheckpointOutcome]
    assert len(values) == len(set(values)) == 12
    assert runtime.COMPLETED_OUTCOMES == {
        CheckpointOutcome.OBSERVATION_RECORDED,
        CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED,
    }


def test_persisted_evidence_survives_the_privacy_and_zero_write_gates(tmp_path):
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
        ),
    )
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    validate_read_only_evidence(payload)
    assert payload["read_call_counts"] == {
        "get_accounts": 1,
        "get_equity_positions": 1,
        "get_equity_orders": 1,
    }
    assert payload["approved_active_protection_state_count"] == 0
    assert SYMBOL not in json.dumps(payload)


def test_a_full_cycle_through_the_real_reader_asks_only_for_reads(tmp_path):
    """End to end through the actual parsing code, not the stub.

    The assertion that matters is the last one: across a complete checkpoint,
    the session was asked for three tool names and all three are reads.
    """
    session = FakeSession(
        default_payloads(
            positions=[{"symbol": SYMBOL, "quantity": "1"}], orders=[order_row()]
        )
    )
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    result = run_checkpoint(make_request(tmp_path), reader)
    assert result.outcome is CheckpointOutcome.OBSERVATION_RECORDED
    assert result.evidence.observed_protection_state == "confirmed"
    assert sorted(session.requested) == sorted(PERMITTED_READ_TOOLS)
    assert all(
        MCP_TOOL_CLASSIFICATION[name] is ToolCapability.READ_ONLY_PERMITTED
        for name in session.requested
    )


def test_the_runner_never_writes_evidence_naming_a_write_tool(tmp_path):
    result = run_checkpoint(make_request(tmp_path), StubReader())
    text = json.dumps(result.evidence.__dict__)
    for name, capability in MCP_TOOL_CLASSIFICATION.items():
        if capability is not ToolCapability.READ_ONLY_PERMITTED:
            assert name not in text


# --------------------------------------------------------------------------
# 10. Review corrections (PR #44 findings F1, F3, F4, F5, F6, F7).
# --------------------------------------------------------------------------


# --- F1: the session handle is private -------------------------------------


def test_the_reader_exposes_no_public_session_handle():
    """F1. A public handle put a generic write call one attribute away."""
    reader = RestrictedReadOnlyReader(session=FakeSession(), account_number=ACCOUNT)
    public = {name for name in dir(reader) if not name.startswith("_")}
    assert "session" not in public
    assert not hasattr(reader, "session")
    assert reader._session is not None


def test_the_reader_has_no_dict_to_reintroduce_a_public_session():
    """__slots__ means a public alias cannot be attached after construction."""
    reader = RestrictedReadOnlyReader(session=FakeSession(), account_number=ACCOUNT)
    assert not hasattr(reader, "__dict__")
    with pytest.raises(AttributeError):
        reader.session = FakeSession()


def test_the_reader_repr_never_renders_the_session():
    """A repr lands in logs and tracebacks."""
    reader = RestrictedReadOnlyReader(session=FakeSession(), account_number=ACCOUNT)
    rendered = repr(reader)
    assert "FakeSession" not in rendered
    assert "session" not in rendered
    assert ACCOUNT not in rendered


def test_the_reader_still_reads_normally_with_a_private_session():
    session = FakeSession(default_payloads())
    reader = RestrictedReadOnlyReader(session=session, account_number=ACCOUNT)
    asyncio.run(reader.read_accounts())
    assert session.requested == ["get_accounts"]


# --- F3: early closes ------------------------------------------------------


EARLY_CLOSE_DAYS = [(2026, 11, 27), (2026, 12, 24), (2027, 11, 26)]


@pytest.mark.parametrize("year,month,day", EARLY_CLOSE_DAYS)
def test_an_early_close_afternoon_is_not_a_regular_session(year, month, day):
    """F3. 14:00 ET previously classified REGULAR_HOURS while shut."""
    moment = datetime(year, month, day, 14, 0, tzinfo=EASTERN)
    assert classify_market_session(moment) is MarketSession.CLOSED


@pytest.mark.parametrize("year,month,day", EARLY_CLOSE_DAYS)
def test_the_early_close_boundary_is_one_pm_eastern(year, month, day):
    before = datetime(year, month, day, 12, 59, tzinfo=EASTERN)
    at_close = datetime(year, month, day, 13, 0, tzinfo=EASTERN)
    assert classify_market_session(before) is MarketSession.REGULAR_HOURS
    assert classify_market_session(at_close) is MarketSession.CLOSED


@pytest.mark.parametrize("year,month,day", EARLY_CLOSE_DAYS)
def test_an_early_close_morning_is_still_a_normal_session(year, month, day):
    """The half-day is short, not cancelled."""
    assert (
        classify_market_session(datetime(year, month, day, 10, 0, tzinfo=EASTERN))
        is MarketSession.REGULAR_HOURS
    )
    assert (
        classify_market_session(datetime(year, month, day, 9, 29, tzinfo=EASTERN))
        is MarketSession.EXTENDED_HOURS
    )


@pytest.mark.parametrize("year,month,day", EARLY_CLOSE_DAYS)
def test_no_early_close_day_reports_extended_hours_after_the_close(year, month, day):
    """Post-close extended trading exists but this table cannot bound it."""
    for hour in (13, 15, 17, 19):
        moment = datetime(year, month, day, hour, 30, tzinfo=EASTERN)
        assert classify_market_session(moment) is MarketSession.CLOSED


def test_a_full_trading_day_still_closes_at_four():
    assert (
        classify_market_session(datetime(2026, 8, 3, 15, 59, tzinfo=EASTERN))
        is MarketSession.REGULAR_HOURS
    )
    assert (
        classify_market_session(datetime(2026, 8, 3, 13, 30, tzinfo=EASTERN))
        is MarketSession.REGULAR_HOURS
    )


def test_no_supported_date_in_range_is_ever_wrongly_open():
    """Sweep every covered day: a closed day must never read as open."""
    from datetime import timedelta

    from src.read_only_observation_runtime import (
        _EARLY_CLOSE_DAYS,
        _MARKET_HOLIDAYS,
    )

    moment = datetime(2026, 1, 1, 14, 0, tzinfo=EASTERN)
    end = datetime(2027, 12, 31, 14, 0, tzinfo=EASTERN)
    while moment <= end:
        session = classify_market_session(moment)
        shut = (
            moment.weekday() >= 5
            or moment.date() in _MARKET_HOLIDAYS
            or moment.date() in _EARLY_CLOSE_DAYS
        )
        if shut:
            assert session is MarketSession.CLOSED, moment
        moment += timedelta(days=1)


def test_the_table_expires_rather_than_ageing_into_silence():
    from src.read_only_observation_runtime import CALENDAR_EXPIRES_AFTER

    assert CALENDAR_EXPIRES_AFTER.isoformat() == "2027-12-31"
    assert (
        classify_market_session(datetime(2028, 1, 3, 10, 0, tzinfo=EASTERN))
        is MarketSession.UNKNOWN
    )


# --- F4: calendar provenance is recorded and displayed ---------------------


def test_calendar_metadata_is_fixed_and_complete():
    """F4. The limitation was previously neither displayed nor recorded."""
    from src.read_only_observation_runtime import calendar_metadata

    meta = calendar_metadata()
    assert set(meta) == {
        "calendar_source",
        "calendar_supported_range",
        "calendar_limitation",
        "calendar_expires_after",
    }
    assert meta["calendar_source"] == "static_tracked_table"
    assert meta["calendar_supported_range"] == "2026-2027"
    assert meta["calendar_expires_after"] == "2027-12-31"
    assert all(isinstance(value, str) for value in meta.values())


def test_evidence_records_the_calendar_provenance(tmp_path):
    result = run_checkpoint(make_request(tmp_path), StubReader())
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["calendar_source"] == "static_tracked_table"
    assert payload["calendar_supported_range"] == "2026-2027"
    assert payload["calendar_expires_after"] == "2027-12-31"
    assert "expires_after_2027" in payload["calendar_limitation"]
    validate_read_only_evidence(payload)


def test_the_launcher_report_displays_the_calendar_limitation(tmp_path, capsys):
    import read_only_observation_checkpoint_launcher as launcher

    result = run_checkpoint(make_request(tmp_path), StubReader())
    launcher.report(result)
    printed = capsys.readouterr().out
    assert "calendar_source: static_tracked_table" in printed
    assert "calendar_supported_range: 2026-2027" in printed
    assert "2027-12-31" in printed
    assert "no authoritative feed" in printed


# --- F5: a declined operator opens no session ------------------------------


class ExplodingSessionFactory:
    """Any use at all is a failure: entering it means a session was opened."""

    def __init__(self, *, fail_on=None):
        self.entered = 0
        self.fail_on = fail_on

    def __call__(self):
        return self

    async def __aenter__(self):
        self.entered += 1
        if self.fail_on == "open":
            raise RuntimeError("transport handshake failed: secret-token-abc")
        return FakeSession(default_payloads())

    async def __aexit__(self, *exc):
        if self.fail_on == "close":
            raise RuntimeError("transport teardown failed: secret-token-xyz")
        return False


def unattended_request(tmp_path, run_id="m9-obs-0002"):
    from src.read_only_observation_runtime import UNATTENDED_MODE

    return ReadOnlyCheckpointRequest(
        run_id=run_id,
        account_number=ACCOUNT,
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        evidence_root=tmp_path / "evidence",
        authorization_flag=READ_ONLY_AUTHORIZATION_FLAG,
        operator_present=False,
        authorization_mode=UNATTENDED_MODE,
    )


def test_a_declined_operator_opens_zero_sessions(tmp_path):
    """F5. The session used to be opened before the prompt was answered."""
    import read_only_observation_checkpoint_launcher as launcher
    from src.read_only_observation_runtime import PreResolvedAuthorization

    factory = ExplodingSessionFactory()
    declined = PreResolvedAuthorization(
        decisions={ACKNOWLEDGMENT_STAGE: True, OBSERVATION_STAGE: False}
    )
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), declined, session_factory=factory
        )
    )
    assert result.outcome is CheckpointOutcome.DECLINED_BY_OPERATOR
    assert factory.entered == 0


def test_declining_the_first_stage_also_opens_zero_sessions(tmp_path):
    import read_only_observation_checkpoint_launcher as launcher
    from src.read_only_observation_runtime import PreResolvedAuthorization

    factory = ExplodingSessionFactory()
    declined = PreResolvedAuthorization(
        decisions={ACKNOWLEDGMENT_STAGE: False, OBSERVATION_STAGE: False}
    )
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), declined, session_factory=factory
        )
    )
    assert result.outcome is CheckpointOutcome.DECLINED_BY_OPERATOR
    assert factory.entered == 0


def test_the_launcher_resolves_interactive_authorization_before_any_session(
    monkeypatch, tmp_path
):
    """Both prompts are answered while no transport object exists."""
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    monkeypatch.setattr(launcher, "_read_secret", lambda prompt: "n")
    namespace = launcher.build_parser().parse_args([
        "--run-id", "m9-obs-0003",
        "--account-number", ACCOUNT,
        "--symbol", SYMBOL,
        "--expected-quantity", "1",
        "--evidence-root", str(tmp_path / "evidence"),
        "--authorization", READ_ONLY_AUTHORIZATION_FLAG,
        "--operator-present",
        "--i-understand-this-performs-real-broker-reads",
        "--interactive",
    ])
    provider = launcher.build_authorization(namespace, now=REGULAR_HOURS)
    assert provider.all_approved is False


def test_an_approved_authorization_does_open_exactly_one_session(tmp_path):
    import read_only_observation_checkpoint_launcher as launcher
    from src.read_only_observation_runtime import PreResolvedAuthorization

    factory = ExplodingSessionFactory()
    approved = PreResolvedAuthorization(
        decisions={ACKNOWLEDGMENT_STAGE: True, OBSERVATION_STAGE: True}
    )
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved, session_factory=factory
        )
    )
    assert factory.entered == 1
    assert result.outcome is CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED


# --- F6: session open and close failures fail closed -----------------------


def approved_authorization():
    from src.read_only_observation_runtime import PreResolvedAuthorization

    return PreResolvedAuthorization(
        decisions={ACKNOWLEDGMENT_STAGE: True, OBSERVATION_STAGE: True}
    )


def test_a_session_open_failure_is_a_recorded_outcome(tmp_path):
    """F6. This previously escaped as an uncaught traceback."""
    import read_only_observation_checkpoint_launcher as launcher

    factory = ExplodingSessionFactory(fail_on="open")
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved_authorization(),
            session_factory=factory,
        )
    )
    assert result.outcome is CheckpointOutcome.SESSION_UNAVAILABLE
    assert result.evidence.operator_action_required == "INVESTIGATE_SESSION_OPEN"
    assert result.evidence_path is not None


def test_a_session_close_failure_is_a_recorded_outcome(tmp_path):
    import read_only_observation_checkpoint_launcher as launcher

    factory = ExplodingSessionFactory(fail_on="close")
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved_authorization(),
            session_factory=factory,
        )
    )
    assert result.outcome is CheckpointOutcome.SESSION_UNAVAILABLE
    assert result.evidence.operator_action_required == "INVESTIGATE_SESSION_CLOSE"


def test_a_close_failure_keeps_the_observation_that_was_recorded(tmp_path):
    """Evidence is immutable once written; history is not rewritten."""
    import read_only_observation_checkpoint_launcher as launcher

    factory = ExplodingSessionFactory(fail_on="close")
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved_authorization(),
            session_factory=factory,
        )
    )
    assert result.evidence_path is not None
    payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert payload["read_call_counts"] == {
        "get_accounts": 1,
        "get_equity_positions": 1,
        "get_equity_orders": 1,
    }


@pytest.mark.parametrize("phase", ["open", "close"])
def test_no_transport_text_survives_a_session_failure(tmp_path, phase):
    """The fakes raise with a secret in the message; none of it may escape."""
    import read_only_observation_checkpoint_launcher as launcher

    factory = ExplodingSessionFactory(fail_on=phase)
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved_authorization(),
            session_factory=factory,
        )
    )
    rendered = json.dumps(result.evidence.__dict__)
    assert "secret-token" not in rendered
    assert "transport" not in rendered
    if result.evidence_path is not None:
        assert "secret-token" not in result.evidence_path.read_text(encoding="utf-8")


def test_session_failure_evidence_still_asserts_zero_writes(tmp_path):
    import read_only_observation_checkpoint_launcher as launcher

    factory = ExplodingSessionFactory(fail_on="open")
    result = asyncio.run(
        launcher._execute(
            unattended_request(tmp_path), approved_authorization(),
            session_factory=factory,
        )
    )
    assert result.evidence.write_counters == {
        name: 0 for name in REQUIRED_ZERO_COUNTERS
    }
    assert_zero_write_evidence(result.evidence)


def test_a_session_failure_on_a_spent_run_id_still_returns_an_outcome(tmp_path):
    """No durable home for the evidence is not a reason to lose the outcome."""
    from src.read_only_observation_runtime import record_session_failure

    request = unattended_request(tmp_path)
    run_checkpoint(request, StubReader())
    result = record_session_failure(request, phase="open")
    assert result.outcome is CheckpointOutcome.SESSION_UNAVAILABLE
    assert result.evidence_path is None


def test_an_invalid_session_failure_phase_is_refused(tmp_path):
    from src.read_only_observation_runtime import record_session_failure

    with pytest.raises(ReadOnlyRuntimeError):
        record_session_failure(unattended_request(tmp_path), phase="reopen")


def test_session_unavailable_is_not_a_completion():
    assert CheckpointOutcome.SESSION_UNAVAILABLE not in runtime.COMPLETED_OUTCOMES


# --- F7: observed state is constrained to the known vocabulary -------------


def test_an_unknown_broker_state_persists_only_as_unrecognized(tmp_path):
    """F7. This previously wrote arbitrary broker text into evidence."""
    leaked = "unlisted_broker_state_x9"
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", leaked, True)],
        ),
    )
    assert result.outcome is CheckpointOutcome.OBSERVATION_RECORDED
    assert result.evidence.observed_protection_state == "UNRECOGNIZED"
    assert leaked not in result.evidence_path.read_text(encoding="utf-8")


def test_a_known_broker_state_is_still_recorded_verbatim(tmp_path):
    """Recording observed states is the entire point of the checkpoint."""
    result = run_checkpoint(
        make_request(tmp_path),
        StubReader(
            positions=[ObservedPosition(SYMBOL, Decimal("1"))],
            orders=[ObservedOrder(SYMBOL, "sell", "stop_market", "confirmed", True)],
        ),
    )
    assert result.evidence.observed_protection_state == "confirmed"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("confirmed", "confirmed"),
        ("Confirmed", "confirmed"),
        ("unconfirmed", "unconfirmed"),
        ("queued", "queued"),
        ("wibble", "UNRECOGNIZED"),
        ("state-with-acct123", "UNRECOGNIZED"),
        ("", "NONE"),
        (None, "NONE"),
        (42, "NONE"),
    ],
)
def test_recordable_order_state_vocabulary(state, expected):
    from src.read_only_observation_runtime import recordable_order_state

    assert recordable_order_state(state) == expected


def test_every_recordable_state_is_in_the_known_vocabulary():
    """No evidence value may fall outside a fixed, reviewable set."""
    from src.read_only_observation_runtime import (
        KNOWN_ORDER_STATES,
        UNRECOGNIZED_ORDER_STATE,
        recordable_order_state,
    )

    permitted = KNOWN_ORDER_STATES | {UNRECOGNIZED_ORDER_STATE, "NONE"}
    for probe in ("confirmed", "filled", "zzz", "", None, "PENDING_CANCELLED"):
        assert recordable_order_state(probe) in permitted


def test_the_approved_set_is_folded_in_dynamically():
    """Promoting a state later must not make it unrecordable."""
    from src.read_only_broker_state import approved_active_protection_states
    from src.read_only_observation_runtime import recordable_order_state

    for state in approved_active_protection_states():
        assert recordable_order_state(state) == state
