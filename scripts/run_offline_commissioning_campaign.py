"""Run and verify the fixed, finite offline commissioning campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from scripts.run_offline_commissioning import (  # noqa: E402
    EXIT_AMBIGUOUS,
    EXIT_FAILED_CLOSED,
    EXIT_INVALID,
    EXIT_OK,
    EXIT_REVIEW,
    EXIT_SUSPENDED,
    FILES,
    INDEX,
    run_commissioning,
)


SCHEMA_VERSION = 1
CAMPAIGN_INDEX = "commissioning-campaign-index.json"
SCENARIOS = (
    "SAFE_OFFLINE_COMPLETION", "OPERATOR_REVIEW_REQUIRED", "ACTIVE_SUSPENSION",
    "AMBIGUOUS_RESTART", "MISSED_HEARTBEAT", "ALERT_DELIVERY_UNCONFIRMED",
    "READINESS_BLOCKED", "STALE_EVIDENCE",
)
_EXIT_CLASSIFICATIONS = {
    EXIT_OK: "COMPLETED",
    EXIT_REVIEW: "REVIEW_REQUIRED",
    EXIT_SUSPENDED: "SUSPENDED",
    EXIT_AMBIGUOUS: "AMBIGUOUS_RESTART",
    EXIT_FAILED_CLOSED: "FAILED_CLOSED",
}
_CAMPAIGN_KEYS = frozenset({
    "campaign_id", "schema_version", "scenario_order", "scenarios",
    "campaign_result", "permits_automated_action", "live_ready",
    "unattended_ready",
})
_SCENARIO_KEYS = frozenset({
    "scenario_id", "relative_bundle_directory", "commissioning_run_id",
    "occurrence_id", "expected_exit_classification",
    "observed_exit_classification", "bundle_index_hash", "verification_status",
})
_FORBIDDEN = (
    "account", "balance", "broker", "credential", "exception", "manifest",
    "order", "password", "price", "quantity", "secret", "symbol", "token",
    "username",
)
_START = datetime(2026, 8, 3, 13, 59, tzinfo=timezone.utc)


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _campaign_id(value: str) -> str:
    if (
        not isinstance(value, str) or not value.startswith("campaign-offline-")
        or value != value.strip() or not value.replace("-", "").isalnum()
        or not 24 <= len(value) <= 96
        or any(term in value.casefold() for term in _FORBIDDEN)
    ):
        raise ValueError("invalid campaign")
    return value


def _bundle_directory(scenario: str) -> str:
    return scenario.casefold().replace("_", "-")


def _run_id(campaign_id: str, ordinal: int) -> str:
    return f"commissioning-{campaign_id.removeprefix('campaign-')}-{ordinal:02d}"


def _expected_classification(scenario: str) -> str:
    return {
        "SAFE_OFFLINE_COMPLETION": "COMPLETED",
        "OPERATOR_REVIEW_REQUIRED": "REVIEW_REQUIRED",
        "ACTIVE_SUSPENSION": "SUSPENDED",
        "AMBIGUOUS_RESTART": "AMBIGUOUS_RESTART",
    }.get(scenario, "FAILED_CLOSED")


def _read_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("object required")
    return raw


def _bundle_entry(campaign_id: str, root: Path, scenario: str, ordinal: int) -> dict[str, object]:
    directory = _bundle_directory(scenario)
    bundle = root / directory
    run_id = _run_id(campaign_id, ordinal)
    if not bundle.is_dir() or {p.name for p in bundle.iterdir()} != {*FILES, INDEX}:
        raise ValueError("bundle is incomplete")
    bundle_index = _read_json(bundle / INDEX)
    if set(bundle_index) != {"commissioning_run_id", "scenario_id", "injected_start_timestamp", "injected_end_timestamp", "files", "completion_status", "permits_automated_action", "live_ready", "unattended_ready"}:
        raise ValueError("bundle index schema changed")
    if bundle_index["commissioning_run_id"] != run_id or bundle_index["scenario_id"] != scenario or bundle_index["completion_status"] != "COMPLETED":
        raise ValueError("bundle identity is invalid")
    if any(bundle_index[key] is not False for key in ("permits_automated_action", "live_ready", "unattended_ready")):
        raise ValueError("bundle readiness is invalid")
    files = bundle_index["files"]
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise ValueError("bundle files are invalid")
    for name in FILES:
        content = (bundle / name).read_bytes()
        if files[name] != hashlib.sha256(content).hexdigest():
            raise ValueError("bundle hash mismatch")
        if any(term.encode() in content.lower() for term in _FORBIDDEN):
            raise ValueError("bundle privacy violation")
    evidence = _read_json(bundle / "evidence-summary.json")
    occurrences = evidence.get("occurrence_ids")
    expected_occurrence = "occurrence-" + run_id.removeprefix("commissioning-") + "-01"
    if occurrences != [expected_occurrence] or evidence.get("permits_automated_action") is not False:
        raise ValueError("occurrence binding is invalid")
    return {
        "scenario_id": scenario,
        "relative_bundle_directory": directory,
        "commissioning_run_id": run_id,
        "occurrence_id": expected_occurrence,
        "expected_exit_classification": _expected_classification(scenario),
        "observed_exit_classification": _expected_classification(scenario),
        "bundle_index_hash": hashlib.sha256((bundle / INDEX).read_bytes()).hexdigest(),
        "verification_status": "VERIFIED",
    }


def _index_payload(campaign_id: str, entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "campaign_id": campaign_id, "schema_version": SCHEMA_VERSION,
        "scenario_order": list(SCENARIOS), "scenarios": entries,
        "campaign_result": "VERIFIED_OFFLINE_CAMPAIGN",
        "permits_automated_action": False, "live_ready": False,
        "unattended_ready": False,
    }


def verify_campaign(output: Path) -> dict[str, object]:
    """Verify an already published campaign, refusing every mismatch."""
    index = _read_json(output / CAMPAIGN_INDEX)
    if set(index) != _CAMPAIGN_KEYS or index["schema_version"] != SCHEMA_VERSION:
        raise ValueError("campaign index schema is invalid")
    campaign_id = _campaign_id(index["campaign_id"])
    if index["scenario_order"] != list(SCENARIOS) or index["campaign_result"] != "VERIFIED_OFFLINE_CAMPAIGN":
        raise ValueError("campaign order or result is invalid")
    if any(index[key] is not False for key in ("permits_automated_action", "live_ready", "unattended_ready")):
        raise ValueError("campaign safety flags are invalid")
    entries = index["scenarios"]
    if not isinstance(entries, list) or len(entries) != len(SCENARIOS) or any(not isinstance(item, dict) for item in entries):
        raise ValueError("campaign scenarios are incomplete")
    expected = [_bundle_entry(campaign_id, output, scenario, i) for i, scenario in enumerate(SCENARIOS, 1)]
    if entries != expected:
        raise ValueError("campaign bindings are invalid")
    return index


def run_campaign(*, campaign_id: str, output: Path) -> int:
    """Run each catalog scenario once and publish one verified index."""
    campaign_id = _campaign_id(campaign_id)
    output = output.expanduser().resolve()
    if output.exists() and (output / CAMPAIGN_INDEX).exists():
        raise FileExistsError("completed campaign already exists")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("partial campaign output already exists")
    output.mkdir(parents=True, exist_ok=True)
    for ordinal, scenario in enumerate(SCENARIOS, 1):
        run_id = _run_id(campaign_id, ordinal)
        original_mkdtemp = tempfile.mkdtemp

        def deterministic_workspace(*, prefix: str = "") -> str:
            workspace = Path(tempfile.gettempdir()) / (prefix + run_id)
            if workspace.exists():
                raise FileExistsError("commissioning workspace already exists")
            workspace.mkdir()
            return os.fspath(workspace)

        tempfile.mkdtemp = deterministic_workspace
        try:
            result = run_commissioning(
                scenario=scenario, run_id=run_id, start=_START,
                tick_count=2, output=output / _bundle_directory(scenario),
            )
        finally:
            tempfile.mkdtemp = original_mkdtemp
        classification = _EXIT_CLASSIFICATIONS.get(result, "FAILED_CLOSED")
        if classification != _expected_classification(scenario):
            raise ValueError("scenario outcome mismatch")
    entries = [_bundle_entry(campaign_id, output, scenario, i) for i, scenario in enumerate(SCENARIOS, 1)]
    index = _index_payload(campaign_id, entries)
    temporary = output / ("." + CAMPAIGN_INDEX + ".partial")
    if temporary.exists():
        raise FileExistsError("partial campaign publication exists")
    with temporary.open("xb") as handle:
        handle.write(_encoded(index)); handle.flush(); os.fsync(handle.fileno())
    temporary.replace(output / CAMPAIGN_INDEX)
    verify_campaign(output)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        return run_campaign(campaign_id=args.campaign_id, output=args.output)
    except (SystemExit, ValueError, FileExistsError, OSError, json.JSONDecodeError):
        return EXIT_INVALID
    except Exception:
        return EXIT_FAILED_CLOSED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CAMPAIGN_INDEX", "SCENARIOS", "main", "run_campaign", "verify_campaign"]
