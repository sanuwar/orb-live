"""Verify one completed offline campaign for evidence-review packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from scripts.run_offline_commissioning_campaign import (  # noqa: E402
    CAMPAIGN_INDEX,
    SCENARIOS,
    verify_campaign,
)
from src.bounded_shadow_session import SESSION_NOW  # noqa: E402
from src.config import load_config  # noqa: E402
from src import execution_gates  # noqa: E402


ACCEPTANCE_INDEX = "offline-campaign-acceptance.json"
SCHEMA_VERSION = 1
ACCEPTANCE_RESULT = "ACCEPTED_FOR_OFFLINE_EVIDENCE_REVIEW"
_ACCEPTANCE_KEYS = frozenset({
    "schema_version", "acceptance_id", "campaign_id", "injected_verified_at",
    "campaign_index_hash", "scenario_count", "scenario_set_status",
    "identity_uniqueness_status", "hash_verification_status",
    "privacy_verification_status", "adverse_outcome_preservation_status",
    "safety_invariant_status", "operator_checklist_status", "acceptance_result",
    "blocker_reasons", "permits_automated_action", "live_ready",
    "unattended_ready", "protection_grounded", "activation_approved",
})
_FORBIDDEN_TEXT = (
    "account", "balance", "broker", "credential", "exception", "manifest",
    "order", "password", "price", "quantity", "secret", "symbol", "token",
    "username",
)
_SAFETY_FIELDS = {
    "permits_automated_action", "live_ready", "unattended_ready",
    "protection_grounded", "activation_approved", "strategy_enabled",
}
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)")


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _acceptance_id(value: object) -> str:
    if (
        not isinstance(value, str) or not value.startswith("acceptance-offline-")
        or value != value.strip() or not value.replace("-", "").isalnum()
        or not 24 <= len(value) <= 96
        or any(term in value.casefold() for term in _FORBIDDEN_TEXT)
    ):
        raise ValueError("acceptance identity is invalid")
    return value


def _verified_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("verification timestamp is invalid")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("verification timestamp is invalid") from None
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("verification timestamp is invalid")
    moment = moment.astimezone(timezone.utc)
    if moment.date() != SESSION_NOW.date() or moment > SESSION_NOW:
        raise ValueError("verification timestamp is invalid")
    return moment


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object is required")
    return value


def _walk_values(value: object):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)


def _verify_privacy_and_safety(campaign_root: Path) -> None:
    expected_names = {item.casefold().replace("_", "-") for item in SCENARIOS}
    expected_names.add(CAMPAIGN_INDEX.casefold())
    if {item.name.casefold() for item in campaign_root.iterdir()} != expected_names:
        raise ValueError("campaign contains partial or extra output")
    for path in campaign_root.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if _ABSOLUTE_PATH.search(text) or "\\" in text:
            raise ValueError("absolute path is serialized")
        value = _read_object(path)
        for key, nested in _walk_values(value):
            key_text = str(key).casefold()
            if key_text != "scenario_order" and any(term in key_text for term in _FORBIDDEN_TEXT):
                raise ValueError("private field is serialized")
            if isinstance(nested, str) and any(term in nested.casefold() for term in _FORBIDDEN_TEXT):
                raise ValueError("private text is serialized")
            if key in _SAFETY_FIELDS and nested is not False:
                raise ValueError("safety invariant is not false")
            if key == "approved_active_protection_state_count" and nested != 0:
                raise ValueError("protection state is approved")


def _verify_runtime_safety() -> None:
    config = load_config()
    strategy = config.get("strategy")
    if not isinstance(strategy, dict) or strategy.get("enabled") is not False:
        raise ValueError("strategy is enabled")
    if execution_gates.ACTIVATION_APPROVALS:
        raise ValueError("activation approvals are present")


def _acceptance_payload(*, acceptance_id: str, campaign_id: str,
                        verified_at: datetime, campaign_index_hash: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": acceptance_id,
        "campaign_id": campaign_id,
        "injected_verified_at": verified_at.isoformat(),
        "campaign_index_hash": campaign_index_hash,
        "scenario_count": len(SCENARIOS),
        "scenario_set_status": "VERIFIED",
        "identity_uniqueness_status": "VERIFIED",
        "hash_verification_status": "VERIFIED",
        "privacy_verification_status": "VERIFIED",
        "adverse_outcome_preservation_status": "VERIFIED",
        "safety_invariant_status": "VERIFIED_CLOSED",
        "operator_checklist_status": "PROVIDED_NOT_AUTHORIZATION",
        "acceptance_result": ACCEPTANCE_RESULT,
        "blocker_reasons": [],
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
        "protection_grounded": False,
        "activation_approved": False,
    }


def verify_acceptance(*, campaign_output: Path, acceptance_id: str,
                      verified_at: str, output: Path) -> int:
    """Verify a campaign and atomically publish one evidence-only result."""
    acceptance_id = _acceptance_id(acceptance_id)
    moment = _verified_at(verified_at)
    campaign_output = campaign_output.expanduser().resolve()
    output = output.expanduser().resolve()
    temporary = Path(__import__("tempfile").gettempdir()).resolve()
    if temporary not in campaign_output.parents or ROOT in campaign_output.parents or campaign_output == ROOT:
        raise ValueError("campaign path is outside temporary storage")
    if temporary not in output.parent.parents and output.parent != temporary:
        raise ValueError("acceptance path is outside temporary storage")
    if ROOT in output.parents or output == ROOT:
        raise ValueError("acceptance path is inside repository")
    if output.exists() or output.with_name("." + output.name + ".partial").exists():
        raise FileExistsError("acceptance output already exists")
    campaign_index = campaign_output / CAMPAIGN_INDEX
    index = verify_campaign(campaign_output)
    if set(index) != {"campaign_id", "schema_version", "scenario_order", "scenarios", "campaign_result", "permits_automated_action", "live_ready", "unattended_ready"}:
        raise ValueError("campaign index is not exact")
    _verify_privacy_and_safety(campaign_output)
    _verify_runtime_safety()
    payload = _acceptance_payload(
        acceptance_id=acceptance_id, campaign_id=index["campaign_id"],
        verified_at=moment,
        campaign_index_hash=hashlib.sha256(campaign_index.read_bytes()).hexdigest(),
    )
    if set(payload) != _ACCEPTANCE_KEYS:
        raise ValueError("acceptance schema is not exact")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name("." + output.name + ".partial")
    with partial.open("xb") as handle:
        handle.write(_encoded(payload)); handle.flush(); os.fsync(handle.fileno())
    partial.replace(output)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-output", type=Path, required=True)
    parser.add_argument("--acceptance-id", required=True)
    parser.add_argument("--verified-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        return verify_acceptance(
            campaign_output=args.campaign_output, acceptance_id=args.acceptance_id,
            verified_at=args.verified_at, output=args.output,
        )
    except (SystemExit, ValueError, FileExistsError, OSError, json.JSONDecodeError):
        return 50
    except Exception:
        return 30


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ACCEPTANCE_INDEX", "main", "verify_acceptance"]
