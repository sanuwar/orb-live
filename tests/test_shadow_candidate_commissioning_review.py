from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_config
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.shadow_candidate_commissioning_review import (
    CandidateFreshnessStatus,
    ReviewReadinessState,
    build_shadow_candidate_commissioning_review,
)
from src.shadow_execution_simulator import (
    ShadowExecutionState,
    simulate_shadow_execution,
)
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)

DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 45, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 8, 11, 14, 50, tzinfo=timezone.utc)
TRUSTED = "e21040027b326716fe4d08a38f6deda23eb69f03a445325d65b6e63a8dac9513"


def decision(*, kind=ShadowDecisionType.LONG_CANDIDATE, entry=Decimal("100"), stop=Decimal("99"), target=None):
    proposed_entry = entry if kind is ShadowDecisionType.LONG_CANDIDATE else None
    proposed_stop = stop if kind is ShadowDecisionType.LONG_CANDIDATE else None
    digest = compute_shadow_decision_digest(
        shadow_run_id="bridge-fixture", symbol="AAPL", trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest=TRUSTED,
        market_data_digest="b" * 64, opening_range_identity="d" * 64,
        signal_timestamp=SIGNAL, decision_type=kind, proposed_entry=proposed_entry,
        proposed_stop=proposed_stop, proposed_target=target, reason="fixture",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        shadow_run_id="bridge-fixture", decision_id=digest, symbol="AAPL",
        trading_date=DAY, strategy_version="orb_long_v0-0.1.0",
        implementation_digest=TRUSTED, market_data_digest="b" * 64,
        opening_range_identity="d" * 64, signal_timestamp=SIGNAL,
        decision_type=kind, proposed_entry=proposed_entry, proposed_stop=proposed_stop,
        proposed_target=target, reason="fixture", market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR", created_at=SIGNAL,
    )


def bar(minute, *, high="100.5", low="99.5", close="100"):
    timestamp = SIGNAL + timedelta(minutes=minute)
    return MarketBar(
        symbol="AAPL", exchange_session="REGULAR", trading_date=DAY,
        event_timestamp=timestamp, end_timestamp=timestamp + timedelta(minutes=1),
        receive_timestamp=timestamp + timedelta(seconds=1), open=Decimal("100"),
        high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=100,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None,
        quality=MarketDataQuality.VALID,
    )


def review(item, *, simulation=None, when=REVIEWED, trusted=TRUSTED):
    return build_shadow_candidate_commissioning_review(
        item, reviewed_at=when, simulation=simulation,
        trusted_implementation_digest=trusted,
    )


def test_default_trusted_digest_is_reconstructed_without_network_or_credentials():
    result = build_shadow_candidate_commissioning_review(decision(), reviewed_at=REVIEWED)
    assert result.readiness_state is ReviewReadinessState.REVIEW_READY
    assert result.trusted_implementation_digest == TRUSTED

def test_authentic_fresh_long_candidate_is_review_ready_and_not_execution_approved():
    result = review(decision())
    assert result.readiness_state is ReviewReadinessState.REVIEW_READY
    assert result.candidate_freshness_status is CandidateFreshnessStatus.FRESH
    assert result.protection_plan_complete is True
    assert result.execution_approval_granted is False
    assert result.broker_write_count == 0
    assert result.proposed_target == Decimal("102.0")


@pytest.mark.parametrize("kind", [
    ShadowDecisionType.REJECTED, ShadowDecisionType.NO_SIGNAL, ShadowDecisionType.DATA_INVALID,
])
def test_non_candidates_are_blocked(kind):
    result = review(decision(kind=kind))
    assert result.readiness_state is ReviewReadinessState.INVALID_CANDIDATE
    assert "DECISION_NOT_LONG_CANDIDATE" in result.blocking_reasons


@pytest.mark.parametrize("attribute, expected", [
    ("decision_id", "DECISION_DIGEST_MISMATCH"),
    ("opening_range_identity", "MISSING_OPENING_RANGE_IDENTITY"),
    ("market_data_digest", "MISSING_MARKET_DATA_DIGEST"),
    ("proposed_stop", "DECISION_ENTRY_STOP_INVALID"),
])
def test_tampered_or_incomplete_candidate_fails_closed(attribute, expected):
    item = decision()
    object.__setattr__(item, attribute, "" if "identity" in attribute or "digest" in attribute else None)
    result = review(item)
    assert result.readiness_state is ReviewReadinessState.INVALID_CANDIDATE
    assert expected in result.blocking_reasons
    if attribute == "proposed_stop":
        assert result.protection_plan_complete is False


def test_wrong_implementation_digest_and_invalid_price_direction_are_blocked():
    assert "IMPLEMENTATION_DIGEST_MISMATCH" in review(decision(), trusted="f" * 64).blocking_reasons
    result = review(decision(stop=Decimal("101")))
    assert result.readiness_state is ReviewReadinessState.INVALID_CANDIDATE
    assert "DECISION_ENTRY_STOP_INVALID" in result.blocking_reasons


def test_stale_candidate_is_not_reviewable_on_a_later_trading_date():
    result = review(decision(), when=datetime(2026, 8, 12, 14, 50, tzinfo=timezone.utc))
    assert result.readiness_state is ReviewReadinessState.STALE
    assert "CANDIDATE_TRADING_DATE_STALE" in result.blocking_reasons


def test_matching_simulation_is_advisory_and_losing_outcome_does_not_block_review():
    item = decision()
    losing = simulate_shadow_execution(item, [bar(1), bar(2, low="98.9", high="100.2")])
    assert losing.state is ShadowExecutionState.STOPPED
    result = review(item, simulation=losing)
    assert result.readiness_state is ReviewReadinessState.REVIEW_READY
    assert result.source_simulation_id == losing.simulation_id
    assert result.simulation_realized_pnl == Decimal("-1")
    assert result.execution_approval_granted is False


def test_profitable_simulation_cannot_increase_authority():
    item = decision()
    profitable = simulate_shadow_execution(item, [bar(1), bar(2, high="102.1", low="99.5", close="100.5")])
    assert profitable.state is ShadowExecutionState.TARGET_HIT
    result = review(item, simulation=profitable)
    assert result.readiness_state is ReviewReadinessState.REVIEW_READY
    assert result.execution_approval_granted is False
    assert result.broker_write_count == 0


def test_mismatched_or_ineligible_simulation_is_rejected_as_advisory_evidence():
    item = decision()
    matching = simulate_shadow_execution(item, [bar(1)])
    mismatched = replace(matching, symbol="MSFT")
    result = review(item, simulation=mismatched)
    assert result.readiness_state is ReviewReadinessState.SIMULATION_MISMATCH
    assert "SIMULATION_CANDIDATE_FIELDS_MISMATCH" in result.blocking_reasons
    invalid = replace(matching, state=ShadowExecutionState.NOT_ELIGIBLE)
    result = review(item, simulation=invalid)
    assert result.readiness_state is ReviewReadinessState.SIMULATION_MISMATCH
    assert "SIMULATION_NOT_VALID_ADVISORY_EVIDENCE" in result.blocking_reasons


def test_review_digest_is_deterministic_and_binds_review_time():
    first = review(decision())
    second = review(decision())
    later = review(decision(), when=REVIEWED + timedelta(minutes=1))
    assert first == second and first.review_id == first.review_digest
    assert first.review_digest != later.review_digest


def test_bridge_has_no_broker_or_write_reachability():
    source = Path(__file__).resolve().parents[1] / "src/shadow_candidate_commissioning_review.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    forbidden = {
        "RobinhoodClient", "RobinhoodCommissioningAdapter",
        "RobinhoodCommissioningSessionBinding", "OneShareCommissioningOrchestrator",
        "ExecutionSafetyDecision", "ExecutionSafetyContext", "call_tool",
        "place_equity_order", "cancel_equity_order", "flatten_position",
    }
    assert not (names & forbidden)