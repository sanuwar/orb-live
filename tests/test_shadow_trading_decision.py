"""M15 Issue 1: ShadowTradingDecision model and the SignalEngine-wrapping
builder.

Every fixture is local and offline; SignalEngine itself is exercised
unchanged (not reimplemented) via `build_shadow_trading_decision`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.market_data_canonical import MarketDataQuality
from src.signal_engine import LiveData, OpeningData
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    ShadowTradingDecisionError,
    build_shadow_trading_decision,
    compute_shadow_decision_digest,
)

ET = ZoneInfo("America/New_York")

SIGNAL_CONFIG = {
    "strategy": {"enabled": True},
    "signal": {
        "entry_start": "09:40", "entry_end": "11:00",
        "minimum_gap_percent": 1.0, "minimum_relative_volume": 3.0,
        "require_spy_above_vwap": True,
    },
    "universe": {"symbols": ["AAPL"]},
}


def _opening(**overrides) -> OpeningData:
    base = dict(
        symbol="AAPL", previous_close=Decimal("200"), today_open=Decimal("203"),
        opening_range_high=Decimal("204"), opening_range_low=Decimal("202"),
        opening_range_volume=3_000_000, median_opening_volume=Decimal("1000000"),
    )
    base.update(overrides)
    return OpeningData(**base)


def _live(**overrides) -> LiveData:
    base = dict(
        timestamp=datetime(2026, 8, 11, 9, 45, tzinfo=ET),
        completed_bar_close=Decimal("204.10"), spy_price=Decimal("690"),
        spy_vwap=Decimal("689"),
    )
    base.update(overrides)
    return LiveData(**base)


def _build(**overrides) -> ShadowTradingDecision:
    base = dict(
        shadow_run_id="shadow-run-1", strategy_version="orb_long_v0-0.1.0",
        implementation_digest="a" * 64, market_data_digest="b" * 64,
        opening_range_identity="c" * 64, exchange_session="REGULAR",
        market_data_quality=MarketDataQuality.VALID, signal_config=SIGNAL_CONFIG,
        opening=_opening(), live=_live(), created_at=datetime.now(timezone.utc),
        profit_target_r=Decimal("2.0"),
    )
    base.update(overrides)
    return build_shadow_trading_decision(**base)


def test_approved_signal_produces_long_candidate_with_target():
    decision = _build()
    assert decision.decision_type is ShadowDecisionType.LONG_CANDIDATE
    assert decision.proposed_entry == Decimal("204.10")
    assert decision.proposed_stop == Decimal("202")
    assert decision.proposed_target == Decimal("208.30")
    assert decision.is_candidate is True


def test_rejected_signal_produces_rejected_with_no_prices():
    decision = _build(live=_live(completed_bar_close=Decimal("203.00")))  # below ORB high
    assert decision.decision_type is ShadowDecisionType.REJECTED
    assert decision.proposed_entry is None
    assert decision.proposed_stop is None
    assert decision.proposed_target is None
    assert decision.is_candidate is False


def test_invalid_market_data_produces_data_invalid_without_calling_signal_engine():
    decision = _build(market_data_quality=MarketDataQuality.STALE)
    assert decision.decision_type is ShadowDecisionType.DATA_INVALID
    assert decision.market_data_quality is MarketDataQuality.STALE
    assert decision.proposed_entry is None


def test_decision_digest_deterministic():
    a = _build()
    b = _build()
    assert a.decision_id == b.decision_id


def test_decision_digest_changes_with_market_data_digest():
    a = _build(market_data_digest="b" * 64)
    b = _build(market_data_digest="d" * 64)
    assert a.decision_id != b.decision_id


def test_decision_digest_changes_with_shadow_run_id():
    a = _build(shadow_run_id="run-1")
    b = _build(shadow_run_id="run-2")
    assert a.decision_id != b.decision_id


def test_no_signal_type_is_a_real_declared_state():
    # ORB V0 always returns approved=True/False (never a third state), so
    # NO_SIGNAL is reachable only via direct construction, not through
    # build_shadow_trading_decision -- documented here so its absence from
    # the builder's own output is a deliberate fact, not an oversight.
    decision = ShadowTradingDecision(
        shadow_run_id="s", decision_id="d" * 64, symbol="AAPL",
        trading_date=datetime.now(timezone.utc).date(),
        strategy_version="v", implementation_digest="a" * 64,
        market_data_digest="b" * 64, opening_range_identity="c" * 64,
        signal_timestamp=datetime.now(timezone.utc),
        decision_type=ShadowDecisionType.NO_SIGNAL,
        proposed_entry=None, proposed_stop=None, proposed_target=None,
        reason="No opening range evaluated yet.",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
        created_at=datetime.now(timezone.utc),
    )
    assert decision.decision_type is ShadowDecisionType.NO_SIGNAL
    assert decision.is_candidate is False


def test_symbol_must_be_normalized_uppercase():
    with pytest.raises(ShadowTradingDecisionError):
        ShadowTradingDecision(
            shadow_run_id="s", decision_id="d" * 64, symbol="aapl",
            trading_date=datetime.now(timezone.utc).date(),
            strategy_version="v", implementation_digest="a" * 64,
            market_data_digest="b" * 64, opening_range_identity="c" * 64,
            signal_timestamp=datetime.now(timezone.utc),
            decision_type=ShadowDecisionType.NO_SIGNAL,
            proposed_entry=None, proposed_stop=None, proposed_target=None,
            reason="", market_data_quality=MarketDataQuality.VALID,
            exchange_session="REGULAR", created_at=datetime.now(timezone.utc),
        )


def test_naive_signal_timestamp_rejected():
    with pytest.raises(ShadowTradingDecisionError):
        ShadowTradingDecision(
            shadow_run_id="s", decision_id="d" * 64, symbol="AAPL",
            trading_date=datetime.now(timezone.utc).date(),
            strategy_version="v", implementation_digest="a" * 64,
            market_data_digest="b" * 64, opening_range_identity="c" * 64,
            signal_timestamp=datetime.now(),  # naive
            decision_type=ShadowDecisionType.NO_SIGNAL,
            proposed_entry=None, proposed_stop=None, proposed_target=None,
            reason="", market_data_quality=MarketDataQuality.VALID,
            exchange_session="REGULAR", created_at=datetime.now(timezone.utc),
        )


def test_has_no_order_id_or_account_fingerprint_field():
    decision = _build()
    assert not hasattr(decision, "order_id")
    assert not hasattr(decision, "account_fingerprint")
    assert not hasattr(decision, "decision_digest")  # not the ExecutionSafetyDecision field name
