"""Attended Checkpoint 7C commissioning entry point.

This file intentionally has no MCP or Robinhood initializer. A reviewed caller
must inject exactly one already-authorized session and all private identity
values. Running this module directly fails closed without making a broker call.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping

from src.one_share_commissioning import (
    AtomicEvidenceStore, CommissioningError, EASTERN, PLANNED_RISK_USD, SYMBOL,
)
from src.canonical_source_hash import (
    CanonicalSourceHashError, canonical_source_sha256,
)
from src.one_share_commissioning_orchestrator import (
    OneShareCommissioningOrchestrator, OrchestrationResult,
    entry_arguments, exit_arguments, stop_arguments,
)
from src.robinhood_commissioning_adapter import RobinhoodCommissioningAdapter


EXPECTED_BASE_HEAD = "1b378605b31e99ab9e436e5afe69fca270c87bf6"
EXPECTED_BRANCH = "main"
REVIEWED_HASHES: Mapping[str, str] = {
    "src/one_share_commissioning.py":
        "f117b636f66a0df19cb66b92750862bc836975e7251b67b9f2f41ef87a3e6293",
    "src/robinhood_commissioning_adapter.py":
        "aa0349176141410898a08e464ba736a457e00dc450b94a7bce10fb35d34a92b8",
    "src/one_share_commissioning_orchestrator.py":
        "eb06c9126b226bf65cf42b4c36f74004eaee2d383beb3a2316a795ffb8c76f91",
}
REAL_SESSION_RUNTIME_FLAG = "ENABLE_ATTENDED_ONE_SHARE_COMMISSIONING"


def verify_reviewed_hashes(root: Path, expected: Mapping[str, str]) -> None:
    if not expected:
        raise CommissioningError("reviewed hash manifest is not approved")
    for relative, digest in expected.items():
        try:
            actual = canonical_source_sha256(root / relative)
        except CanonicalSourceHashError as exc:
            raise CommissioningError("reviewed file hash could not be verified") from exc
        if actual != digest:
            raise CommissioningError("reviewed file hash mismatch")


def verify_git_posture(root: Path) -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if branch != EXPECTED_BRANCH:
        raise CommissioningError("commissioning branch mismatch")
    base = subprocess.run(
        ["git", "merge-base", "HEAD", "main"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if base != EXPECTED_BASE_HEAD:
        raise CommissioningError("reviewed base HEAD mismatch")


def sanitized_review_summary(review: Mapping[str, Any]) -> dict[str, Any]:
    """Return only policy facts and types; never echo private or price values."""
    return {
        "symbol_match": review.get("symbol") == SYMBOL,
        "quantity_match": str(review.get("quantity")) == "1",
        "side": review.get("side") if review.get("side") in {"buy", "sell"} else "unknown",
        "order_type": review.get("type")
        if review.get("type") in {"market", "stop", "stop_market"} else "unknown",
        "planned_risk_usd": int(PLANNED_RISK_USD),
        "review_json_type": type(review).__name__,
    }


def is_regular_market_hours(now: datetime) -> bool:
    if now.tzinfo != EASTERN or now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time().replace(tzinfo=None) < time(16, 0)


async def execute_commissioning(
    *, root: Path, session: Any, commissioned_account_number: str,
    run_id: str, evidence_root: Path, monotonic: Any, sleep: Any,
    now_eastern: Any, authorization_provider: Any, emergency_handoff: Any,
    strategy_enabled: bool, mode: str = "blocked", runtime_flag: str = "",
    real_session_authorized: bool = False,
) -> OrchestrationResult:
    """Create exactly one adapter and orchestrator around an injected session."""
    if mode not in {"fake", "real"}:
        raise CommissioningError("launcher is fail closed by default")
    if mode == "fake" and getattr(session, "offline_fixture", False) is not True:
        raise CommissioningError("fake mode requires an explicitly marked offline fixture")
    if mode == "real" and (
        runtime_flag != REAL_SESSION_RUNTIME_FLAG or not real_session_authorized
    ):
        raise CommissioningError("real-session mode lacks explicit runtime authorization")
    if mode == "real" and getattr(session, "offline_fixture", False) is True:
        raise CommissioningError("offline fixture cannot enter real-session mode")
    verify_git_posture(root)
    verify_reviewed_hashes(root, REVIEWED_HASHES)
    if strategy_enabled:
        raise CommissioningError("strategy.enabled must remain false")
    current = now_eastern()
    if current.tzinfo != EASTERN or not is_regular_market_hours(current):
        raise CommissioningError("current Eastern time is outside regular market hours")
    store = AtomicEvidenceStore(evidence_root, run_id)
    adapter = RobinhoodCommissioningAdapter(session, commissioned_account_number)
    orchestrator = OneShareCommissioningOrchestrator(
        run_id=run_id, adapter=adapter, monotonic=monotonic, sleep=sleep,
        now_eastern=now_eastern, authorization_provider=authorization_provider,
        evidence_store=store, emergency_handoff=emergency_handoff,
        strategy_enabled=strategy_enabled, regular_market_hours=True,
    )
    result = await orchestrator.run()
    print(f"commissioning_state={result.state.value}")
    print("planned_risk_usd=15; actual loss may exceed planned risk")
    return result


def run_fake_demo(**kwargs: Any) -> OrchestrationResult:
    """Run only with an explicitly injected offline fake session."""
    kwargs["mode"] = "fake"
    return asyncio.run(execute_commissioning(**kwargs))


def main() -> int:
    print("BLOCKED: reviewed runtime binding and separate live authorization required")
    print("planned_risk_usd=15; actual loss may exceed planned risk")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
