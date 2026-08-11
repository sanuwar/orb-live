"""Explicit production read-only validation for a saved M18 handoff.

This command never imports an adapter or dispatches a broker write.  It first
rejects non-candidate live-shadow evidence, so a normal no-candidate session
does not trigger any account observation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.account_state import AccountStateReader
from src.broker_identity import CommissionedAccount, load_local_override
from src.commissioning_suspension_identity import (
    CommissioningSuspensionIdentity, CommissioningSuspensionStateProvider,
)
from src.config import load_config
from src.live_attended_preflight_inputs import observe_suspension
from src.market_data_canonical import MarketDataQuality
from scripts.run_supervised_micro_live_rehearsal import _evidence, _handoff_from_json
from src.supervised_micro_live_rehearsal import build_supervised_micro_live_rehearsal

_ACKS = ("acknowledges_loss_may_exceed_plan", "operator_present", "robinhood_ui_open", "no_second_trade")


def _candidate(raw: dict) -> dict | None:
    if raw.get("run_mode") != "LIVE_SHADOW" or raw.get("provider") != "alpaca":
        raise ValueError("production mode requires authentic LIVE_SHADOW Alpaca evidence")
    decision = raw.get("shadow_trading_decision")
    if not isinstance(decision, dict) or decision.get("decision_type") != "LONG_CANDIDATE":
        return None
    required = ("decision_id", "market_data_digest", "opening_range_identity", "implementation_digest", "strategy_version", "trading_date")
    if any(not isinstance(decision.get(key), str) or not decision[key] for key in required):
        raise ValueError("candidate evidence is incomplete")
    return decision


async def _observe(*, release_sha: str, suspension_db: Path):
    commissioned = CommissionedAccount.from_config(load_config(), local_config=load_local_override())
    snapshot = await AccountStateReader().read_snapshot()
    commissioned.verify(snapshot.metadata.account_fingerprint)
    now = datetime.now(timezone.utc)
    identity = CommissioningSuspensionIdentity.derive(
        release_sha=release_sha, account_fingerprint_reference=commissioned.expected_fingerprint,
    )
    with CommissioningSuspensionStateProvider(database_path=suspension_db, identity=identity) as provider:
        suspension = observe_suspension(provider, observed_at=now)
    return snapshot, commissioned.expected_fingerprint, suspension, now


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-read-only", action="store_true", required=True)
    parser.add_argument("--live-shadow", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--suspension-db", type=Path, required=True)
    parser.add_argument("--ack", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if tuple(sorted(set(args.ack))) != tuple(sorted(_ACKS)) or len(args.ack) != len(_ACKS):
        raise ValueError("all four exact operator acknowledgements must be explicitly supplied")
    live = json.loads(args.live_shadow.read_text(encoding="utf-8"))
    candidate = _candidate(live)
    if candidate is None:
        print(json.dumps({"final_rehearsal_state": "NO_VALID_CANDIDATE", "broker_write_count": 0, "execution_authorized": False, "protection_grounded": False}, sort_keys=True))
        return 2
    handoff = _handoff_from_json(json.loads(args.handoff.read_text(encoding="utf-8")))
    if handoff.source_shadow_decision_id != candidate["decision_id"] or handoff.release_sha != args.release_sha:
        raise ValueError("candidate/handoff source mismatch")
    snapshot, expected, suspension, observed_at = asyncio.run(_observe(release_sha=args.release_sha, suspension_db=args.suspension_db))
    if handoff.account_fingerprint_reference != expected or suspension.decision.state.value != "CLEAR":
        raise ValueError("read-only account or suspension prerequisite blocked")
    result = build_supervised_micro_live_rehearsal(handoff, evidence_source="PRODUCTION_READ_ONLY")
    evidence = _evidence(result)
    evidence.update({"source_candidate_id": candidate["decision_id"], "safe_account_fingerprint_reference": expected, "suspension_controller_id": suspension.controller_id, "suspension_state": suspension.decision.state.value, "acknowledgements": sorted(args.ack), "observed_at": observed_at.isoformat()})
    output = args.output or Path("artifacts") / "commissioning-rehearsal" / f"production-{result.rehearsal_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"final_rehearsal_state": result.rehearsal_state.value, "broker_write_count": 0, "execution_authorized": False, "protection_grounded": False, "evidence": str(output)}, sort_keys=True))
    return 0 if not result.blocking_reasons else 2


if __name__ == "__main__":
    raise SystemExit(main())
