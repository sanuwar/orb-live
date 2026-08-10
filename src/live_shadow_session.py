"""Bounded, read-only Alpaca market-data shadow session (M15 Issue 2).

The runtime has exactly three modes.  OFFLINE_FIXTURE is the default and
cannot open a network connection.  Production modes use the Issue 1
AlpacaMarketDataProvider; no broker client, account data, or execution path is
reachable from this module.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.config import load_config
from src.exchange_calendar import EASTERN, CalendarSession, classify, regular_session_bounds
from src.market_data import calculate_estimated_vwap, calculate_median_opening_volume, calculate_opening_range
from src.market_data_canonical import (
    DEFAULT_FRESHNESS_POLICY, MarketBar, MarketDataFreshnessPolicy,
    MarketDataQuality, compute_market_data_batch_digest, compute_market_data_digest,
    session_open_for, validate_market_bar, validate_market_data_batch,
)
from src.market_data_provider import AlpacaCredentials, AlpacaMarketDataProvider, MarketDataProviderError
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.shadow_trading_decision import ShadowTradingDecision, build_shadow_trading_decision
from src.signal_engine import LiveData, OpeningData

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_DIR = REPOSITORY_ROOT / "artifacts" / "live-shadow"
MAX_SYMBOLS = 2
MAX_REQUESTS = 4
MAX_ATTEMPTS_PER_FETCH = 2
HTTP_TIMEOUT_SECONDS = 8.0
HISTORICAL_FRESHNESS_POLICY = MarketDataFreshnessPolicy("market-data-history-v1", 45 * 86400.0)
NORMALIZATION_VERSION = "alpaca-market-data-v1"


class LiveShadowError(RuntimeError):
    """A bounded shadow run could not safely evaluate market data."""


class RunMode(str, Enum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    PRODUCTION_SMOKE_READ = "PRODUCTION_SMOKE_READ"
    LIVE_SHADOW = "LIVE_SHADOW"


@dataclass(frozen=True)
class SessionLimits:
    max_symbols: int = MAX_SYMBOLS
    max_requests: int = MAX_REQUESTS
    max_attempts_per_fetch: int = MAX_ATTEMPTS_PER_FETCH
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if self.max_symbols < 1 or self.max_symbols > MAX_SYMBOLS:
            raise LiveShadowError("symbol scope exceeds the bounded limit")
        if self.max_requests < 1 or self.max_requests > MAX_REQUESTS:
            raise LiveShadowError("request budget exceeds the bounded limit")
        if self.max_attempts_per_fetch < 1 or self.max_attempts_per_fetch > MAX_ATTEMPTS_PER_FETCH:
            raise LiveShadowError("retry budget exceeds the bounded limit")
        if self.timeout_seconds <= 0:
            raise LiveShadowError("timeout must be positive")


class _Response(Protocol):
    status_code: int
    def json(self) -> Any: ...


@dataclass(frozen=True)
class _UrllibResponse:
    status_code: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class UrllibMarketDataHttpClient:
    """Tiny injected transport used only behind the Issue 1 provider adapter."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def _get(self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any]) -> _UrllibResponse:
        request = Request(f"{url}?{urlencode(params)}", headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec B310: fixed HTTPS adapter URL
                return _UrllibResponse(int(response.status), response.read())
        except HTTPError as error:
            return _UrllibResponse(int(error.code), b"")

    async def get(self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any]) -> _UrllibResponse:
        return await asyncio.to_thread(self._get, url, headers=headers, params=params)


class BudgetedHttpClient:
    """Counts only market-data transport attempts and refuses before attempt N+1."""

    def __init__(self, inner: Any, *, max_requests: int) -> None:
        self._inner = inner
        self._max_requests = max_requests
        self.request_count = 0

    async def get(self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any]) -> _Response:
        if self.request_count >= self._max_requests:
            raise LiveShadowError("market-data request budget exhausted")
        self.request_count += 1
        return await self._inner.get(url, headers=headers, params=params)


def _safe_error(error: BaseException) -> str:
    if isinstance(error, MarketDataProviderError):
        text = str(error)
        if "(429)" in text:
            return "PROVIDER_RATE_LIMIT"
        if "non-success status" in text:
            return "PROVIDER_HTTP_ERROR"
        if "valid JSON" in text:
            return "MALFORMED_PROVIDER_RESPONSE"
        return "PROVIDER_ERROR"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "PROVIDER_TIMEOUT"
    if isinstance(error, LiveShadowError):
        return ("ALPACA_MARKET_DATA_CREDENTIALS_MISSING" if str(error) == "ALPACA_MARKET_DATA_CREDENTIALS_MISSING" else "RUNNER_BOUNDARY_ERROR")
    return "UNEXPECTED_RUNNER_ERROR"


def _credentials_from_environment(environment: Mapping[str, str]) -> AlpacaCredentials:
    key = environment.get("ALPACA_API_KEY_ID", "").strip()
    secret = environment.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise LiveShadowError("ALPACA_MARKET_DATA_CREDENTIALS_MISSING")
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


def _authority_state() -> dict[str, object]:
    return {
        "authority_ceiling": "ATTENDED_COMMISSIONING_ONLY",
        "strategy_enabled": False,
        "protection_grounded": False,
        "activation_approved": False,
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, ShadowTradingDecision):
        return {name: _json_value(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _run_id(mode: RunMode, symbols: Sequence[str], started_at: datetime) -> str:
    material = f"{mode.value}|{','.join(symbols)}|{started_at.isoformat()}".encode("utf-8")
    return f"m15-{mode.value.lower()}-{hashlib.sha256(material).hexdigest()[:16]}"


def _base_evidence(*, mode: RunMode, symbols: Sequence[str], started_at: datetime, root: Path) -> dict[str, Any]:
    run_id = _run_id(mode, symbols, started_at)
    return {
        "schema_version": "m15-live-shadow-evidence-v1",
        "run_id": run_id,
        "run_mode": mode.value,
        "reviewed_sha": resolve_head_sha(root),
        "implementation_digest": checkpoint_implementation_digest(root),
        "provider": "alpaca" if mode is not RunMode.OFFLINE_FIXTURE else "fixture-alpaca-shape",
        "feed": "iex",
        "symbols": list(symbols),
        "trading_date": started_at.astimezone(EASTERN).date(),
        "session_identity": None,
        "started_at": started_at,
        "completed_at": None,
        "request_count": 0,
        "normalized_bar_count": 0,
        "market_data_digest": None,
        "provider_diagnostics": {},
        "quality_outcome": "NOT_EVALUATED",
        "quality_rejection_reasons": [],
        "quality_rejection_counts": {},
        "opening_range_identity": None,
        "strategy_version": None,
        "shadow_trading_decision": None,
        "proposed_entry": None,
        "proposed_stop": None,
        "no_signal_or_rejection_reason": None,
        "errors": [],
        "broker_write_count": 0,
        **_authority_state(),
    }


def persist_evidence(evidence: Mapping[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(evidence["run_id"])
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise LiveShadowError("unsafe evidence run ID")
    target = output_dir / f"{run_id}.json"
    target.write_text(json.dumps(_json_value(evidence), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return target


def _previous_trading_dates(day: date, count: int) -> list[date]:
    values: list[date] = []
    cursor = day - timedelta(days=1)
    while len(values) < count:
        if regular_session_bounds(cursor) is not None:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return values


def _smoke_read_window(moment: datetime) -> tuple[datetime, datetime]:
    """Return a bounded window ending at the latest completed regular session.

    Smoke-read authentication/parser validation must work outside market hours;
    this deliberately does not alter LIVE_SHADOW's current-session rules.
    """
    local = moment.astimezone(EASTERN)
    day = local.date()
    bounds = regular_session_bounds(day)
    if bounds is not None and local.time() >= bounds[1]:
        selected_day = day
    elif bounds is not None and bounds[0] <= local.time() < bounds[1]:
        return moment - timedelta(minutes=15), moment
    else:
        selected_day = day - timedelta(days=1)
        while regular_session_bounds(selected_day) is None:
            selected_day -= timedelta(days=1)
    _, close = regular_session_bounds(selected_day)
    end = datetime.combine(selected_day, close, tzinfo=EASTERN).astimezone(timezone.utc)
    return end - timedelta(minutes=15), end

def _live_candidate_windows(day: date, moment: datetime, *, lookback_days: int) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Return two bounded windows covering the exact baseline and live data."""
    historical = sorted(_previous_trading_dates(day, lookback_days))
    midpoint = len(historical) // 2
    older, recent = historical[:midpoint], historical[midpoint:]
    if not older or not recent:
        raise LiveShadowError("historical baseline window could not be constructed")
    def midnight(value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    return ((midnight(older[0]), midnight(older[-1] + timedelta(days=1))), (midnight(recent[0]), moment))


def _live_eligible_bars(symbol: str, bars: Sequence[MarketBar], *, now: datetime, current_day: date) -> tuple[list[MarketBar], dict[str, int], list[str]]:
    """Discard calendar-invalid surplus bars; retain all other failures."""
    batch = validate_market_data_batch(bars)
    latest_current = max((bar.event_timestamp for bar in bars if bar.trading_date == current_day), default=None)
    eligible: list[MarketBar] = []
    counts: Counter[str] = Counter()
    fatal: list[str] = []
    discardable = {MarketDataQuality.OUTSIDE_SESSION, MarketDataQuality.MARKET_HOLIDAY}
    for index, bar in enumerate(bars):
        if bar.event_timestamp.astimezone(EASTERN).date() != bar.trading_date:
            outcome = MarketDataQuality.WRONG_TRADING_DATE
        else:
            policy = DEFAULT_FRESHNESS_POLICY if bar.event_timestamp == latest_current else HISTORICAL_FRESHNESS_POLICY
            outcome = batch.get(index, validate_market_bar(bar, expected_symbol=symbol, expected_trading_date=bar.trading_date, expected_interval_seconds=60, freshness_policy=policy, now=now))
        if outcome is MarketDataQuality.VALID:
            eligible.append(bar)
        else:
            counts[outcome.value] += 1
            if outcome not in discardable:
                fatal.append(outcome.value)
    return eligible, dict(sorted(counts.items())), fatal

def _fixture_bar(symbol: str, moment: datetime, *, open_price: str, high: str, low: str, close: str, volume: int) -> MarketBar:
    from src.market_data_provider import ALPACA_IEX_SOURCE
    return MarketBar(symbol=symbol, exchange_session="REGULAR", trading_date=moment.astimezone(EASTERN).date(), event_timestamp=moment, end_timestamp=moment + timedelta(minutes=1), receive_timestamp=moment + timedelta(seconds=1), open=Decimal(open_price), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume, source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID)


def deterministic_fixture(now: datetime) -> dict[str, list[MarketBar]]:
    now_et = now.astimezone(EASTERN)
    day = now_et.date()
    candidate = "AAPL"
    history: list[MarketBar] = []
    for historical_day in _previous_trading_dates(day, 20):
        opening = session_open_for(historical_day)
        for offset in range(5):
            history.append(_fixture_bar(candidate, datetime.combine(historical_day, opening, tzinfo=EASTERN) + timedelta(minutes=offset), open_price="200", high="201", low="199", close="200", volume=200000))
    opening = session_open_for(day)
    current = [
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=0), open_price="203", high="203.5", low="202", close="203", volume=600000),
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=1), open_price="203", high="204", low="202", close="203.5", volume=600000),
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=2), open_price="203.5", high="204", low="202", close="203.8", volume=600000),
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=3), open_price="203.8", high="204", low="202", close="203.9", volume=600000),
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=4), open_price="203.9", high="204", low="202", close="203.7", volume=600000),
        _fixture_bar(candidate, datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=15), open_price="204", high="205", low="203.8", close="204.1", volume=100000),
    ]
    spy = [_fixture_bar("SPY", datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=offset), open_price="690", high="691", low="689", close="690", volume=100000) for offset in range(5)]
    spy.append(_fixture_bar("SPY", datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=15), open_price="690", high="692", low="689", close="691", volume=100000))
    return {candidate: sorted(history, key=lambda bar: bar.event_timestamp) + current, "SPY": spy}


def _quality_for(symbol: str, bars: Sequence[MarketBar], *, now: datetime, current_day: date) -> tuple[MarketDataQuality, list[str]]:
    if not bars:
        return MarketDataQuality.INCOMPLETE_RESPONSE, [MarketDataQuality.INCOMPLETE_RESPONSE.value]
    batch = validate_market_data_batch(bars)
    outcomes: list[MarketDataQuality] = []
    latest_current = max((bar.event_timestamp for bar in bars if bar.trading_date == current_day), default=None)
    for index, bar in enumerate(bars):
        if bar.event_timestamp.astimezone(EASTERN).date() != bar.trading_date:
            outcomes.append(MarketDataQuality.WRONG_TRADING_DATE)
            continue
        policy = DEFAULT_FRESHNESS_POLICY if bar.event_timestamp == latest_current else HISTORICAL_FRESHNESS_POLICY
        individual = validate_market_bar(bar, expected_symbol=symbol, expected_trading_date=bar.trading_date, expected_interval_seconds=60, freshness_policy=policy, now=now)
        outcomes.append(batch.get(index, individual))
    rejected = [item.value for item in outcomes if item is not MarketDataQuality.VALID]
    return (MarketDataQuality.VALID if not rejected else outcomes[next(i for i, item in enumerate(outcomes) if item is not MarketDataQuality.VALID)]), rejected


def _complete_opening_range(bars: Sequence[MarketBar], *, day: date, minutes: int) -> bool:
    opening = session_open_for(day)
    expected = {datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=index) for index in range(minutes)}
    actual = [bar.event_timestamp.astimezone(EASTERN) for bar in bars if bar.event_timestamp.astimezone(EASTERN).date() == day and bar.event_timestamp.astimezone(EASTERN).time() >= opening and bar.event_timestamp.astimezone(EASTERN).time() < (datetime.combine(day, opening, tzinfo=EASTERN) + timedelta(minutes=minutes)).time()]
    return len(actual) == minutes and set(actual) == expected


def _evaluate(symbol: str, bars_by_symbol: Mapping[str, Sequence[MarketBar]], *, now: datetime, evidence: dict[str, Any]) -> None:
    day = now.astimezone(EASTERN).date()
    candidate_bars = list(bars_by_symbol[symbol])
    spy_bars = list(bars_by_symbol["SPY"])
    quality, reasons = _quality_for(symbol, candidate_bars, now=now, current_day=day)
    spy_quality, spy_reasons = _quality_for("SPY", spy_bars, now=now, current_day=day)
    evidence["quality_rejection_reasons"] = reasons + spy_reasons
    if quality is not MarketDataQuality.VALID or spy_quality is not MarketDataQuality.VALID:
        evidence["quality_outcome"] = (quality if quality is not MarketDataQuality.VALID else spy_quality).value
        evidence["no_signal_or_rejection_reason"] = "DATA_INVALID"
        return
    config = load_config()
    opening_minutes = int(config["signal"]["opening_range_minutes"])
    if not _complete_opening_range(candidate_bars, day=day, minutes=opening_minutes):
        evidence["quality_outcome"] = MarketDataQuality.MISSING_OPENING_RANGE_BARS.value
        evidence["quality_rejection_reasons"] = [MarketDataQuality.MISSING_OPENING_RANGE_BARS.value]
        evidence["no_signal_or_rejection_reason"] = "DATA_INCOMPLETE"
        return
    candidate = sorted((bar.to_minute_bar() for bar in candidate_bars), key=lambda bar: bar.begins_at)
    spy = sorted((bar.to_minute_bar() for bar in spy_bars if bar.event_timestamp.astimezone(EASTERN).date() == day), key=lambda bar: bar.begins_at)
    try:
        opening = calculate_opening_range(candidate, session_date=day, opening_range_minutes=opening_minutes, session_open=session_open_for(day))
        median_volume = calculate_median_opening_volume(candidate, before_date=day, lookback_days=int(config["signal"]["relative_volume_lookback_days"]), opening_range_minutes=opening_minutes, session_open_for=session_open_for)
        current_candidate = [bar for bar in candidate if bar.begins_at.astimezone(EASTERN).date() == day]
        historical = [bar for bar in candidate if bar.begins_at.astimezone(EASTERN).date() < day]
        if not current_candidate or not historical or not spy:
            raise ValueError("required strategy inputs are absent")
        live_bar = max(current_candidate, key=lambda bar: bar.begins_at)
        prior_close = max(historical, key=lambda bar: bar.begins_at).close_price
        opening_data = OpeningData(symbol=symbol, previous_close=prior_close, today_open=current_candidate[0].open_price, opening_range_high=opening.high_price, opening_range_low=opening.low_price, opening_range_volume=opening.volume, median_opening_volume=median_volume)
        live_data = LiveData(timestamp=live_bar.begins_at + timedelta(minutes=1), completed_bar_close=live_bar.close_price, spy_price=spy[-1].close_price, spy_vwap=calculate_estimated_vwap(spy))
    except (ValueError, KeyError, IndexError):
        evidence["quality_outcome"] = MarketDataQuality.INCOMPLETE_RESPONSE.value
        evidence["no_signal_or_rejection_reason"] = "STRATEGY_INPUT_INCOMPLETE"
        return
    evaluation_config = copy.deepcopy(config)
    evaluation_config["strategy"]["enabled"] = True  # local shadow evaluation only; persisted authority remains false
    strategy_version = f"{config['strategy']['name']}-{config['strategy']['version']}"
    ordered = candidate_bars + spy_bars
    digests = [compute_market_data_digest(bar, normalization_version=NORMALIZATION_VERSION) for bar in ordered]
    evidence["market_data_digest"] = compute_market_data_batch_digest(digests)
    opening_identity = hashlib.sha256((symbol + "|" + day.isoformat() + "|" + str(opening.high_price) + "|" + str(opening.low_price)).encode("utf-8")).hexdigest()
    decision = build_shadow_trading_decision(shadow_run_id=evidence["run_id"], strategy_version=strategy_version, implementation_digest=evidence["implementation_digest"], market_data_digest=evidence["market_data_digest"], opening_range_identity=opening_identity, exchange_session="REGULAR", market_data_quality=MarketDataQuality.VALID, signal_config=evaluation_config, opening=opening_data, live=live_data, created_at=now)
    evidence.update(quality_outcome=MarketDataQuality.VALID.value, opening_range_identity=opening_identity, strategy_version=strategy_version, shadow_trading_decision=decision, proposed_entry=decision.proposed_entry, proposed_stop=decision.proposed_stop, no_signal_or_rejection_reason=None if decision.is_candidate else decision.reason)


async def _fetch(provider: AlpacaMarketDataProvider, *, symbol: str, start: datetime, end: datetime, attempts: int, sleep: Any = asyncio.sleep) -> list[MarketBar]:
    for attempt in range(attempts):
        try:
            return list(await provider.fetch_minute_bars(symbol=symbol, start=start, end=end))
        except MarketDataProviderError as error:
            if "(429)" in str(error) and attempt + 1 < attempts:
                await sleep(0.01)
                continue
            raise
    raise AssertionError("unreachable")


async def run_session(*, mode: RunMode = RunMode.OFFLINE_FIXTURE, candidate_symbol: str = "AAPL", output_dir: Path = DEFAULT_EVIDENCE_DIR, now: datetime | None = None, environment: Mapping[str, str] | None = None, http_client: Any | None = None, limits: SessionLimits = SessionLimits(), production_confirmed: bool = False, root: Path = REPOSITORY_ROOT) -> tuple[dict[str, Any], Path]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    symbol = candidate_symbol.upper().strip()
    symbols = (symbol,) if mode is RunMode.PRODUCTION_SMOKE_READ else (symbol, "SPY")
    if not symbol or len(set(symbols)) > limits.max_symbols:
        raise LiveShadowError("symbol scope exceeds the bounded limit")
    evidence = _base_evidence(mode=mode, symbols=symbols, started_at=moment, root=root)
    transport: BudgetedHttpClient | None = None
    try:
        if mode is RunMode.OFFLINE_FIXTURE:
            bars_by_symbol = deterministic_fixture(moment)
            fixture_classification = classify(moment, today=moment.astimezone(EASTERN).date())
            evidence["session_identity"] = {"session": fixture_classification.session.value, "reason": fixture_classification.reason.value, "is_regular_session": fixture_classification.is_regular_session}
            evidence["normalized_bar_count"] = sum(len(items) for items in bars_by_symbol.values())
            _evaluate(symbol, bars_by_symbol, now=moment, evidence=evidence)
        else:
            if not production_confirmed:
                raise LiveShadowError("production mode requires explicit confirmation")
            classification = classify(moment, today=moment.astimezone(EASTERN).date())
            evidence["session_identity"] = {"session": classification.session.value, "reason": classification.reason.value, "is_regular_session": classification.is_regular_session}
            credentials = _credentials_from_environment(environment if environment is not None else os.environ)
            transport = BudgetedHttpClient(http_client or UrllibMarketDataHttpClient(timeout_seconds=limits.timeout_seconds), max_requests=limits.max_requests)
            provider = AlpacaMarketDataProvider(credentials=credentials, http_client=transport, now=lambda: moment)
            day = moment.astimezone(EASTERN).date()
            if mode is RunMode.PRODUCTION_SMOKE_READ:
                smoke_start, smoke_end = _smoke_read_window(moment)
                bars = await _fetch(provider, symbol=symbol, start=smoke_start, end=smoke_end, attempts=limits.max_attempts_per_fetch)
                quality, reasons = _quality_for(symbol, bars, now=moment, current_day=day)
                evidence["provider_diagnostics"] = {
                    "endpoint_path": f"/v2/stocks/{symbol}/bars",
                    "requested_symbol": symbol,
                    "requested_timeframe": "1Min",
                    "requested_start": smoke_start,
                    "requested_end": smoke_end,
                    **provider.last_response_diagnostics,
                }
                evidence.update(normalized_bar_count=len(bars), request_count=transport.request_count, market_data_digest=compute_market_data_batch_digest([compute_market_data_digest(bar, normalization_version=NORMALIZATION_VERSION) for bar in bars]) if bars else None, quality_outcome=quality.value, quality_rejection_reasons=reasons, no_signal_or_rejection_reason="PRODUCTION_MARKET_DATA_SMOKE_READ")
            else:
                if classification.session is not CalendarSession.REGULAR or not classification.is_regular_session:
                    evidence.update(quality_outcome="OUTSIDE_REGULAR_SESSION", no_signal_or_rejection_reason="LIVE_SHADOW_REQUIRES_REGULAR_SESSION")
                else:
                    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
                    historical_window, current_window = _live_candidate_windows(day, moment, lookback_days=lookback_days)
                    historical_bars = await _fetch(provider, symbol=symbol, start=historical_window[0], end=historical_window[1], attempts=limits.max_attempts_per_fetch)
                    historical_diagnostics = dict(provider.last_response_diagnostics)
                    current_bars = await _fetch(provider, symbol=symbol, start=current_window[0], end=current_window[1], attempts=limits.max_attempts_per_fetch)
                    current_diagnostics = dict(provider.last_response_diagnostics)
                    spy_start = datetime.combine(day, session_open_for(day), tzinfo=EASTERN).astimezone(timezone.utc)
                    spy_bars = await _fetch(provider, symbol="SPY", start=spy_start, end=moment, attempts=limits.max_attempts_per_fetch)
                    spy_diagnostics = dict(provider.last_response_diagnostics)
                    candidate_bars = historical_bars + current_bars
                    candidate_eligible, candidate_counts, candidate_fatal = _live_eligible_bars(symbol, candidate_bars, now=moment, current_day=day)
                    spy_eligible, spy_counts, spy_fatal = _live_eligible_bars("SPY", spy_bars, now=moment, current_day=day)
                    counts = Counter(candidate_counts) + Counter(spy_counts)
                    evidence.update(normalized_bar_count=len(candidate_bars) + len(spy_bars), request_count=transport.request_count, provider_diagnostics={"datasets": [{"symbol": symbol, "requested_start": historical_window[0], "requested_end": historical_window[1], **historical_diagnostics}, {"symbol": symbol, "requested_start": current_window[0], "requested_end": current_window[1], **current_diagnostics}, {"symbol": "SPY", "requested_start": spy_start, "requested_end": moment, **spy_diagnostics}]}, quality_rejection_counts=dict(sorted(counts.items())), quality_rejection_reasons=sorted(counts))
                    if candidate_fatal or spy_fatal:
                        evidence.update(quality_outcome=(candidate_fatal + spy_fatal)[0], no_signal_or_rejection_reason="DATA_INVALID")
                    else:
                        _evaluate(symbol, {symbol: candidate_eligible, "SPY": spy_eligible}, now=moment, evidence=evidence)
                        evidence["quality_rejection_counts"] = dict(sorted(counts.items()))
                        evidence["quality_rejection_reasons"] = sorted(counts)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
            evidence["errors"].append("RUN_CANCELLED")
            evidence["no_signal_or_rejection_reason"] = "RUN_CANCELLED"
        else:
            evidence["errors"].append(_safe_error(error))
            evidence["no_signal_or_rejection_reason"] = evidence["no_signal_or_rejection_reason"] or evidence["errors"][-1]
        if evidence["quality_outcome"] == "NOT_EVALUATED":
            evidence["quality_outcome"] = "FAILED_CLOSED"
    if transport is not None:
        evidence["request_count"] = transport.request_count
    evidence["completed_at"] = moment
    evidence["broker_write_count"] = 0
    return evidence, persist_evidence(evidence, output_dir=output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded read-only Alpaca ORB shadow session")
    parser.add_argument("--mode", choices=[mode.value for mode in RunMode], default=RunMode.OFFLINE_FIXTURE.value)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--production", action="store_true", help="explicitly permit read-only Alpaca market-data access")
    args = parser.parse_args(argv)
    try:
        evidence, path = asyncio.run(run_session(mode=RunMode(args.mode), candidate_symbol=args.symbol, output_dir=args.output_dir, production_confirmed=args.production))
    except LiveShadowError:
        return 2
    print(path)
    return 0 if not evidence["errors"] else 2


__all__ = ["BudgetedHttpClient", "DEFAULT_EVIDENCE_DIR", "LiveShadowError", "RunMode", "SessionLimits", "UrllibMarketDataHttpClient", "deterministic_fixture", "main", "persist_evidence", "run_session"]