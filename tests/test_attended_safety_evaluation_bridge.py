from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from src.account_state import AccountInfo, AccountState, PortfolioState
from src.attended_safety_evaluation_bridge import (
    AttendedSafetyEvaluationState, AttendedSafetyInputs,
    build_attended_safety_evaluation_result,
    compute_attended_safety_evaluation_digest,
)
from src.broker_snapshot import account_fingerprint
from src.durable_scheduler import SchedulerAction
from src.durable_suspension_controller import (
    ControllerState, SuspensionCause, SuspensionDecision,
)
from src.execution_safety_governance import OperatorAcknowledgements
from src.live_attended_preflight_inputs import (
    SuspensionObservation, build_live_attended_preflight_result,
)
from src.market_data_canonical import MarketDataQuality
from src.shadow_candidate_commissioning_review import build_shadow_candidate_commissioning_review
from src.shadow_trading_decision import (
    ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest,
)
from src.supervised_micro_live_dry_run import build_supervised_micro_live_dry_run_plan
from src.supervised_micro_live_preflight import PreflightInputs, build_supervised_micro_live_preflight
from tests.snapshot_helpers import make_broker_snapshot

EASTERN = ZoneInfo("America/New_York")
DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 45, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 11, 10, 50, tzinfo=EASTERN)
TRUSTED = "448f1e2739eb01d8606536723c7752c0e06a86e770c655489262d94553d9e114"


def candidate() -> ShadowTradingDecision:
    digest = compute_shadow_decision_digest(
        shadow_run_id="m17-3", symbol="AAPL", trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest=TRUSTED,
        market_data_digest="b" * 64, opening_range_identity="d" * 64,
        signal_timestamp=SIGNAL, decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=Decimal("100"), proposed_stop=Decimal("99"),
        proposed_target=Decimal("102"), reason="fixture",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        "m17-3", digest, "AAPL", DAY, "orb_long_v0-0.1.0", TRUSTED,
        "b" * 64, "d" * 64, SIGNAL, ShadowDecisionType.LONG_CANDIDATE,
        Decimal("100"), Decimal("99"), Decimal("102"), "fixture",
        MarketDataQuality.VALID, "REGULAR", SIGNAL,
    )


def source_preflight():
    review = build_shadow_candidate_commissioning_review(
        candidate(), reviewed_at=NOW, trusted_implementation_digest=TRUSTED,
    )
    dry_run = build_supervised_micro_live_dry_run_plan(
        review, account_fingerprint_reference=account_fingerprint("TEST12345678"),
        trusted_implementation_digest=TRUSTED,
    )
    return build_supervised_micro_live_preflight(
        review, dry_run,
        inputs=PreflightInputs(
            evaluated_at=NOW, acknowledgements=(True, True, True, True),
            source_hash_verified=True, release_sha="a" * 40,
            account_fingerprint_matched=True,
            account_fingerprint_reference="b" * 64, suspension_state="CLEAR",
        ), trusted_implementation_digest=TRUSTED,
    )


def source_live_preflight():
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
    snap = make_broker_snapshot(
        account, reconciliation_id="m17-3-snapshot",
        started_at=NOW - timedelta(milliseconds=3),
        positions_read_at=NOW - timedelta(milliseconds=2),
        orders_read_at=NOW - timedelta(milliseconds=1), completed_at=NOW,
    )
    observation = SuspensionObservation(
        controller_id="commissioning-suspension-fixture", observed_at=NOW,
        decision=SuspensionDecision(
            ControllerState.CLEAR, SuspensionCause.NONE, False,
            SchedulerAction.REQUIRE_OPERATOR_REVIEW,
        ),
    )
    return build_live_attended_preflight_result(
        source_preflight(), snapshot=snap,
        expected_account_fingerprint=account_fingerprint("TEST12345678"),
        suspension_observation=observation, evaluated_at=NOW,
        trusted_implementation_digest=TRUSTED,
    )


def inputs(*, acknowledgements=None, attended=False, source_hash=False):
    return AttendedSafetyInputs(
        decision_time=NOW, operator_acknowledgements=acknowledgements,
        operator_attended=attended, source_hash_verified=source_hash,
        release_sha="a" * 40 if source_hash else "",
    )


def allowed_acknowledgements():
    return OperatorAcknowledgements(True, True, True, True)


def test_genuine_live_preflight_constructs_existing_context_and_allows_only_as_evidence():
    result = build_attended_safety_evaluation_result(
        source_live_preflight(),
        inputs=inputs(acknowledgements=allowed_acknowledgements(), attended=True, source_hash=True),
        trusted_implementation_digest=TRUSTED,
    )
    assert result.evaluation_state is AttendedSafetyEvaluationState.SAFETY_EVALUATED_ALLOW
    assert result.safety_result == "ALLOW"
    assert result.quantity == "1" and result.action_identity is not None
    assert result.execution_authorized is False
    assert result.protection_grounded is False and result.broker_write_count == 0


def test_missing_acknowledgements_are_incomplete_not_synthesized():
    result = build_attended_safety_evaluation_result(
        source_live_preflight(), inputs=inputs(), trusted_implementation_digest=TRUSTED,
    )
    assert result.evaluation_state is AttendedSafetyEvaluationState.SAFETY_INPUT_INCOMPLETE
    assert result.safety_result == "INCOMPLETE"
    assert result.operator_acknowledgements_status == "REQUIRED"
    assert "OPERATOR_ACK_REQUIRED" in result.blocking_reasons


def test_explicit_unsatisfied_acknowledgements_reach_existing_evaluator_as_deny():
    denied_acks = OperatorAcknowledgements(False, True, True, True)
    result = build_attended_safety_evaluation_result(
        source_live_preflight(),
        inputs=inputs(acknowledgements=denied_acks, attended=True, source_hash=True),
        trusted_implementation_digest=TRUSTED,
    )
    assert result.evaluation_state is AttendedSafetyEvaluationState.SAFETY_EVALUATED_DENY
    assert "DENY_OPERATOR_ACKNOWLEDGEMENT_MISSING" in result.blocking_reasons
    assert result.execution_authorized is False


def test_tampered_or_nonready_live_preflight_is_refused_before_evaluation():
    source = source_live_preflight()
    object.__setattr__(source, "market_data_digest", "x" * 64)
    result = build_attended_safety_evaluation_result(
        source, inputs=inputs(acknowledgements=allowed_acknowledgements(), attended=True, source_hash=True),
        trusted_implementation_digest=TRUSTED,
    )
    assert result.evaluation_state is AttendedSafetyEvaluationState.SOURCE_INVALID
    assert "LIVE_PREFLIGHT_DIGEST_MISMATCH" in result.blocking_reasons


def test_result_is_deterministic_and_has_no_adapter_or_broker_write_reachability():
    kwargs = dict(
        inputs=inputs(acknowledgements=allowed_acknowledgements(), attended=True, source_hash=True),
        trusted_implementation_digest=TRUSTED,
    )
    first = build_attended_safety_evaluation_result(source_live_preflight(), **kwargs)
    second = build_attended_safety_evaluation_result(source_live_preflight(), **kwargs)
    assert first == second
    assert compute_attended_safety_evaluation_digest(first) == first.evaluation_digest
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/attended_safety_evaluation_bridge.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "RobinhoodClient", "place_equity_order", "cancel_equity_order", "call_tool", "_call"})
