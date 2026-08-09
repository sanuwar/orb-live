"""Shared test-only helper for M14 Issue 1/6: build a minimal, already-ALLOW
(or already-DENY) `ExecutionSafetyDecision` bound to exact write arguments.

Adapter-level tests exercise `RobinhoodCommissioningAdapter` directly, below
the orchestrator that would normally compute this decision from a full
`ExecutionSafetyContext`. Constructing the decision directly here (rather
than driving the full context/gate machinery) keeps these adapter-focused
tests decoupled from governance-policy specifics they are not testing.

M14 Issue 6: the write boundary now independently re-validates a presented
decision's policy_version/authority_ceiling/decision_digest (via
`validate_execution_safety_decision`), not only its outcome and bound_*
fields. So `allow_decision` builds an AUTHENTIC decision by default -- real
`POLICY_VERSION`/`AUTHORITY_CEILING` and a correctly-computed
`decision_digest`/`decision_id`, using the module's own
`compute_decision_digest` rather than a second hashing scheme -- so these
tests keep exercising real adapter-boundary enforcement instead of
accidentally exercising the exact forged-decision pattern M14 Issue 4 found.
`evaluated_at` defaults to "now" so freshness holds against the adapter's
own real-clock default without depending on wall-clock alignment beyond
that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.execution_safety_governance import (
    AUTHORITY_CEILING,
    POLICY_VERSION,
    ExecutionSafetyDecision,
    ExecutionSafetyOutcome,
    compute_decision_digest,
)


def _decision(
    *, role: str, arguments: Mapping[str, Any], outcome: ExecutionSafetyOutcome,
    satisfied_gates: tuple[str, ...], failed_gates: tuple[str, ...],
    reason_codes: tuple[str, ...],
) -> ExecutionSafetyDecision:
    action_identity = f"test-action:{role}"
    bound = dict(
        bound_role=role,
        bound_symbol=arguments.get("symbol", ""),
        bound_side=arguments.get("side", ""),
        bound_order_type=arguments.get("type", ""),
        bound_quantity=arguments.get("quantity", ""),
        bound_stop_price=arguments.get("stop_price"),
        bound_order_id=arguments.get("order_id"),
    )
    digest = compute_decision_digest(
        policy_version=POLICY_VERSION, action_identity=action_identity,
        outcome=outcome, satisfied=satisfied_gates, failed=failed_gates,
        reasons=reason_codes, authority_ceiling=AUTHORITY_CEILING, **bound,
    )
    return ExecutionSafetyDecision(
        decision_id=digest,
        policy_version=POLICY_VERSION,
        action_identity=action_identity,
        outcome=outcome,
        satisfied_gates=satisfied_gates,
        failed_gates=failed_gates,
        reason_codes=reason_codes,
        decision_digest=digest,
        evaluated_at=datetime.now(timezone.utc),
        authority_ceiling=AUTHORITY_CEILING,
        **bound,
    )


def allow_decision(*, role: str, arguments: Mapping[str, Any]) -> ExecutionSafetyDecision:
    return _decision(
        role=role, arguments=arguments, outcome=ExecutionSafetyOutcome.ALLOW,
        satisfied_gates=(), failed_gates=(), reason_codes=(),
    )


def deny_decision(*, role: str, arguments: Mapping[str, Any]) -> ExecutionSafetyDecision:
    return _decision(
        role=role, arguments=arguments, outcome=ExecutionSafetyOutcome.DENY,
        satisfied_gates=(), failed_gates=("test_gate",), reason_codes=("DENY_TEST_ONLY",),
    )
