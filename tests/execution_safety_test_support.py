"""Shared test-only helper for M14 Issue 1: build a minimal, already-ALLOW
`ExecutionSafetyDecision` bound to exact write arguments.

Adapter-level tests exercise `RobinhoodCommissioningAdapter` directly, below
the orchestrator that would normally compute this decision from a full
`ExecutionSafetyContext`. `decision_binds_write` only inspects the `bound_*`
fields and `outcome`, so constructing the decision directly here (rather
than driving the full context/gate machinery) is sufficient and keeps these
adapter-focused tests decoupled from governance-policy specifics they are
not testing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.execution_safety_governance import (
    ExecutionSafetyDecision,
    ExecutionSafetyOutcome,
)


def allow_decision(*, role: str, arguments: Mapping[str, Any]) -> ExecutionSafetyDecision:
    return ExecutionSafetyDecision(
        decision_id="test-allow-decision",
        policy_version="test",
        action_identity=f"test-action:{role}",
        outcome=ExecutionSafetyOutcome.ALLOW,
        satisfied_gates=(),
        failed_gates=(),
        reason_codes=(),
        decision_digest="test-allow-digest",
        evaluated_at=datetime.now(timezone.utc),
        authority_ceiling="TEST_ONLY",
        bound_role=role,
        bound_symbol=arguments.get("symbol", ""),
        bound_side=arguments.get("side", ""),
        bound_order_type=arguments.get("type", ""),
        bound_quantity=arguments.get("quantity", ""),
        bound_stop_price=arguments.get("stop_price"),
        bound_order_id=arguments.get("order_id"),
    )


def deny_decision(*, role: str, arguments: Mapping[str, Any]) -> ExecutionSafetyDecision:
    return ExecutionSafetyDecision(
        decision_id="test-deny-decision",
        policy_version="test",
        action_identity=f"test-action:{role}",
        outcome=ExecutionSafetyOutcome.DENY,
        satisfied_gates=(),
        failed_gates=("test_gate",),
        reason_codes=("DENY_TEST_ONLY",),
        decision_digest="test-deny-digest",
        evaluated_at=datetime.now(timezone.utc),
        authority_ceiling="TEST_ONLY",
        bound_role=role,
        bound_symbol=arguments.get("symbol", ""),
        bound_side=arguments.get("side", ""),
        bound_order_type=arguments.get("type", ""),
        bound_quantity=arguments.get("quantity", ""),
        bound_stop_price=arguments.get("stop_price"),
        bound_order_id=arguments.get("order_id"),
    )
