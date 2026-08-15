from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.market_data_canonical import MarketDataQuality
from src.shadow_candidate_commissioning_review import build_shadow_candidate_commissioning_review
from src.shadow_trading_decision import ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest
from src.supervised_micro_live_dry_run import build_supervised_micro_live_dry_run_plan
from src.supervised_micro_live_preflight import (
    PreflightInputs, PreflightState, build_supervised_micro_live_preflight,
    compute_supervised_micro_live_preflight_digest,
)

DAY = date(2026, 8, 11)
SIGNAL = datetime(2026, 8, 11, 14, 45, tzinfo=timezone.utc)
REVIEWED = datetime(2026, 8, 11, 14, 50, tzinfo=timezone.utc)
TRUSTED = "448f1e2739eb01d8606536723c7752c0e06a86e770c655489262d94553d9e114"


def candidate(kind=ShadowDecisionType.LONG_CANDIDATE):
    entry = Decimal("100") if kind is ShadowDecisionType.LONG_CANDIDATE else None
    stop = Decimal("99") if entry else None
    target = Decimal("102") if entry else None
    digest = compute_shadow_decision_digest(shadow_run_id="m17", symbol="AAPL", trading_date=DAY, strategy_version="orb_long_v0-0.1.0", implementation_digest=TRUSTED, market_data_digest="b" * 64, opening_range_identity="d" * 64, signal_timestamp=SIGNAL, decision_type=kind, proposed_entry=entry, proposed_stop=stop, proposed_target=target, reason="fixture", market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR")
    return ShadowTradingDecision("m17", digest, "AAPL", DAY, "orb_long_v0-0.1.0", TRUSTED, "b" * 64, "d" * 64, SIGNAL, kind, entry, stop, target, "fixture", MarketDataQuality.VALID, "REGULAR", SIGNAL)


def artifacts(item=None):
    review = build_shadow_candidate_commissioning_review(item or candidate(), reviewed_at=REVIEWED, trusted_implementation_digest=TRUSTED)
    return review, build_supervised_micro_live_dry_run_plan(review, trusted_implementation_digest=TRUSTED)


def inputs(*, ready=False):
    return PreflightInputs(
        evaluated_at=REVIEWED,
        acknowledgements=(ready, ready, ready, ready),
        source_hash_verified=ready, release_sha="a" * 40 if ready else "",
        account_fingerprint_matched=ready, account_fingerprint_reference="b" * 64 if ready else "",
        suspension_state="CLEAR" if ready else "NOT_EVALUATED",
    )


def test_full_authentic_flow_is_blocked_only_by_unresolved_attended_facts():
    review, dry_run = artifacts()
    result = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs())
    assert result.preflight_state is PreflightState.PREFLIGHT_BLOCKED
    assert {"OPERATOR_ACK_REQUIRED", "ACCOUNT_VERIFICATION_REQUIRED", "SUSPENSION_CLEARANCE_REQUIRED"} <= set(result.blocking_reasons)
    assert result.execution_authorized is False and result.broker_write_count == 0


def test_offline_attested_preflight_ready_still_cannot_authorize_execution():
    review, dry_run = artifacts()
    result = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True))
    assert result.preflight_state is PreflightState.PREFLIGHT_READY
    assert result.safety_context is not None
    assert result.safety_context.action == "ENTRY_PLACE"
    assert result.execution_authorized is False and result.broker_write_count == 0


@pytest.mark.parametrize("kind", [ShadowDecisionType.REJECTED, ShadowDecisionType.NO_SIGNAL])
def test_rejected_candidate_cannot_reach_preflight(kind):
    review, dry_run = artifacts(candidate(kind))
    result = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True))
    assert result.preflight_state is PreflightState.PREFLIGHT_BLOCKED


def test_tampered_review_or_dry_run_is_blocked():
    review, dry_run = artifacts()
    object.__setattr__(review, "market_data_digest", "x" * 64)
    assert "REVIEW_DIGEST_MISMATCH" in build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True)).blocking_reasons
    review, dry_run = artifacts()
    object.__setattr__(dry_run, "market_data_digest", "x" * 64)
    assert "DRY_RUN_DIGEST_MISMATCH" in build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True)).blocking_reasons


def test_account_suspension_and_acknowledgements_never_synthesize_readiness():
    review, dry_run = artifacts()
    result = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs())
    assert result.acknowledgement_status == "REQUIRED"
    assert result.account_fingerprint_status == "REQUIRED"
    assert result.suspension_clearance_status == "REQUIRED"


def test_deterministic_digest_and_one_share_bound():
    review, dry_run = artifacts()
    first = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True))
    second = build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True))
    assert first == second and compute_supervised_micro_live_preflight_digest(first) == first.preflight_digest
    object.__setattr__(dry_run, "quantity", Decimal("2"))
    with pytest.raises(ValueError):
        build_supervised_micro_live_preflight(review, dry_run, inputs=inputs(ready=True))


def test_preflight_has_no_broker_mutation_reachability():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/supervised_micro_live_preflight.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "RobinhoodCommissioningSessionBinding", "place_equity_order", "cancel_equity_order", "call_tool", "RobinhoodClient"})