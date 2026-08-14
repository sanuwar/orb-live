"""Safe filter facts derived from authenticated local LIVE_SHADOW evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_EVIDENCE_ROOT = (REPOSITORY_ROOT / "artifacts" / "live-shadow").resolve()
_STATES = frozenset({"VALID_DECISION", "NO_SIGNAL", "REJECTED", "DATA_INVALID", "DATA_INCOMPLETE", "PROVIDER_FAILURE"})
_RULES = frozenset({"OPENING_GAP", "OPENING_RELATIVE_VOLUME", "SPY_ABOVE_VWAP", "OPENING_RANGE_BREAKOUT", "ENTRY_WINDOW", "OTHER"})


class FilterProjectionError(ValueError):
    """Evidence cannot safely supply filter facts."""


def project_filter_facts(reference: object) -> list[dict[str, str]]:
    """Extract only evaluator-owned state and failed-rule facts from local evidence."""
    if not isinstance(reference, str):
        return []
    try:
        path = Path(reference).resolve(strict=True)
        if _EVIDENCE_ROOT not in path.parents:
            raise FilterProjectionError("unsafe evaluation evidence reference")
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FilterProjectionError("evaluation evidence is unavailable") from exc
    results = evidence.get("symbol_results") if isinstance(evidence, Mapping) else None
    if not isinstance(results, Mapping) or not results or len(results) > 10:
        raise FilterProjectionError("evaluation evidence is malformed")
    projected: list[dict[str, str]] = []
    for symbol, result in sorted(results.items()):
        if not isinstance(symbol, str) or not symbol.isupper() or len(symbol) > 12 or not isinstance(result, Mapping):
            raise FilterProjectionError("evaluation evidence is malformed")
        state = result.get("evaluation_state")
        if state not in _STATES:
            raise FilterProjectionError("evaluation state is invalid")
        diagnostics = result.get("strategy_diagnostics")
        failed_rule = diagnostics.get("failed_rule") if isinstance(diagnostics, Mapping) else None
        if failed_rule is not None and failed_rule not in _RULES:
            raise FilterProjectionError("filter fact is invalid")
        projected.append({"symbol": symbol, "state": state, "failedRule": failed_rule or "UNAVAILABLE"})
    return projected


__all__ = ["FilterProjectionError", "project_filter_facts"]