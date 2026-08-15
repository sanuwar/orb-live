from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.market_data_canonical import MarketDataQuality
from src.shadow_candidate_commissioning_review import (
    ReviewReadinessState,
    build_shadow_candidate_commissioning_review,
    compute_shadow_candidate_commissioning_review_digest,
)
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)
from src.supervised_micro_live_dry_run import (
    AUTHORITY_CEILING,
    DryRunState,
    ONE_SHARE,
    build_supervised_micro_live_dry_run_plan,
    compute_supervised_micro_live_dry_run_digest,
    write_supervised_micro_live_dry_run_artifact,
)

DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 45, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 8, 11, 14, 50, tzinfo=timezone.utc)
TRUSTED = "448f1e2739eb01d8606536723c7752c0e06a86e770c655489262d94553d9e114"


def decision(*, symbol="AAPL", kind=ShadowDecisionType.LONG_CANDIDATE, entry=Decimal("100"), stop=Decimal("99"), target=Decimal("102")):
    entry_value = entry if kind is ShadowDecisionType.LONG_CANDIDATE else None
    stop_value = stop if kind is ShadowDecisionType.LONG_CANDIDATE else None
    target_value = target if kind is ShadowDecisionType.LONG_CANDIDATE else None
    digest = compute_shadow_decision_digest(
        shadow_run_id="dry-run-fixture", symbol=symbol, trading_date=DAY,
        strategy_version="orb_long_v0-0.1.0", implementation_digest=TRUSTED,
        market_data_digest="b" * 64, opening_range_identity="d" * 64,
        signal_timestamp=SIGNAL, decision_type=kind, proposed_entry=entry_value,
        proposed_stop=stop_value, proposed_target=target_value, reason="fixture",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        shadow_run_id="dry-run-fixture", decision_id=digest, symbol=symbol,
        trading_date=DAY, strategy_version="orb_long_v0-0.1.0",
        implementation_digest=TRUSTED, market_data_digest="b" * 64,
        opening_range_identity="d" * 64, signal_timestamp=SIGNAL,
        decision_type=kind, proposed_entry=entry_value, proposed_stop=stop_value,
        proposed_target=target_value, reason="fixture", market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR", created_at=SIGNAL,
    )


def review(item=None):
    return build_shadow_candidate_commissioning_review(
        item or decision(), reviewed_at=REVIEWED, trusted_implementation_digest=TRUSTED,
    )


def test_review_ready_builds_read_only_one_share_dry_run_plan():
    plan = build_supervised_micro_live_dry_run_plan(review())
    assert plan.dry_run_state is DryRunState.DRY_RUN_READY
    assert plan.quantity == ONE_SHARE
    assert plan.entry_intent.side == "buy"
    assert plan.entry_intent.order_type == "market"
    assert plan.protective_stop_intent.side == "sell"
    assert plan.protective_stop_intent.order_type == "stop_market"
    assert plan.target_price == Decimal("102")
    assert plan.execution_authorized is False
    assert plan.broker_write_count == 0
    assert plan.authority_ceiling == AUTHORITY_CEILING


def test_required_acknowledgements_are_exact_and_never_auto_satisfied():
    plan = build_supervised_micro_live_dry_run_plan(review())
    assert [item.name for item in plan.required_operator_acknowledgements] == [
        "acknowledges_loss_may_exceed_plan", "operator_present",
        "robinhood_ui_open", "no_second_trade",
    ]
    assert not any(item.acknowledged for item in plan.required_operator_acknowledgements)


def test_account_and_suspension_requirements_remain_unresolved_without_lookup():
    plan = build_supervised_micro_live_dry_run_plan(review())
    assert plan.account_fingerprint_status == "REQUIRED_NOT_SUPPLIED"
    assert plan.suspension_clearance_status == "REQUIRED_NOT_EVALUATED"
    assert plan.source_hash_verification_status == "REQUIRED_NOT_VERIFIED"
    assert plan.required_safety_context.values_resolved is False
    assert "ACCOUNT_FINGERPRINT_MATCH" in plan.missing_prerequisites
    assert "FRESH_SUSPENSION_CLEARANCE" in plan.missing_prerequisites


def test_safe_offline_account_fingerprint_is_recorded_but_not_matched():
    plan = build_supervised_micro_live_dry_run_plan(review(), account_fingerprint_reference="a" * 64)
    assert plan.account_fingerprint_reference == "a" * 64
    assert plan.account_fingerprint_status == "SUPPLIED_UNVERIFIED"
    assert "ACCOUNT_FINGERPRINT_MATCH" in plan.missing_prerequisites


@pytest.mark.parametrize("kind", [ShadowDecisionType.REJECTED, ShadowDecisionType.NO_SIGNAL])
def test_non_review_ready_sources_are_blocked(kind):
    plan = build_supervised_micro_live_dry_run_plan(review(decision(kind=kind)))
    assert plan.dry_run_state is DryRunState.REVIEW_NOT_READY
    assert "REVIEW_NOT_READY" in plan.blocking_reasons


def test_tampered_review_digest_is_blocked():
    item = review()
    object.__setattr__(item, "market_data_digest", "x" * 64)
    plan = build_supervised_micro_live_dry_run_plan(item)
    assert plan.dry_run_state is DryRunState.REVIEW_NOT_READY
    assert "REVIEW_DIGEST_MISMATCH" in plan.blocking_reasons


def test_review_digest_public_reconstruction_detects_tampering():
    item = review()
    assert compute_shadow_candidate_commissioning_review_digest(item) == item.review_digest
    object.__setattr__(item, "source_shadow_decision_id", "z" * 64)
    assert compute_shadow_candidate_commissioning_review_digest(item) != item.review_digest


def test_source_symbol_outside_fixed_commissioning_policy_is_fail_closed():
    plan = build_supervised_micro_live_dry_run_plan(review(decision(symbol="MSFT")))
    assert plan.dry_run_state is DryRunState.SOURCE_MISMATCH
    assert "COMMISSIONING_SYMBOL_POLICY_MISMATCH" in plan.blocking_reasons


def test_invalid_protection_is_blocked_before_any_future_action_plan():
    item = review()
    object.__setattr__(item, "proposed_stop", None)
    object.__setattr__(item, "protection_plan_complete", False)
    digest = compute_shadow_candidate_commissioning_review_digest(item)
    object.__setattr__(item, "review_id", digest)
    object.__setattr__(item, "review_digest", digest)
    plan = build_supervised_micro_live_dry_run_plan(item)
    assert plan.dry_run_state is DryRunState.INVALID_PROTECTION_PLAN
    assert "PROTECTION_PLAN_INVALID" in plan.blocking_reasons


def test_quantity_above_one_is_impossible():
    with pytest.raises(ValueError, match="exactly one share"):
        build_supervised_micro_live_dry_run_plan(review(), quantity=Decimal("2"))


def test_deterministic_digest_and_sanitized_artifact(tmp_path):
    first = build_supervised_micro_live_dry_run_plan(review())
    second = build_supervised_micro_live_dry_run_plan(review())
    assert first == second
    assert compute_supervised_micro_live_dry_run_digest(first) == first.dry_run_digest
    path = write_supervised_micro_live_dry_run_artifact(first, tmp_path / "dry-run.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["dry_run_id"] == first.dry_run_id
    assert payload["execution_authorized"] is False
    assert payload["broker_write_count"] == 0
    assert "account_number" not in path.read_text(encoding="utf-8")


def test_review_source_mismatch_and_execution_approval_cannot_be_trusted():
    item = review()
    object.__setattr__(item, "execution_approval_granted", True)
    plan = build_supervised_micro_live_dry_run_plan(item)
    assert plan.dry_run_state is DryRunState.REVIEW_NOT_READY
    assert "REVIEW_EXECUTION_APPROVAL_INVALID" in plan.blocking_reasons


def test_dry_run_module_has_no_broker_or_write_reachability():
    source = Path(__file__).resolve().parents[1] / "src/supervised_micro_live_dry_run.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    forbidden = {
        "RobinhoodClient", "RobinhoodCommissioningAdapter", "RobinhoodCommissioningSessionBinding",
        "OneShareCommissioningOrchestrator", "ExecutionSafetyDecision", "ExecutionSafetyContext",
        "call_tool", "place_equity_order", "cancel_equity_order", "flatten_position",
    }
    assert not (names & forbidden)