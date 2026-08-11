"""Canonical shadow (observational, non-executing) ORB trading decision
model for M15 Issue 1.

`ShadowTradingDecision` is evidence, never an order and never a broker
authorization. It is structurally incapable of being mistaken for
`src.execution_safety_governance.ExecutionSafetyDecision`: it shares no
base class, no field name for its own identity/outcome, carries no
`account_fingerprint`/`order_id`/broker-write-boundary fields at all, and
is never constructed by, passed to, or accepted by
`src.robinhood_commissioning_adapter.RobinhoodCommissioningAdapter`,
`src.one_share_commissioning_orchestrator.OneShareCommissioningOrchestrator`,
or `src.execution_safety_governance.evaluate_execution_safety` (see
`tests/test_market_data_shadow_firewall.py` for the structural proof).

`build_shadow_trading_decision` wraps `src.signal_engine.SignalEngine`
unchanged -- the actual ORB V0 signal logic is not duplicated here, only
packaged with the identity/digest/quality metadata M15 needs for
reproducible evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from src.market_data_canonical import MarketDataQuality
from src.protection_observation_model import canonical_bytes
from src.signal_engine import LiveData, OpeningData, SignalDecision, SignalEngine


class ShadowTradingDecisionError(RuntimeError):
    """This module's own construction contract was violated."""


_DIGEST_DOMAIN = b"orb-live/shadow-trading-decision/v1"


class ShadowDecisionType(str, Enum):
    """What this decision asserts. `LONG_CANDIDATE`/`SHORT_CANDIDATE` are
    both declared even though ORB V0 (config/strategy_v0.yaml) is long-only
    today -- the vocabulary should not need to change the day a short
    variant is added. `REJECTED` is a real, evaluated NO (the signal engine
    ran and said no); `DATA_INVALID` means market data quality prevented
    evaluation from running at all -- these are never conflated."""

    NO_SIGNAL = "NO_SIGNAL"
    LONG_CANDIDATE = "LONG_CANDIDATE"
    SHORT_CANDIDATE = "SHORT_CANDIDATE"
    REJECTED = "REJECTED"
    DATA_INVALID = "DATA_INVALID"


def _require_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowTradingDecisionError(f"{label} is required")
    return value


def _require_aware(value: Any, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise ShadowTradingDecisionError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ShadowTradingDecisionError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True)
class ShadowTradingDecision:
    """One evidence-grade, non-executable ORB shadow decision. Frozen and
    self-contained: reproducing `decision_id` from every other field is
    sufficient to prove this record was not altered after the fact (see
    `compute_shadow_decision_digest`)."""

    shadow_run_id: str
    decision_id: str
    symbol: str
    trading_date: date
    strategy_version: str
    implementation_digest: str
    market_data_digest: str
    opening_range_identity: str
    signal_timestamp: datetime
    decision_type: ShadowDecisionType
    proposed_entry: Decimal | None
    proposed_stop: Decimal | None
    proposed_target: Decimal | None
    reason: str
    market_data_quality: MarketDataQuality
    exchange_session: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_str(self.shadow_run_id, label="shadow_run_id")
        _require_str(self.decision_id, label="decision_id")
        _require_str(self.symbol, label="symbol")
        if self.symbol != self.symbol.upper().strip():
            raise ShadowTradingDecisionError("symbol must be normalized upper-case")
        if not isinstance(self.trading_date, date) or isinstance(self.trading_date, datetime):
            raise ShadowTradingDecisionError("trading_date must be a plain date")
        _require_str(self.strategy_version, label="strategy_version")
        _require_str(self.implementation_digest, label="implementation_digest")
        _require_str(self.market_data_digest, label="market_data_digest")
        _require_str(self.opening_range_identity, label="opening_range_identity")
        _require_aware(self.signal_timestamp, label="signal_timestamp")
        if not isinstance(self.decision_type, ShadowDecisionType):
            raise ShadowTradingDecisionError("decision_type must be a ShadowDecisionType")
        for label, value in (
            ("proposed_entry", self.proposed_entry),
            ("proposed_stop", self.proposed_stop),
            ("proposed_target", self.proposed_target),
        ):
            if value is not None and not isinstance(value, Decimal):
                raise ShadowTradingDecisionError(f"{label} must be a Decimal or None")
        if not isinstance(self.reason, str):
            raise ShadowTradingDecisionError("reason must be a string")
        if not isinstance(self.market_data_quality, MarketDataQuality):
            raise ShadowTradingDecisionError(
                "market_data_quality must be a MarketDataQuality"
            )
        _require_str(self.exchange_session, label="exchange_session")
        _require_aware(self.created_at, label="created_at")
        # Structural firewall: this type must never carry anything that
        # would let it be confused for a broker write or an
        # ExecutionSafetyDecision. Asserted here, not only by the absence
        # of such fields in the dataclass definition above, so a future
        # edit that added one back would fail loudly at every construction
        # site, not just in a review that might miss it.
        forbidden_attrs = ("order_id", "account_fingerprint", "broker_call_tool", "decision_digest")
        for attr in forbidden_attrs:
            if hasattr(self, attr):
                raise ShadowTradingDecisionError(
                    f"ShadowTradingDecision must never carry {attr!r}"
                )

    @property
    def is_candidate(self) -> bool:
        return self.decision_type in (
            ShadowDecisionType.LONG_CANDIDATE, ShadowDecisionType.SHORT_CANDIDATE,
        )


def compute_shadow_decision_digest(
    *,
    shadow_run_id: str,
    symbol: str,
    trading_date: date,
    strategy_version: str,
    implementation_digest: str,
    market_data_digest: str,
    opening_range_identity: str,
    signal_timestamp: datetime,
    decision_type: ShadowDecisionType,
    proposed_entry: Decimal | None,
    proposed_stop: Decimal | None,
    proposed_target: Decimal | None,
    reason: str,
    market_data_quality: MarketDataQuality,
    exchange_session: str,
) -> str:
    """Deterministic digest over every field that determines what this
    decision asserts, including `shadow_run_id` (identity input). Excludes
    `created_at` (when this process happened to record the decision, not a
    fact about the decision's content) and `decision_id` itself, which is
    not a parameter here because it IS this digest's own output -- the
    caller sets `decision_id` equal to it, the same pattern `execution_
    safety_governance.evaluate_execution_safety` uses for
    `decision_id`/`decision_digest`."""
    payload = {
        "shadow_run_id": shadow_run_id,
        "symbol": symbol,
        "trading_date": trading_date.isoformat(),
        "strategy_version": strategy_version,
        "implementation_digest": implementation_digest,
        "market_data_digest": market_data_digest,
        "opening_range_identity": opening_range_identity,
        "signal_timestamp": signal_timestamp.isoformat(),
        "decision_type": decision_type.value,
        "proposed_entry": format(proposed_entry, "f") if proposed_entry is not None else "",
        "proposed_stop": format(proposed_stop, "f") if proposed_stop is not None else "",
        "proposed_target": format(proposed_target, "f") if proposed_target is not None else "",
        "reason": reason,
        "market_data_quality": market_data_quality.value,
        "exchange_session": exchange_session,
    }
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00decision\x00" + canonical_bytes(payload)
    ).hexdigest()


def build_shadow_trading_decision(
    *,
    shadow_run_id: str,
    strategy_version: str,
    implementation_digest: str,
    market_data_digest: str,
    opening_range_identity: str,
    exchange_session: str,
    market_data_quality: MarketDataQuality,
    signal_config: dict[str, Any],
    opening: OpeningData,
    live: LiveData,
    created_at: datetime,
    profit_target_r: Decimal | None = None,
    evaluated_signal_decision: SignalDecision | None = None,
) -> ShadowTradingDecision:
    """The one place M15 wraps `SignalEngine.evaluate()`. If market data
    quality is not VALID, the signal engine is never even called -- a
    DATA_INVALID decision is recorded instead, exactly the same fail-closed
    posture `execution_safety_governance.py` uses (a gate that cannot be
    evaluated is exactly as denying as a gate that was evaluated and
    failed). `profit_target_r`, if given, computes a proposed_target as
    entry + profit_target_r * (entry - stop) purely for evidence purposes
    -- src.exit_engine.py, not this function, is the actual authority on
    exit management once (if ever) a position is real."""
    if market_data_quality is not MarketDataQuality.VALID:
        decision = SignalDecision(approved=False, reason=f"Market data quality is {market_data_quality.value}.")
        decision_type = ShadowDecisionType.DATA_INVALID
    else:
        decision = evaluated_signal_decision or SignalEngine(signal_config).evaluate(opening, live)
        decision_type = (
            ShadowDecisionType.LONG_CANDIDATE if decision.approved
            else ShadowDecisionType.REJECTED
        )

    proposed_entry = decision.entry_price if decision.approved else None
    proposed_stop = decision.stop_price if decision.approved else None
    proposed_target: Decimal | None = None
    if decision.approved and profit_target_r is not None and proposed_entry is not None and proposed_stop is not None:
        risk_per_share = proposed_entry - proposed_stop
        proposed_target = proposed_entry + profit_target_r * risk_per_share

    digest = compute_shadow_decision_digest(
        shadow_run_id=shadow_run_id,
        symbol=opening.symbol,
        trading_date=live.timestamp.date(),
        strategy_version=strategy_version,
        implementation_digest=implementation_digest,
        market_data_digest=market_data_digest,
        opening_range_identity=opening_range_identity,
        signal_timestamp=live.timestamp,
        decision_type=decision_type,
        proposed_entry=proposed_entry,
        proposed_stop=proposed_stop,
        proposed_target=proposed_target,
        reason=decision.reason,
        market_data_quality=market_data_quality,
        exchange_session=exchange_session,
    )

    return ShadowTradingDecision(
        shadow_run_id=shadow_run_id,
        decision_id=digest,
        symbol=opening.symbol,
        trading_date=live.timestamp.date(),
        strategy_version=strategy_version,
        implementation_digest=implementation_digest,
        market_data_digest=market_data_digest,
        opening_range_identity=opening_range_identity,
        signal_timestamp=live.timestamp,
        decision_type=decision_type,
        proposed_entry=proposed_entry,
        proposed_stop=proposed_stop,
        proposed_target=proposed_target,
        reason=decision.reason,
        market_data_quality=market_data_quality,
        exchange_session=exchange_session,
        created_at=created_at,
    )


__all__ = [
    "ShadowTradingDecisionError",
    "ShadowDecisionType",
    "ShadowTradingDecision",
    "compute_shadow_decision_digest",
    "build_shadow_trading_decision",
]
