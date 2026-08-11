from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.live_shadow_session import (
    BudgetedHttpClient, RunMode, SessionLimits, _base_evidence, _evaluate,
    deterministic_fixture, main, run_session,
)
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketDataQuality
from src.market_data_provider import MarketDataProviderError
from src.shadow_trading_decision import ShadowDecisionType

NOW = datetime(2026, 8, 11, 13, 46, tzinfo=timezone.utc)
CREDS = {"ALPACA_API_KEY_ID": "test-key-id", "ALPACA_API_SECRET_KEY": "test-secret-value"}


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
    def json(self):
        return self.payload


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []
    async def get(self, url, *, headers, params):
        self.calls += 1
        self.requests.append({"url": url, "params": dict(params)})
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def raw_bar(moment=NOW - timedelta(minutes=1)):
    return {"t": moment.isoformat().replace("+00:00", "Z"), "o": "200", "h": "201", "l": "199", "c": "200.5", "v": 100}


def test_default_fixture_is_zero_network_and_produces_candidate(tmp_path):
    evidence, path = asyncio.run(run_session(now=NOW, output_dir=tmp_path))
    assert evidence["request_count"] == 0
    assert evidence["broker_write_count"] == 0
    assert evidence["shadow_trading_decision"].decision_type is ShadowDecisionType.LONG_CANDIDATE
    assert json.loads(path.read_text())["broker_write_count"] == 0


def test_fixture_evidence_is_deterministic(tmp_path):
    first, _ = asyncio.run(run_session(now=NOW, output_dir=tmp_path / "one"))
    second, _ = asyncio.run(run_session(now=NOW, output_dir=tmp_path / "two"))
    assert json.dumps(first, default=str, sort_keys=True) == json.dumps(second, default=str, sort_keys=True)
    assert first["market_data_digest"]
    assert first["strategy_version"] == "orb_long_v0-0.1.0"


def test_production_requires_explicit_flag_before_http(tmp_path):
    client = Client([Response({"bars": [raw_bar()]})])
    evidence, _ = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=NOW, output_dir=tmp_path, environment=CREDS, http_client=client))
    assert client.calls == 0
    assert evidence["errors"] == ["RUNNER_BOUNDARY_ERROR"]


def test_missing_credentials_fail_before_http_and_never_leak(tmp_path):
    client = Client([Response({"bars": [raw_bar()]})])
    evidence, path = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=NOW, output_dir=tmp_path, environment={}, http_client=client, production_confirmed=True))
    rendered = path.read_text()
    assert client.calls == 0
    assert "MISSING" in evidence["no_signal_or_rejection_reason"]
    assert "test-key-id" not in rendered and "test-secret-value" not in rendered


def test_smoke_is_correctly_classified_outside_regular_session(tmp_path):
    outside = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)  # Sunday
    client = Client([Response({"bars": [raw_bar(outside - timedelta(minutes=1))]})])
    evidence, _ = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=outside, output_dir=tmp_path, environment=CREDS, http_client=client, production_confirmed=True))
    assert evidence["run_mode"] == "PRODUCTION_SMOKE_READ"
    assert evidence["no_signal_or_rejection_reason"] == "PRODUCTION_MARKET_DATA_SMOKE_READ"
    assert evidence["shadow_trading_decision"] is None
    assert evidence["session_identity"]["is_regular_session"] is False


def test_incomplete_opening_range_never_creates_candidate():
    bars = deterministic_fixture(NOW)
    bars["AAPL"] = [bar for bar in bars["AAPL"] if bar.event_timestamp != datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc)]
    evidence = _base_evidence(mode=RunMode.OFFLINE_FIXTURE, symbols=("AAPL", "SPY"), started_at=NOW, root=Path.cwd())
    _evaluate("AAPL", bars, now=NOW, evidence=evidence)
    assert evidence["quality_outcome"] == MarketDataQuality.MISSING_OPENING_RANGE_BARS.value
    assert evidence["shadow_trading_decision"] is None


def test_stale_or_malformed_data_fails_closed():
    bars = deterministic_fixture(NOW)
    bars["AAPL"][-1] = replace(bars["AAPL"][-1], event_timestamp=NOW - timedelta(minutes=10), end_timestamp=NOW - timedelta(minutes=9))
    evidence = _base_evidence(mode=RunMode.OFFLINE_FIXTURE, symbols=("AAPL", "SPY"), started_at=NOW, root=Path.cwd())
    _evaluate("AAPL", bars, now=NOW, evidence=evidence)
    assert evidence["quality_outcome"] == MarketDataQuality.STALE.value
    assert evidence["shadow_trading_decision"] is None


def test_provider_error_timeout_and_rate_limit_are_bounded(tmp_path):
    error_client = Client([Response({"error": "no"}, status=403)])
    evidence, _ = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=NOW, output_dir=tmp_path / "error", environment=CREDS, http_client=error_client, production_confirmed=True))
    assert evidence["errors"] == ["PROVIDER_HTTP_ERROR"]
    timeout_client = Client([TimeoutError("secret should never appear")])
    timeout, path = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=NOW, output_dir=tmp_path / "timeout", environment=CREDS, http_client=timeout_client, production_confirmed=True))
    assert timeout_client.calls == 1 and "secret" not in path.read_text()
    rate_client = Client([Response({}, status=429), Response({"bars": [raw_bar()]})])
    rate, _ = asyncio.run(run_session(mode=RunMode.PRODUCTION_SMOKE_READ, now=NOW, output_dir=tmp_path / "rate", environment=CREDS, http_client=rate_client, production_confirmed=True))
    assert rate_client.calls == 2 and rate["request_count"] == 2


def test_request_and_symbol_bounds_are_enforced():
    class Inner:
        async def get(self, *args, **kwargs):
            return Response({"bars": []})
    client = BudgetedHttpClient(Inner(), max_requests=1)
    asyncio.run(client.get("https://data.alpaca.markets", headers={}, params={}))
    try:
        asyncio.run(client.get("https://data.alpaca.markets", headers={}, params={}))
    except Exception as error:
        assert "budget" in str(error)
    else:
        raise AssertionError("budget must refuse the second request")
    try:
        SessionLimits(max_symbols=11)
    except Exception:
        pass
    else:
        raise AssertionError("ten-candidate maximum must be enforced")


def test_runner_has_no_commissioning_or_broker_write_names():
    root = Path(__file__).resolve().parents[1]
    forbidden = {"RobinhoodCommissioningAdapter", "RobinhoodCommissioningSessionBinding", "OneShareCommissioningOrchestrator", "ExecutionSafetyDecision", "evaluate_execution_safety", "call_tool", "place_equity_order", "cancel_equity_order"}
    for relative in ("src/live_shadow_session.py", "scripts/run_live_shadow_session.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (names & forbidden), (relative, names & forbidden)


def test_authority_and_evidence_identity_fields_are_preserved(tmp_path):
    evidence, path = asyncio.run(run_session(now=NOW, output_dir=tmp_path))
    saved = json.loads(path.read_text())
    assert saved["provider"] == "fixture-alpaca-shape" and saved["feed"] == "iex"
    assert saved["broker_write_count"] == 0
    assert saved["authority_ceiling"] == "ATTENDED_COMMISSIONING_ONLY"
    assert all(saved[name] is False for name in ("strategy_enabled", "protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready"))

def test_smoke_read_uses_last_completed_regular_session_outside_hours(tmp_path):
    sunday = datetime(2026, 8, 10, 4, 36, tzinfo=timezone.utc)
    friday_bar = raw_bar(datetime(2026, 8, 7, 19, 59, tzinfo=timezone.utc))
    client = Client([Response({"bars": [friday_bar]})])
    evidence, _ = asyncio.run(run_session(
        mode=RunMode.PRODUCTION_SMOKE_READ,
        now=sunday,
        output_dir=tmp_path,
        environment=CREDS,
        http_client=client,
        production_confirmed=True,
    ))
    assert client.calls == 1
    assert client.requests[0]["params"]["start"] == "2026-08-07T19:45:00Z"
    assert client.requests[0]["params"]["end"] == "2026-08-07T20:00:00Z"
    assert evidence["normalized_bar_count"] == 1
    assert evidence["market_data_digest"] is not None
    assert evidence["quality_outcome"] == MarketDataQuality.VALID.value
    assert evidence["provider_diagnostics"] == {
        "endpoint_path": "/v2/stocks/AAPL/bars",
        "requested_symbol": "AAPL",
        "requested_timeframe": "1Min",
        "requested_start": datetime(2026, 8, 7, 19, 45, tzinfo=timezone.utc),
        "requested_end": datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc),
        "http_status": 200,
        "top_level_keys": ["bars"],
        "returned_bar_count": 1,
        "returned_timestamps": ["2026-08-07T19:59:00+00:00"],
    }
    assert evidence["broker_write_count"] == 0

def _raw_market_bar(bar):
    return {
        "t": bar.event_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "o": str(bar.open), "h": str(bar.high), "l": str(bar.low),
        "c": str(bar.close), "v": bar.volume,
    }


def test_live_shadow_uses_bounded_baseline_windows_and_discards_extended_surplus(tmp_path):
    from src.live_shadow_session import _live_candidate_windows

    fixture = deterministic_fixture(NOW)
    day = NOW.astimezone(EASTERN).date()
    first_window, second_window = _live_candidate_windows(day, NOW, lookback_days=20)
    first = [_raw_market_bar(bar) for bar in fixture["AAPL"] if first_window[0] <= bar.event_timestamp < first_window[1]]
    second = [_raw_market_bar(bar) for bar in fixture["AAPL"] if second_window[0] <= bar.event_timestamp < second_window[1]]
    # A real multi-day request can contain premarket data. It is rejected by
    # the calendar validator, counted once in evidence, and never reaches ORB.
    second.append(raw_bar(datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)))
    second.sort(key=lambda bar: bar["t"])
    spy = [_raw_market_bar(bar) for bar in fixture["SPY"]]
    client = Client([Response({"bars": spy}), Response({"bars": first}), Response({"bars": second})])

    evidence, _ = asyncio.run(run_session(mode=RunMode.LIVE_SHADOW, now=NOW, output_dir=tmp_path, environment=CREDS, http_client=client, production_confirmed=True))

    assert client.calls == 3
    assert evidence["request_count"] == 3
    assert evidence["quality_outcome"] == MarketDataQuality.VALID.value
    assert evidence["market_data_digest"] is not None
    assert evidence["opening_range_identity"] is not None
    assert evidence["shadow_trading_decision"] is not None
    assert evidence["quality_rejection_reasons"] == [MarketDataQuality.OUTSIDE_SESSION.value]
    assert evidence["quality_rejection_counts"] == {MarketDataQuality.OUTSIDE_SESSION.value: 1}
    assert evidence["broker_write_count"] == 0


def test_live_calendar_filter_keeps_historical_regular_bars_and_rejects_extended_weekend_and_holiday():
    from src.live_shadow_session import _fixture_bar, _live_eligible_bars
    from src.exchange_calendar import EASTERN

    current_day = NOW.astimezone(EASTERN).date()
    regular = _fixture_bar("AAPL", datetime(2026, 8, 7, 13, 30, tzinfo=timezone.utc), open_price="1", high="2", low="1", close="2", volume=1)
    premarket = _fixture_bar("AAPL", datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc), open_price="1", high="2", low="1", close="2", volume=1)
    afterhours = _fixture_bar("AAPL", datetime(2026, 8, 7, 20, 30, tzinfo=timezone.utc), open_price="1", high="2", low="1", close="2", volume=1)
    weekend = _fixture_bar("AAPL", datetime(2026, 8, 8, 13, 30, tzinfo=timezone.utc), open_price="1", high="2", low="1", close="2", volume=1)
    holiday = _fixture_bar("AAPL", datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc), open_price="1", high="2", low="1", close="2", volume=1)

    eligible, counts, fatal = _live_eligible_bars("AAPL", [holiday, premarket, regular, afterhours, weekend], now=NOW, current_day=current_day)

    assert eligible == [regular]
    assert counts[MarketDataQuality.OUTSIDE_SESSION.value] == 2
    assert counts[MarketDataQuality.MARKET_HOLIDAY.value] == 2
    assert fatal == []


def _live_responses_for(symbols, *, invalid_symbol=None):
    fixture = deterministic_fixture(NOW, candidate_symbols=symbols)
    day = NOW.astimezone(EASTERN).date()
    first_window, second_window = __import__("src.live_shadow_session", fromlist=["_live_candidate_windows"])._live_candidate_windows(day, NOW, lookback_days=20)
    responses = [Response({"bars": [_raw_market_bar(bar) for bar in fixture["SPY"]]})]
    for symbol in symbols:
        if symbol == invalid_symbol:
            responses.extend([Response({"bars": []}), Response({"bars": []})])
            continue
        first = [_raw_market_bar(bar) for bar in fixture[symbol] if first_window[0] <= bar.event_timestamp < first_window[1]]
        second = [_raw_market_bar(bar) for bar in fixture[symbol] if second_window[0] <= bar.event_timestamp < second_window[1]]
        responses.extend([Response({"bars": first}), Response({"bars": second})])
    return responses


def test_watchlist_parsing_deduplication_and_maximum_are_deterministic():
    from src.live_shadow_session import LiveShadowError, _watchlist

    assert _watchlist("AAPL", ("msft", "AAPL", "MSFT"), max_symbols=10) == ("MSFT", "AAPL")
    for values in ((), ("",), ("SPY",), tuple(f"S{index}" for index in range(11))):
        try:
            _watchlist("AAPL", values, max_symbols=10)
        except LiveShadowError:
            pass
        else:
            raise AssertionError("invalid bounded watchlist must be rejected")


def test_offline_multi_symbol_evidence_reuses_shared_spy_and_is_deterministic(tmp_path):
    first, _ = asyncio.run(run_session(now=NOW, output_dir=tmp_path / "one", candidate_symbols=("AAPL", "MSFT")))
    second, _ = asyncio.run(run_session(now=NOW, output_dir=tmp_path / "two", candidate_symbols=("AAPL", "MSFT")))

    assert first["watchlist"] == ["AAPL", "MSFT"]
    assert list(first["symbol_results"]) == ["AAPL", "MSFT"]
    assert first["shared_market_context"]["symbol"] == "SPY"
    assert first["candidate_summary"]["evaluated_count"] == 2
    assert all(item["market_data_digest"] and item["opening_range_identity"] and item["strategy_version"] for item in first["symbol_results"].values())
    assert json.dumps(first, default=str, sort_keys=True) == json.dumps(second, default=str, sort_keys=True)
    assert first["broker_write_count"] == 0


def test_live_multi_symbol_uses_spy_once_and_isolates_invalid_candidate(tmp_path):
    client = Client(_live_responses_for(("AAPL", "MSFT"), invalid_symbol="MSFT"))
    evidence, _ = asyncio.run(run_session(mode=RunMode.LIVE_SHADOW, now=NOW, output_dir=tmp_path, candidate_symbols=("AAPL", "MSFT"), environment=CREDS, http_client=client, production_confirmed=True))

    assert client.calls == 5  # one shared SPY request + two per candidate
    assert [request["params"]["symbols"] if "symbols" in request["params"] else request["url"].split("/stocks/")[1].split("/")[0] for request in client.requests].count("SPY") == 1
    assert evidence["symbol_results"]["AAPL"]["evaluation_state"] == "VALID_DECISION"
    assert evidence["symbol_results"]["MSFT"]["evaluation_state"] == "DATA_INCOMPLETE"
    assert evidence["candidate_summary"]["evaluated_count"] == 1
    assert evidence["candidate_summary"]["data_incomplete_count"] == 1
    assert evidence["broker_write_count"] == 0


def test_multi_symbol_request_budget_is_enforced(tmp_path):
    client = Client(_live_responses_for(("AAPL", "MSFT")))
    evidence, _ = asyncio.run(run_session(mode=RunMode.LIVE_SHADOW, now=NOW, output_dir=tmp_path, candidate_symbols=("AAPL", "MSFT"), environment=CREDS, http_client=client, production_confirmed=True, limits=SessionLimits(max_requests=4)))

    assert evidence["request_count"] == 4
    assert evidence["symbol_results"]["MSFT"]["evaluation_state"] == "PROVIDER_FAILURE"
    assert evidence["symbol_results"]["AAPL"]["evaluation_state"] == "VALID_DECISION"


def test_cli_symbols_parsing_preserves_single_symbol_compatibility(tmp_path, capsys):
    assert main(["--symbols", "AAPL,MSFT", "--output-dir", str(tmp_path)]) == 0
    multi_path = Path(capsys.readouterr().out.strip())
    assert json.loads(multi_path.read_text())["watchlist"] == ["AAPL", "MSFT"]
    assert main(["--symbol", "AAPL", "--output-dir", str(tmp_path)]) == 0
    single_path = Path(capsys.readouterr().out.strip())
    assert json.loads(single_path.read_text())["watchlist"] == ["AAPL"]


def test_shared_provider_failure_stops_live_watchlist_deterministically(tmp_path):
    client = Client([Response({"message": "forbidden"}, status=403)])
    evidence, _ = asyncio.run(run_session(mode=RunMode.LIVE_SHADOW, now=NOW, output_dir=tmp_path, candidate_symbols=("AAPL", "MSFT"), environment=CREDS, http_client=client, production_confirmed=True))

    assert client.calls == 1
    assert evidence["errors"] == ["PROVIDER_HTTP_ERROR"]
    assert evidence["symbol_results"] == {}
    assert evidence["broker_write_count"] == 0


def test_rejection_diagnostics_are_canonical_and_do_not_change_decision(tmp_path):
    evidence, _ = asyncio.run(run_session(now=NOW, output_dir=tmp_path))
    diagnostics = evidence["strategy_diagnostics"]
    decision = evidence["shadow_trading_decision"]
    assert diagnostics["failed_rule"] is None
    assert diagnostics["observed_gap_pct"] == "1.500"
    assert diagnostics["required_gap_pct"] == "1.0"
    assert diagnostics["opening_range_high"]
    assert decision.decision_type is ShadowDecisionType.LONG_CANDIDATE