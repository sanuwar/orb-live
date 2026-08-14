"""Sanitized, idempotent projection of completed morning-watch evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from src.runtime_filter_projection import FilterProjectionError, project_filter_facts


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_PATH = REPOSITORY_ROOT / "artifacts" / "operator-projection" / "runtime-journal.json"
SCHEMA_VERSION = 1
_DENIED = ("account", "token", "credential", "cookie", "authorization", "header", "request", "response", "path")
_SAFE_COUNT_KEYS = frozenset({"requestcount", "totalhttprequestcount"})
_FINAL_STATES = frozenset({"WINDOW_COMPLETE_NO_CANDIDATE", "CANDIDATE_CAPTURED", "OPERATOR_STOPPED", "PROVIDER_FAILURE"})


class RuntimeProjectionError(ValueError):
    """Evidence cannot be safely projected."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_code(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or not all(char.isupper() or char.isdigit() or char in "_:-" for char in value):
        raise RuntimeProjectionError(f"invalid {field}")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or "T" not in value or len(value) > 48:
        raise RuntimeProjectionError("invalid timestamp")
    return value.replace("+00:00", "Z")


def _contains_private(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = key.casefold().replace("_", "")
            if normalized not in _SAFE_COUNT_KEYS and any(part in normalized for part in _DENIED):
                return True
            if _contains_private(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    return False


def _record(*, projection_key: str, status: str, stage: str, occurred_at: str, symbol: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "journalRecordId": "runtime-" + _digest(projection_key)[:16], "source": "AUTOMATIC_RUNTIME",
        "environment": "LIVE", "runId": projection_key, "symbol": symbol, "side": None, "quantity": None,
        "status": status, "commissioningStage": stage, "reason": reason, "eventTimestamp": occurred_at,
        "automaticallyImported": True, "operatorInterventionRequired": False, "initiator": "SYSTEM",
    }


def _event(*, projection_key: str, event_type: str, occurred_at: str, severity: str, component: str, detail_code: str) -> dict[str, str]:
    return {
        "eventId": "runtime-event-" + _digest(projection_key + ":" + event_type)[:16], "eventType": event_type,
        "occurredAt": occurred_at, "severity": severity, "component": component, "detailCode": detail_code,
        "source": "AUTOMATIC_RUNTIME",
    }


def _single_value(facts: list[dict[str, Any]], field: str) -> str | None:
    values = {fact[field] for fact in facts if fact.get(field) is not None}
    return values.pop() if len(values) == 1 else None


def _funnel_summary(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize evaluator-owned gates without converting unknowns to passes."""
    if not facts:
        return None
    gap_known = all(fact["gates"]["openingGap"] in {"PASSED", "REJECTED"} for fact in facts)
    gap_survivors = [fact for fact in facts if fact["gates"]["openingGap"] == "PASSED"]
    rvol_known = gap_known and all(fact["gates"]["openingRvol"] in {"PASSED", "REJECTED"} for fact in gap_survivors)
    rvol_survivors = [fact for fact in gap_survivors if fact["gates"]["openingRvol"] == "PASSED"]
    enabled_values = {fact["spyVwapEnabled"] for fact in rvol_survivors}
    spy_enabled = enabled_values.pop() if len(enabled_values) == 1 else None
    spy_known = rvol_known and spy_enabled is not None and all(
        fact["gates"]["spyVwap"] in ({"PASSED", "WAITING"} if spy_enabled else {"NOT_EVALUATED"})
        for fact in rvol_survivors
    )
    spy_passes = [fact for fact in rvol_survivors if fact["gates"]["spyVwap"] == "PASSED"]
    breakout_known = rvol_known and all(fact["gates"]["orbBreakout"] in {"PASSED", "WAITING", "NOT_EVALUATED"} for fact in rvol_survivors)
    breakout_passes = [fact for fact in rvol_survivors if fact["gates"]["orbBreakout"] == "PASSED"]
    rejected = [
        {"symbol": fact["symbol"], "reason": fact["terminalRejectionReason"], "failedRule": fact["failedRule"]}
        for fact in facts if fact["terminalRejectionReason"] is not None
    ]
    alive = [fact["symbol"] for fact in rvol_survivors]
    unavailable = [
        fact["symbol"] for fact in facts
        if fact["terminalRejectionReason"] is None and fact["symbol"] not in alive
    ]
    return {
        "status": "AVAILABLE" if gap_known and rvol_known and not unavailable else "INCOMPLETE",
        "startingSymbolCount": len(facts),
        "survivorsAfterGap": len(gap_survivors) if gap_known else None,
        "survivorsAfterOpeningRvol": len(rvol_survivors) if rvol_known else None,
        "survivorsAfterSpyVwap": len(spy_passes) if spy_known and spy_enabled else len(rvol_survivors) if spy_known else None,
        "survivorsAfterBreakout": len(breakout_passes) if breakout_known else None,
        "spyVwapEnabled": spy_enabled,
        "gapMinimumPct": _single_value(facts, "requiredGapPct"),
        "openingRvolMinimum": _single_value(facts, "requiredOpeningRvol"),
        "aliveSymbols": alive,
        "rejectedSymbols": rejected,
        "unavailableSymbols": unavailable,
    }


def project_watch_summary(summary: Mapping[str, Any], *, artifact_name: str) -> dict[str, Any]:
    """Project one authentic persisted watcher summary without raw references."""
    if _contains_private(summary) or not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        raise RuntimeProjectionError("unsafe watch evidence")
    final_state = _safe_code(summary.get("final_state"), field="final state")
    if final_state not in _FINAL_STATES:
        raise RuntimeProjectionError("unsupported watch final state")
    started_at, completed_at = _timestamp(summary.get("started_at")), _timestamp(summary.get("completed_at"))
    stop_reason = _safe_code(summary.get("stop_reason"), field="stop reason")
    evaluations = summary.get("evaluations")
    if not isinstance(evaluations, list) or len(evaluations) > 17:
        raise RuntimeProjectionError("invalid evaluations")
    evidence_id = _digest({"artifact": artifact_name, "summary": summary})
    session_id = "watch-" + evidence_id[:20]
    records = [_record(projection_key=session_id, status=final_state, stage="COMPLETE", occurred_at=completed_at, reason=stop_reason)]
    events = [_event(projection_key=session_id, event_type="WATCH_SESSION_COMPLETED", occurred_at=completed_at, severity="INFO" if final_state != "PROVIDER_FAILURE" else "WARNING", component="morning_watcher", detail_code=final_state)]
    evaluation_facts: list[dict[str, Any]] = []
    for ordinal, evaluation in enumerate(evaluations):
        if not isinstance(evaluation, Mapping):
            raise RuntimeProjectionError("invalid evaluation")
        when = _timestamp(evaluation.get("scheduled_at"))
        candidates = evaluation.get("candidate_decisions")
        if not isinstance(candidates, list) or len(candidates) > 1:
            raise RuntimeProjectionError("invalid candidate decisions")
        key = f"{session_id}:evaluation:{ordinal}"
        try:
            filter_facts = project_filter_facts(evaluation.get("evidence_artifact"))
        except FilterProjectionError:
            filter_facts = []
        evaluation_facts.append({"scheduledAt": when, "filterEvidence": "AVAILABLE" if filter_facts else "UNAVAILABLE", "filterFacts": filter_facts, "funnel": _funnel_summary(filter_facts)})
        records.append(_record(projection_key=key, status="EVALUATED", stage="RUN_STARTED", occurred_at=when, reason=_safe_code(str(evaluation.get("quality_outcome", "UNKNOWN")), field="quality outcome")))
        events.append(_event(projection_key=key, event_type="SCANNER_EVALUATED", occurred_at=when, severity="INFO", component="morning_watcher", detail_code="SCANNER_EVALUATED"))
        if candidates:
            candidate = candidates[0]
            if not isinstance(candidate, Mapping):
                raise RuntimeProjectionError("invalid candidate")
            symbol = candidate.get("symbol")
            if not isinstance(symbol, str) or not symbol.isupper() or len(symbol) > 12:
                raise RuntimeProjectionError("invalid candidate symbol")
            candidate_key = key + ":candidate"
            records.append(_record(projection_key=candidate_key, status="CANDIDATE_CAPTURED", stage="CANDIDATE_CAPTURED", occurred_at=when, symbol=symbol, reason="LONG_CANDIDATE"))
            events.append(_event(projection_key=candidate_key, event_type="CANDIDATE_DETECTED", occurred_at=when, severity="REVIEW", component="strategy", detail_code="LONG_CANDIDATE"))
    return {"schemaVersion": SCHEMA_VERSION, "sourceEvidenceId": evidence_id, "session": {"sessionId": session_id, "startedAt": started_at, "completedAt": completed_at, "terminalState": final_state, "stopReason": stop_reason, "evaluationCount": len(evaluations), "evaluations": evaluation_facts}, "records": records, "events": events}


def read_history(path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    if not Path(path).is_file():
        return {"schemaVersion": SCHEMA_VERSION, "status": "NO_AUTOMATIC_EVIDENCE", "sessions": [], "records": [], "events": []}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeProjectionError("projection history is unavailable") from exc
    if not isinstance(value, Mapping) or value.get("schemaVersion") != SCHEMA_VERSION or _contains_private(value):
        raise RuntimeProjectionError("projection history is unsafe")
    for key in ("sessions", "records", "events"):
        if not isinstance(value.get(key), list):
            raise RuntimeProjectionError("projection history is malformed")
    return dict(value)


def append_watch_summary(summary: Mapping[str, Any], *, artifact_name: str, history_path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    projected = project_watch_summary(summary, artifact_name=artifact_name)
    current = read_history(history_path)
    known = {item.get("sourceEvidenceId") for item in current.get("sessions", []) if isinstance(item, Mapping)}
    if projected["sourceEvidenceId"] in known:
        return {**current, "status": "ALREADY_PROJECTED"}
    sessions = [*current["sessions"], {"sourceEvidenceId": projected["sourceEvidenceId"], **projected["session"]}]
    result = {"schemaVersion": SCHEMA_VERSION, "status": "AUTOMATIC_EVIDENCE_AVAILABLE", "sessions": sessions, "records": [*current["records"], *projected["records"]], "events": sorted([*current["events"], *projected["events"]], key=lambda item: (item["occurredAt"], item["eventId"]), reverse=True)}
    target = Path(history_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="runtime-journal-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, target)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return result


def append_watch_artifact(artifact: Path, *, history_path: Path = DEFAULT_HISTORY_PATH) -> dict[str, Any]:
    try:
        summary = json.loads(Path(artifact).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeProjectionError("watch evidence is unavailable") from exc
    if not isinstance(summary, Mapping):
        raise RuntimeProjectionError("watch evidence is malformed")
    return append_watch_summary(summary, artifact_name=Path(artifact).name, history_path=history_path)


__all__ = ["DEFAULT_HISTORY_PATH", "RuntimeProjectionError", "append_watch_artifact", "append_watch_summary", "project_watch_summary", "read_history"]
