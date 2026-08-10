from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.live_shadow_session import (
    BudgetedHttpClient, RunMode, SessionLimits, _base_evidence, _evaluate,
    deterministic_fixture, run_session,
)
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
    async def get(self, url, *, headers, params):
        self.calls += 1
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
        SessionLimits(max_symbols=3)
    except Exception:
        pass
    else:
        raise AssertionError("two-symbol maximum must be enforced")


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