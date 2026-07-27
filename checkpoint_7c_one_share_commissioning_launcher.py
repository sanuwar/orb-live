"""Attended Checkpoint 7C commissioning entry point.

This file intentionally has no MCP or Robinhood initializer. A reviewed caller
must inject exactly one already-authorized session and all private identity
values. Running this module directly fails closed without making a broker call.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from src.one_share_commissioning import (
    CommissioningError, CommissioningMachine, PLANNED_RISK_USD, QUANTITY, SYMBOL,
)
from src.robinhood_commissioning_adapter import RobinhoodCommissioningAdapter


EXPECTED_BASE_HEAD = "1b378605b31e99ab9e436e5afe69fca270c87bf6"
EXPECTED_BRANCH = "agent/m7-one-share-commissioning"
REVIEWED_HASHES: Mapping[str, str] = {
    "src/one_share_commissioning.py":
        "fe4ffb3c32aec72cfda84a8a2326217025bde4ab7a07e1932185418610539240",
    "src/robinhood_commissioning_adapter.py":
        "8f44dc4378cbf2d0d985f4eccbe3cf5526920e4a35f35e1399e8505fe9d57eb5",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_reviewed_hashes(root: Path, expected: Mapping[str, str]) -> None:
    if not expected:
        raise CommissioningError("reviewed hash manifest is not approved")
    for relative, digest in expected.items():
        if sha256(root / relative) != digest:
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


def entry_arguments(account_number: str) -> dict[str, Any]:
    return {
        "account_number": account_number,
        "symbol": SYMBOL,
        "side": "buy",
        "type": "market",
        "quantity": "1",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


def stop_arguments(account_number: str, stop_price: Decimal) -> dict[str, Any]:
    return {
        "account_number": account_number,
        "symbol": SYMBOL,
        "side": "sell",
        "type": "stop_market",
        "quantity": "1",
        "stop_price": format(stop_price, "f"),
        "time_in_force": "gtc",
        "market_hours": "regular_hours",
    }


def exit_arguments(account_number: str) -> dict[str, Any]:
    return {
        "account_number": account_number,
        "symbol": SYMBOL,
        "side": "sell",
        "type": "market",
        "quantity": "1",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


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


@dataclass
class CommissioningRuntime:
    """Dependency-injected runtime, unreachable from the normal strategy runner."""

    machine: CommissioningMachine
    adapter: RobinhoodCommissioningAdapter

    def verify_static_posture(self, *, strategy_enabled: bool) -> None:
        if strategy_enabled:
            raise CommissioningError("strategy.enabled must remain false")
        if self.machine.policy.symbol != SYMBOL or self.machine.policy.quantity != QUANTITY:
            raise CommissioningError("commissioning policy drift")
        if self.machine.policy.planned_risk_usd != PLANNED_RISK_USD:
            raise CommissioningError("planned risk policy drift")


def main() -> int:
    print("BLOCKED: reviewed runtime binding and separate live authorization required")
    print("planned_risk_usd=15; actual loss may exceed planned risk")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
