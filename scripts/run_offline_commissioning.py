"""One finite, deterministic, privacy-safe offline commissioning run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.bounded_shadow_session import SESSION_NOW, run_bounded_shadow_session
from src.offline_evidence_summary import SessionEvidence, summarize_sessions
from src.offline_fixture_package import FixtureScenario, MAX_TICKS, build_fixture

EXIT_OK = 0
EXIT_REVIEW = 10
EXIT_SUSPENDED = 20
EXIT_FAILED_CLOSED = 30
EXIT_AMBIGUOUS = 40
EXIT_INVALID = 50
SCENARIOS = tuple(item.value for item in FixtureScenario if item.value not in {"ACKNOWLEDGED_BUT_STILL_SUSPENDED", "DURABLE_RESTART"})
FILES = ("session-report.json", "evidence-summary.json", "readiness-report.json", "privacy-audit.json")
INDEX = "commissioning-index.json"
_FORBIDDEN = ("account", "balance", "broker", "credential", "exception", "manifest", "order", "password", "price", "quantity", "secret", "symbol", "token", "username")


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_output(value: Path) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        raise ValueError
    if candidate.exists() and candidate.is_symlink():
        raise ValueError
    resolved = candidate.resolve(strict=False)
    temporary = Path(__import__("tempfile").gettempdir()).resolve()
    if temporary not in resolved.parents or ROOT in resolved.parents or resolved == ROOT:
        raise ValueError
    if any(part.casefold() == "data" for part in resolved.parts if part.casefold() == "data"):
        raise ValueError
    return resolved


def _parse_timestamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError
    if moment != moment.astimezone(timezone.utc):
        moment = moment.astimezone(timezone.utc)
    if moment.date() != SESSION_NOW.date() or moment > SESSION_NOW:
        raise ValueError
    return moment


def _validate_run_id(value: str) -> str:
    if not value.startswith("commissioning-") or value != value.strip() or not value.replace("-", "").isalnum() or not 16 <= len(value) <= 96:
        raise ValueError
    if any(term in value.casefold() for term in _FORBIDDEN):
        raise ValueError
    return value


def _exit_code(scenario: str) -> int:
    return {"SAFE_OFFLINE_COMPLETION": EXIT_OK, "OPERATOR_REVIEW_REQUIRED": EXIT_REVIEW, "ACTIVE_SUSPENSION": EXIT_SUSPENDED, "AMBIGUOUS_RESTART": EXIT_AMBIGUOUS}.get(scenario, EXIT_FAILED_CLOSED)


def _write_once(root: Path, name: str, content: bytes) -> None:
    target = root / name
    temporary = root / ("." + name + ".partial")
    if target.exists() or temporary.exists():
        raise FileExistsError
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target)


def run_commissioning(*, scenario: str, run_id: str, start: datetime, tick_count: int, output: Path) -> int:
    selected = FixtureScenario(scenario)
    run_id = _validate_run_id(run_id)
    if not isinstance(tick_count, int) or isinstance(tick_count, bool) or not 1 <= tick_count <= MAX_TICKS:
        raise ValueError
    if tick_count != 2:
        raise ValueError
    destination = _safe_output(output)
    destination.mkdir(parents=True, exist_ok=True)
    existing = {path.name for path in destination.iterdir() if path.name in (*FILES, INDEX)}
    if existing:
        raise FileExistsError
    ticks = (start, start + timedelta(minutes=1))
    if ticks[-1] > SESSION_NOW:
        raise ValueError
    build_fixture(selected, tick_timestamps=ticks)
    work = Path(__import__("tempfile").mkdtemp(prefix="orb-offline-commissioning-"))
    try:
        report = run_bounded_shadow_session(work / "session", tick_timestamps=ticks, maximum_tick_count=tick_count, evaluation_time=ticks[-1])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    evidence = summarize_sessions("batch-" + run_id.removeprefix("commissioning-"), (SessionEvidence("occurrence-" + run_id.removeprefix("commissioning-") + "-01", report),), evaluated_at=ticks[-1])
    session_payload = _json(report.to_dict())
    evidence_payload = _json(evidence.to_dict())
    if report._readiness_projection is None:
        raise RuntimeError
    readiness_payload = _json(report._readiness_projection)
    privacy_payload = _json({"privacy_safe": True, "allowlisted_files": [*FILES, INDEX], "prohibited_fields_absent": True, "live_ready": False, "unattended_ready": False, "permits_automated_action": False})
    payloads = dict(zip(FILES, (session_payload, evidence_payload, readiness_payload, privacy_payload)))
    if any(term.encode() in content.lower() for content in payloads.values() for term in ("account", "broker", "credential", "password", "symbol", "quantity", "price", "balance", "order")):
        raise ValueError
    for name, content in payloads.items():
        _write_once(destination, name, content)
    index = {"commissioning_run_id": run_id, "scenario_id": selected.value, "injected_start_timestamp": start.isoformat(), "injected_end_timestamp": ticks[-1].isoformat(), "files": {name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()}, "completion_status": "COMPLETED", "permits_automated_action": False, "live_ready": False, "unattended_ready": False}
    _write_once(destination, INDEX, _json(index))
    return _exit_code(selected.value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--tick-count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        return run_commissioning(scenario=args.scenario, run_id=args.run_id, start=_parse_timestamp(args.start), tick_count=args.tick_count, output=args.output)
    except (SystemExit, ValueError, FileExistsError, OSError, RuntimeError):
        return EXIT_INVALID
    except Exception:
        return EXIT_FAILED_CLOSED


if __name__ == "__main__":
    raise SystemExit(main())
