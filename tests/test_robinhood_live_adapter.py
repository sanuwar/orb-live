"""Fixture-only tests for the default-off initialization adapter."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.broker_snapshot import account_fingerprint
from src.config import load_config
from src.live_order_contract import parse_cancel_response, parse_place_response
from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
from src.read_only_broker_gateway import READ_ONLY_TOOL_ALLOWLIST
from src.robinhood_live_adapter import (
    CANCEL_RESPONSE_PARSER,
    FIXED_REVIEW_ARGUMENTS,
    INITIALIZATION_TOOL_SEQUENCE,
    MAX_DELEGATED_TOOL_CALLS,
    MAX_MCP_SESSIONS,
    MAX_PAGINATION_CALLS,
    MAX_RETRIES,
    PLACE_RESPONSE_PARSER,
    BrowserAuthenticationRequestError,
    CallSequenceError,
    CommissionedIdentityMismatchError,
    InvalidLiveAdapterModeError,
    LiveAdapterMode,
    ProhibitedToolError,
    ResponseShapeViolationError,
    RobinhoodLiveAdapter,
    SessionLimitViolationError,
)
from src.broker_saga_executor import SagaExecutor, SagaExecutorError


ACCOUNT = "FIXTURE-ACCOUNT-0001"
OTHER_ACCOUNT = "FIXTURE-ACCOUNT-0002"
FINGERPRINT = account_fingerprint(ACCOUNT)
ROOT = Path(__file__).parent.parent


class FakeResult:
    def __init__(self, payload, *, is_error=False):
        self.isError = is_error
        self.structuredContent = payload
        self.content = []


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        result = self.responses[tool_name]
        if isinstance(result, Exception):
            raise result
        return result


def envelope(data, guide=""):
    return FakeResult({"data": data, "guide": guide})


def account(number=ACCOUNT, **overrides):
    value = {
        "account_number": number,
        "rhs_account_number": "10000001",
        "type": "cash",
        "brokerage_account_type": "individual",
        "is_default": True,
        "agentic_allowed": True,
        "option_level": "",
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }
    value.update(overrides)
    return value


def position(**overrides):
    value = {
        "symbol": "ZZPRIVATE",
        "quantity": "7",
        "intraday_quantity": "0",
        "average_buy_price": None,
        "shares_available_for_sells": "7",
        "shares_held_for_sells": "0",
        "shares_held_for_stock_grants": "0",
        "shares_held_for_options_events": "0",
        "shares_held_for_asset_transfer": "0",
        "shares_pending_from_options_events": "0",
        "type": "long",
    }
    value.update(overrides)
    return value


def order(state="queued", **overrides):
    value = {
        "id": "FIXTURE-ORDER-SECRET",
        "instrument_id": "FIXTURE-INSTRUMENT-SECRET",
        "symbol": "ZZPRIVATE",
        "side": "sell",
        "type": "market",
        "state": state,
        "quantity": "7",
        "cumulative_quantity": "2",
        "price": None,
        "stop_price": "9",
        "average_price": "10",
        "fees": "0",
        "dollar_based_amount": None,
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "trigger": "stop",
        "placed_agent": "agentic",
        "created_at": "2020-01-01T00:00:00Z",
        "last_transaction_at": None,
        "executions": None,
    }
    value.update(overrides)
    return value


def responses(*, accounts=None, positions=None, orders=None, review=None):
    return {
        "get_accounts": envelope({"accounts": [account()] if accounts is None else accounts}),
        "get_equity_positions": envelope({"positions": [position()] if positions is None else positions, "next": ""}),
        "get_equity_orders": envelope({"orders": [order()] if orders is None else orders, "next": ""}),
        "review_equity_order": envelope(
            {
                "symbol": "AAPL",
                "side": "buy",
                "type": "market",
                "quantity": "1",
                "order_checks": {},
                "quote_data": None,
            }
            if review is None
            else review
        ),
    }


def adapter(session=None, *, commissioned=FINGERPRINT):
    instance = RobinhoodLiveAdapter(
        LiveAdapterMode.INITIALIZATION_READ_REVIEW_ONLY,
        config_loader=lambda: {"broker": {"expected_account_fingerprint": commissioned}},
        local_config_loader=lambda: {},
    )
    if session is not None:
        instance.attach_session(session)
    return instance


async def run_full(instance):
    account_summary = await instance.get_accounts()
    position_summary = await instance.get_equity_positions()
    order_summary = await instance.get_equity_orders()
    review_summary = await instance.review_equity_order()
    return account_summary, position_summary, order_summary, review_summary


def test_exact_initialization_mode_is_required():
    with pytest.raises(InvalidLiveAdapterModeError):
        RobinhoodLiveAdapter(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidLiveAdapterModeError):
        RobinhoodLiveAdapter("INITIALIZATION_READ_REVIEW_ONLY")  # type: ignore[arg-type]


def test_construction_does_not_run_loaders_or_create_a_session():
    calls = []
    instance = RobinhoodLiveAdapter(
        LiveAdapterMode.INITIALIZATION_READ_REVIEW_ONLY,
        config_loader=lambda: calls.append("config"),
        local_config_loader=lambda: calls.append("local"),
    )
    assert calls == []
    assert "session" not in repr(instance).lower()


def test_fixture_only_write_parser_wiring_is_exact():
    assert PLACE_RESPONSE_PARSER is parse_place_response
    assert CANCEL_RESPONSE_PARSER is parse_cancel_response


def test_adapter_cannot_masquerade_as_broker_transport():
    public = set(dir(RobinhoodLiveAdapter))
    assert "simulated_only" not in public
    assert not {"review_order", "submit_order", "cancel_order", "lookup_order_by_ref", "snapshot"} & public


def test_saga_executor_rejects_adapter_without_binding():
    with pytest.raises(SagaExecutorError):
        SagaExecutor._validate_transport(adapter())


def test_one_session_limit_is_exact():
    first = FakeSession(responses())
    instance = adapter(first)
    with pytest.raises(SessionLimitViolationError):
        instance.attach_session(FakeSession(responses()))
    assert MAX_MCP_SESSIONS == 1


@pytest.mark.anyio
async def test_exact_four_call_sequence_and_sanitized_results(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses())
    instance = adapter(session)
    summaries = await run_full(instance)
    complete = instance.assert_complete()
    assert [name for name, _ in session.calls] == list(INITIALIZATION_TOOL_SEQUENCE)
    assert complete.delegated_call_count == MAX_DELEGATED_TOOL_CALLS == 4
    assert complete.session_count == 1 and complete.complete is True
    assert summaries[0].identity_match is True
    assert summaries[1].record_count == 1
    assert summaries[2].record_count == 1
    assert summaries[3].non_committing is True
    assert summaries[3].place_parser_live_validated is False
    assert summaries[3].cancel_parser_live_validated is False


@pytest.mark.anyio
async def test_fixed_review_request_has_no_extra_order_fields(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses())
    await run_full(adapter(session))
    name, payload = session.calls[-1]
    assert name == "review_equity_order"
    assert payload == {"account_number": ACCOUNT, **FIXED_REVIEW_ARGUMENTS}
    for forbidden in ("ref_id", "dollar_amount", "limit_price", "stop_price", "tax_lots"):
        assert forbidden not in payload


@pytest.mark.anyio
async def test_out_of_order_request_is_rejected_before_delegation():
    session = FakeSession(responses())
    instance = adapter(session)
    with pytest.raises(CallSequenceError):
        await instance.get_equity_positions()
    assert session.calls == []


@pytest.mark.anyio
async def test_repeated_request_is_rejected_before_delegation(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses())
    instance = adapter(session)
    await instance.get_accounts()
    with pytest.raises(CallSequenceError):
        await instance.get_accounts()
    assert [name for name, _ in session.calls] == ["get_accounts"]


def test_missing_request_fails_closed():
    instance = adapter(FakeSession(responses()))
    with pytest.raises(CallSequenceError):
        instance.assert_complete()


@pytest.mark.anyio
async def test_fifth_request_is_rejected_before_delegation(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses())
    instance = adapter(session)
    await run_full(instance)
    with pytest.raises(SessionLimitViolationError):
        await instance.review_equity_order()
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    "name",
    [
        "place_equity_order",
        "cancel_equity_order",
        "place_option_order",
        "get_option_orders",
        "get_equity_quotes",
        "get_equity_historicals",
        "list_tools",
        "unexpected_tool",
    ],
)
def test_every_non_allowlisted_tool_is_typed_and_rejected(name):
    session = FakeSession(responses())
    instance = adapter(session)
    with pytest.raises(ProhibitedToolError):
        getattr(instance, name)
    assert session.calls == []


@pytest.mark.anyio
async def test_identity_ambiguity_fails_closed_without_identifiers(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses(accounts=[account(), account(OTHER_ACCOUNT)]))
    instance = adapter(session)
    with pytest.raises(CommissionedIdentityMismatchError) as caught:
        await instance.get_accounts()
    rendered = str(caught.value)
    assert ACCOUNT not in rendered and OTHER_ACCOUNT not in rendered


@pytest.mark.anyio
async def test_identity_mismatch_fails_closed_without_fingerprint(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    session = FakeSession(responses())
    instance = adapter(session, commissioned=account_fingerprint(OTHER_ACCOUNT))
    with pytest.raises(CommissionedIdentityMismatchError) as caught:
        await instance.get_accounts()
    assert ACCOUNT not in str(caught.value)
    assert FINGERPRINT not in str(caught.value)


@pytest.mark.anyio
async def test_historical_scalars_never_appear_in_results(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    summaries = await run_full(adapter(FakeSession(responses())))
    rendered = repr(summaries)
    for secret in (ACCOUNT, FINGERPRINT, "ZZPRIVATE", "FIXTURE-ORDER-SECRET", "2020-01-01", "7", "9"):
        assert secret not in rendered


@pytest.mark.anyio
async def test_review_output_is_shape_only(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    review = {
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "quantity": "1",
        "order_checks": {"alert_type": "INVENTED_ALERT", "invented_alert_details": {"approved": False}},
        "quote_data": None,
        "market_data_disclosure": "PRIVATE QUOTE PROSE 123.45",
    }
    summaries = await run_full(adapter(FakeSession(responses(review=review))))
    result = summaries[-1]
    rendered = repr(result)
    for secret in ("AAPL", "INVENTED_ALERT", "PRIVATE QUOTE PROSE", "123.45"):
        assert secret not in rendered
    assert ("data.order_checks.invented_alert_details.approved", "boolean") in result.json_types
    assert result.boolean_count == 1


@pytest.mark.anyio
async def test_working_and_pending_states_never_prove_protection(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    states = ["new", "queued", "confirmed", "unconfirmed", "partially_filled", "pending_cancelled"]
    session = FakeSession(responses(orders=[order(state) for state in states]))
    summaries = await run_full(adapter(session))
    result = summaries[2]
    counts = dict(result.lifecycle_counts)
    assert counts["ACTIVE_OR_WORKING"] == 5
    assert counts["CANCELLATION_PENDING"] == 1
    assert result.active_protection_mapping_empty is True
    assert result.historical_records_prove_active_protection is False
    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()


@pytest.mark.anyio
async def test_unknown_order_state_fails_closed_to_indeterminate(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    result = (await run_full(adapter(FakeSession(responses(orders=[order("future_state")])))))[2]
    assert dict(result.lifecycle_counts)["INDETERMINATE_OR_UNSUPPORTED"] == 1


@pytest.mark.anyio
async def test_nonempty_pagination_fails_without_followup(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    data = responses()
    data["get_equity_positions"] = envelope({"positions": [], "next": "opaque-next"})
    session = FakeSession(data)
    instance = adapter(session)
    await instance.get_accounts()
    with pytest.raises(ResponseShapeViolationError):
        await instance.get_equity_positions()
    assert [name for name, _ in session.calls] == ["get_accounts", "get_equity_positions"]
    assert MAX_PAGINATION_CALLS == 0


@pytest.mark.anyio
async def test_malformed_response_fails_without_raw_values(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    data = responses(accounts=[{**account(), "SUPERSECRET": "DO-NOT-LOG"}])
    instance = adapter(FakeSession(data))
    with pytest.raises(ResponseShapeViolationError) as caught:
        await instance.get_accounts()
    rendered = str(caught.value)
    assert "SUPERSECRET" not in rendered and "DO-NOT-LOG" not in rendered


@pytest.mark.anyio
async def test_underlying_failure_is_sanitized_and_never_retried(monkeypatch):
    monkeypatch.delenv("ORB_EXPECTED_ACCOUNT_FINGERPRINT", raising=False)
    data = responses()
    data["get_accounts"] = RuntimeError("RAW BROKER SECRET")
    session = FakeSession(data)
    instance = adapter(session)
    with pytest.raises(ResponseShapeViolationError) as caught:
        await instance.get_accounts()
    assert "RAW BROKER SECRET" not in str(caught.value)
    assert len(session.calls) == 1
    assert MAX_RETRIES == 0


def test_browser_authentication_has_a_dedicated_typed_error():
    assert issubclass(BrowserAuthenticationRequestError, RuntimeError)


def test_adapter_call_sites_are_exact_literals_only():
    source = (ROOT / "src" / "robinhood_live_adapter.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "call_tool":
            assert isinstance(node.args[0], ast.Constant)
            calls.append(node.args[0].value)
    assert tuple(calls) == INITIALIZATION_TOOL_SEQUENCE


def test_no_retry_pagination_client_or_import_time_io_surface():
    source = (ROOT / "src" / "robinhood_live_adapter.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "robinhoodclient(" not in lowered
    assert "while " not in lowered
    assert "asyncio.sleep" not in lowered
    assert "time.sleep" not in lowered
    assert "local.yaml" not in lowered
    assert ".rh_tokens" not in lowered
    assert "open(" not in lowered
    assert "brokertransport" not in source.replace("``BrokerTransport``", "")


def test_read_only_gateway_review_prohibition_is_unchanged():
    assert "review_equity_order" not in READ_ONLY_TOOL_ALLOWLIST
    assert all(name.startswith("get_") for name in READ_ONLY_TOOL_ALLOWLIST)


def test_strategy_stays_disabled_and_risk_config_is_not_adapter_input():
    config = load_config()
    assert config["strategy"]["enabled"] is False
    signature = inspect.signature(RobinhoodLiveAdapter)
    assert "risk" not in signature.parameters
