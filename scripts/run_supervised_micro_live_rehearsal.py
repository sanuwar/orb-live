"""Run a saved attended-commissioning handoff through the final non-writing gate."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.attended_commissioning_handoff import (
    AttendedCommissioningHandoff,
    AttendedCommissioningHandoffState,
)
from src.supervised_micro_live_rehearsal import (
    SupervisedMicroLiveRehearsal,
    build_supervised_micro_live_rehearsal,
)


def _handoff_from_json(raw: dict[str, Any]) -> AttendedCommissioningHandoff:
    allowed = {field.name for field in fields(AttendedCommissioningHandoff)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unsupported handoff evidence fields: {sorted(unknown)}")
    payload = dict(raw)
    payload["required_operator_acknowledgements"] = tuple(payload["required_operator_acknowledgements"])
    payload["intended_protection_sequence"] = tuple(payload["intended_protection_sequence"])
    payload["blocking_reasons"] = tuple(payload["blocking_reasons"])
    payload["handoff_state"] = AttendedCommissioningHandoffState(payload["handoff_state"])
    return AttendedCommissioningHandoff(**payload)


def _evidence(result: SupervisedMicroLiveRehearsal) -> dict[str, Any]:
    return {
        "rehearsal_id": result.rehearsal_id,
        "rehearsal_digest": result.rehearsal_digest,
        "source_handoff_id": result.source_handoff_id,
        "source_shadow_decision_id": result.source_shadow_decision_id,
        "source_review_id": result.source_review_id,
        "source_dry_run_id": result.source_dry_run_id,
        "source_live_preflight_id": result.source_live_preflight_id,
        "source_safety_evaluation_id": result.source_safety_evaluation_id,
        "symbol": result.symbol,
        "trading_date": result.trading_date,
        "quantity": result.quantity,
        "side": result.side,
        "order_type": result.order_type,
        "intended_entry": result.intended_entry,
        "intended_stop": result.intended_stop,
        "intended_target": result.intended_target,
        "safety_decision_result": result.safety_decision_result,
        "safety_decision_freshness": result.safety_decision_freshness,
        "action_identity": result.action_identity,
        "protection_sequence": list(result.protection_sequence),
        "broker_mutation_boundary": result.broker_mutation_boundary,
        "evidence_source": result.evidence_source,
        "blocking_reasons": list(result.blocking_reasons),
        "rehearsal_state": result.rehearsal_state.value,
        "protection_grounded": False,
        "execution_authorized": False,
        "broker_write_count": 0,
        "authority_ceiling": result.authority_ceiling,
    }


def _print_summary(result: SupervisedMicroLiveRehearsal, output: Path) -> None:
    print("SUPERVISED MICRO-LIVE REHEARSAL")
    print(f"Evidence source: {result.evidence_source}")
    print(f"Symbol: {result.symbol}")
    print(f"Side: {result.side.upper()}")
    print(f"Quantity: {result.quantity}")
    print(f"Order type: {result.order_type.upper()}")
    print(f"Entry intent: {result.intended_entry}")
    print(f"Protective stop intent: {result.intended_stop}")
    print(f"Target intent: {result.intended_target or 'NONE'}")
    print(f"Safety evaluation: {result.safety_decision_result} ({result.safety_decision_freshness})")
    print(f"Handoff: {result.rehearsal_state.value}")
    print(f"Broker mutation boundary: {result.broker_mutation_boundary}")
    print("BROKER WRITE EXECUTED: NO")
    print(f"Final status: {result.rehearsal_state.value}")
    if result.blocking_reasons:
        print("Blocking reasons: " + ", ".join(result.blocking_reasons))
    print(f"Evidence: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path, help="saved sanitized AttendedCommissioningHandoff JSON")
    parser.add_argument("--output", type=Path, help="sanitized rehearsal artifact destination")
    parser.add_argument("--fixture", action="store_true", help="label input explicitly as deterministic fixture rehearsal evidence")
    args = parser.parse_args()
    raw = json.loads(args.handoff.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("handoff evidence must be a JSON object")
    result = build_supervised_micro_live_rehearsal(
        _handoff_from_json(raw),
        evidence_source="FIXTURE_REHEARSAL" if args.fixture else "SAVED_ARTIFACT",
    )
    output = args.output or Path("artifacts") / "commissioning-rehearsal" / f"{result.rehearsal_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_evidence(result), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _print_summary(result, output)
    return 0 if not result.blocking_reasons else 2


if __name__ == "__main__":
    raise SystemExit(main())
