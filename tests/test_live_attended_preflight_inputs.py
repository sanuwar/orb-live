from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.account_state import AccountInfo, AccountState, PortfolioState
from src.broker_snapshot import account_fingerprint
from src.durable_scheduler import SchedulerAction
from src.durable_suspension_controller import (
    ControllerState, SuspensionCause, SuspensionDecision,
)
from src.market_data_canonical import MarketDataQuality
from src.shadow_candidate_commissioning_review import (
    build_shadow_candidate_commissioning_review,
)
from src.shadow_trading_decision import (
    ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest,
)
from src.supervised_micro_live_dry_run import build_supervised_micro_live_dry_run_plan
from src.supervised_micro_live_preflight import (
    PreflightInputs, build_supervised_micro_live_preflight,
)
from src.live_attended_preflight_inputs import (
    LiveAttendedPreflightState, SuspensionObservation,
    build_live_attended_preflight_result,
    compute_live_attended_preflight_digest,
)
from tests.snapshot_helpers import make_broker_snapshot

DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 45, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 11, 14, 50, tzinfo=timezone.utc)
TRUSTED = "e21040027b326716fe4d08a38f6deda23eb69f03a445325d65b6e63a8dac9513"


def candidate() -> ShadowTradingDecision:
    digest = compute_shadow_decision_digest(
        shadow_run_id="m17-2", symbol="AAPL", trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest=TRUSTED,
        market_data_digest="b" * 64, opening_range_identity="d" * 64,
        signal_timestamp=SIGNAL, decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=Decimal("100"), proposed_stop=Decimal("99"),
        proposed_target=Decimal("102"), reason="fixture",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        "m17-2", digest, "AAPL", DAY, "orb_long_v0-0.1.0", TRUSTED,
        "b" * 64, "d" * 64, SIGNAL, ShadowDecisionType.LONG_CANDIDATE,
        Decimal("100"), Decimal("99"), Decimal("102"), "fixture",
        MarketDataQuality.VALID, "REGULAR", SIGNAL,
    )


def ready_preflight():
    review = build_shadow_candidate_commissioning_review(
        candidate(), reviewed_at=NOW, trusted_implementation_digest=TRUSTED,
    )
    dry_run = build_supervised_micro_live_dry_run_plan(
        review, trusted_implementation_digest=TRUSTED,
    )
    return build_supervised_micro_live_preflight(
        review, dry_run,
        inputs=PreflightInputs(
            evaluated_at=NOW, acknowledgements=(True, True, True, True),
            source_hash_verified=True, release_sha="a" * 40,
            account_fingerprint_matched=True,
            account_fingerprint_reference="b" * 64,
            suspension_state="CLEAR",
        ),
        trusted_implementation_digest=TRUSTED,
    )


def snapshot(*, completed_at=NOW - timedelta(seconds=1), fingerprint=None):
    account = AccountState(
        account=AccountInfo(
            account_number="TEST12345678", masked_account_number="test-account",
            brokerage_account_type="individual", trading_type="cash", nickname="Test",
            agentic_allowed=True, state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("0"), equity_value=Decimal("0"), cash=Decimal("0"),
            pending_deposits=Decimal("0"), buying_power=Decimal("0"),
            unleveraged_buying_power=Decimal("0"), intraday_buying_power=None, currency="USD",
        ), positions=(), open_orders=(),
    )
    return make_broker_snapshot(
        account, started_at=completed_at - timedelta(milliseconds=3),
        positions_read_at=completed_at - timedelta(milliseconds=2),
        orders_read_at=completed_at - timedelta(milliseconds=1), completed_at=completed_at,
        account_fingerprint_value=fingerprint,
        reconciliation_id="snapshot-fixture",
    )


def clear_observation(*, observed_at=NOW):
    return SuspensionObservation(
        controller_id="commissioning-suspension-fixture", observed_at=observed_at,
        decision=SuspensionDecision(
            ControllerState.CLEAR, SuspensionCause.NONE, False,
            SchedulerAction.REQUIRE_OPERATOR_REVIEW,
        ),
    )


def result(**changes):
    item = snapshot()
    expected = account_fingerprint("TEST12345678")
    return build_live_attended_preflight_result(
        ready_preflight(), evaluated_at=NOW, trusted_implementation_digest=TRUSTED,
        **{"snapshot": item, "expected_account_fingerprint": expected,
           "suspension_observation": clear_observation(), **changes},
    )


def test_matching_valid_snapshot_and_fresh_clear_satisfy_only_read_only_prerequisites():
    item = result()
    assert item.result_state is LiveAttendedPreflightState.LIVE_PREFLIGHT_READY
    assert item.account_fingerprint_observation_status == "MATCHED"
    assert item.suspension_observation_status == "CLEAR"
    assert item.operator_acknowledgements_status == "REQUIRED_AT_ATTENDED_GATE"
    assert item.execution_authorized is False and item.protection_grounded is False
    assert item.broker_write_count == 0


def test_missing_stale_or_invalid_snapshot_blocks():
    expected = account_fingerprint("TEST12345678")
    missing = build_live_attended_preflight_result(
        ready_preflight(), snapshot=None, expected_account_fingerprint=expected,
        suspension_observation=clear_observation(), evaluated_at=NOW,
        trusted_implementation_digest=TRUSTED,
    )
    stale = build_live_attended_preflight_result(
        ready_preflight(), snapshot=snapshot(completed_at=NOW - timedelta(seconds=31)),
        expected_account_fingerprint=expected, suspension_observation=clear_observation(),
        evaluated_at=NOW, trusted_implementation_digest=TRUSTED,
    )
    assert missing.result_state is LiveAttendedPreflightState.LIVE_PREFLIGHT_BLOCKED
    assert stale.account_fingerprint_observation_status == "INVALID"


def test_snapshot_fingerprint_mismatch_blocks_fail_closed():
    item = result(expected_account_fingerprint="f" * 64)
    assert item.account_fingerprint_observation_status == "MISMATCH"
    assert "ACCOUNT_IDENTITY_MISMATCH" in item.blocking_reasons


def test_suspended_or_stale_assessment_blocks_without_synthetic_clear():
    suspended = SuspensionObservation(
        controller_id="commissioning-suspension-fixture", observed_at=NOW,
        decision=SuspensionDecision(
            ControllerState.SUSPENDED, SuspensionCause.OPERATOR_HALT, True,
            SchedulerAction.SUSPEND,
        ),
    )
    blocked = result(suspension_observation=suspended)
    stale = result(suspension_observation=clear_observation(observed_at=NOW - timedelta(seconds=31)))
    assert blocked.suspension_observation_status == "NOT_CLEAR"
    assert stale.suspension_observation_status == "STALE"
    assert blocked.result_state is LiveAttendedPreflightState.LIVE_PREFLIGHT_BLOCKED


def test_tampered_or_stale_source_preflight_blocks():
    source = ready_preflight()
    object.__setattr__(source, "market_data_digest", "x" * 64)
    item = build_live_attended_preflight_result(
        source, snapshot=snapshot(), expected_account_fingerprint=account_fingerprint("TEST12345678"),
        suspension_observation=clear_observation(), evaluated_at=NOW,
        trusted_implementation_digest=TRUSTED,
    )
    assert "PREFLIGHT_DIGEST_MISMATCH" in item.blocking_reasons


def test_result_identity_is_deterministic_and_has_no_broker_reachability():
    first, second = result(), result()
    assert first == second
    assert compute_live_attended_preflight_digest(first) == first.live_preflight_digest
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/live_attended_preflight_inputs.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "RobinhoodClient", "place_equity_order", "cancel_equity_order", "call_tool", "ExecutionSafetyDecision"})
