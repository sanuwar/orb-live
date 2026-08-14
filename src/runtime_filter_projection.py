"""Safe funnel facts derived from authentic local LIVE_SHADOW evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_EVIDENCE_ROOT = (REPOSITORY_ROOT / "artifacts" / "live-shadow").resolve()
_STATES = frozenset({"VALID_DECISION", "NO_SIGNAL", "REJECTED", "DATA_INVALID", "DATA_INCOMPLETE", "PROVIDER_FAILURE"})
_RULES = frozenset({"OPENING_GAP", "OPENING_RELATIVE_VOLUME", "SPY_ABOVE_VWAP", "OPENING_RANGE_BREAKOUT", "ENTRY_WINDOW", "OTHER"})
_GATE_STATES = frozenset({"PASSED", "REJECTED", "WAITING", "NOT_EVALUATED", "UNAVAILABLE"})
_STATIC_REJECTIONS = {
    "OPENING_GAP": "Opening gap is below the required threshold.",
    "OPENING_RELATIVE_VOLUME": "Opening relative volume is below the required threshold.",
}
_RULE_REASONS = {
    **_STATIC_REJECTIONS,
    "SPY_ABOVE_VWAP": "SPY is not above its session VWAP.",
    "OPENING_RANGE_BREAKOUT": "Completed bar has not broken above the opening-range high.",
    "ENTRY_WINDOW": "Outside the permitted entry window.",
}
_AUTHENTIC_REASONS = frozenset({
    *_STATIC_REJECTIONS.values(),
    "SPY is not above its session VWAP.",
    "Completed bar has not broken above the opening-range high.",
    "Outside the permitted entry window.",
    "ORB V0 long-entry signal approved.",
})
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?$")


class FilterProjectionError(ValueError):
    """Evidence cannot safely supply funnel facts."""


def _safe_decimal(value: object) -> str | None:
    return value if isinstance(value, str) and _DECIMAL.fullmatch(value) else None


def _reason(value: object) -> str | None:
    return value if value in _AUTHENTIC_REASONS else None


def _gate_states(*, state: str, failed_rule: str | None, spy_enabled: bool | None) -> dict[str, str]:
    unavailable = {"openingGap": "UNAVAILABLE", "openingRvol": "UNAVAILABLE", "spyVwap": "UNAVAILABLE", "orbBreakout": "UNAVAILABLE"}
    if state in {"DATA_INVALID", "DATA_INCOMPLETE", "PROVIDER_FAILURE"}:
        return unavailable
    if failed_rule == "ENTRY_WINDOW":
        return {name: "NOT_EVALUATED" for name in unavailable}
    if failed_rule == "OPENING_GAP":
        return {"openingGap": "REJECTED", "openingRvol": "NOT_EVALUATED", "spyVwap": "NOT_EVALUATED", "orbBreakout": "NOT_EVALUATED"}
    if failed_rule == "OPENING_RELATIVE_VOLUME":
        return {"openingGap": "PASSED", "openingRvol": "REJECTED", "spyVwap": "NOT_EVALUATED", "orbBreakout": "NOT_EVALUATED"}
    if failed_rule == "SPY_ABOVE_VWAP":
        return {"openingGap": "PASSED", "openingRvol": "PASSED", "spyVwap": "WAITING", "orbBreakout": "NOT_EVALUATED"}
    if failed_rule == "OPENING_RANGE_BREAKOUT":
        return {"openingGap": "PASSED", "openingRvol": "PASSED", "spyVwap": "PASSED" if spy_enabled is True else "NOT_EVALUATED" if spy_enabled is False else "UNAVAILABLE", "orbBreakout": "WAITING"}
    if state == "VALID_DECISION" and failed_rule is None:
        return {"openingGap": "PASSED", "openingRvol": "PASSED", "spyVwap": "PASSED" if spy_enabled is True else "NOT_EVALUATED" if spy_enabled is False else "UNAVAILABLE", "orbBreakout": "PASSED"}
    return unavailable


def _project_result(symbol: str, result: Mapping[str, Any]) -> dict[str, Any]:
    state = result.get("evaluation_state")
    if state not in _STATES:
        raise FilterProjectionError("evaluation state is invalid")
    diagnostics = result.get("strategy_diagnostics")
    failed_rule = diagnostics.get("failed_rule") if isinstance(diagnostics, Mapping) else None
    if failed_rule is not None and failed_rule not in _RULES:
        raise FilterProjectionError("filter fact is invalid")
    spy_enabled_value = diagnostics.get("spy_vwap_filter_enabled") if isinstance(diagnostics, Mapping) else None
    spy_enabled = spy_enabled_value if isinstance(spy_enabled_value, bool) else True if failed_rule == "SPY_ABOVE_VWAP" else None
    reason = _reason(result.get("no_signal_or_rejection_reason"))
    if failed_rule in _RULE_REASONS and (state != "REJECTED" or reason != _RULE_REASONS[failed_rule]):
        raise FilterProjectionError("contradictory filter evidence")
    if failed_rule == "SPY_ABOVE_VWAP" and spy_enabled is not True:
        raise FilterProjectionError("contradictory SPY filter evidence")
    gates = _gate_states(state=state, failed_rule=failed_rule, spy_enabled=spy_enabled)
    if any(value not in _GATE_STATES for value in gates.values()):
        raise FilterProjectionError("gate state is invalid")
    terminal_reason = reason if failed_rule in _STATIC_REJECTIONS and reason == _STATIC_REJECTIONS[failed_rule] else None
    return {
        "symbol": symbol,
        "state": state,
        "failedRule": failed_rule or "UNAVAILABLE",
        "rejectionReason": reason,
        "terminalRejectionReason": terminal_reason,
        "spyVwapEnabled": spy_enabled,
        "gates": gates,
        "observedGapPct": _safe_decimal(diagnostics.get("observed_gap_pct")) if isinstance(diagnostics, Mapping) else None,
        "requiredGapPct": _safe_decimal(diagnostics.get("required_gap_pct")) if isinstance(diagnostics, Mapping) else None,
        "observedOpeningRvol": _safe_decimal(diagnostics.get("observed_opening_relative_volume")) if isinstance(diagnostics, Mapping) else None,
        "requiredOpeningRvol": _safe_decimal(diagnostics.get("required_opening_relative_volume")) if isinstance(diagnostics, Mapping) else None,
    }


def project_filter_facts(reference: object) -> list[dict[str, Any]]:
    """Extract only evaluator-owned funnel state from a local evidence artifact."""
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
    projected: list[dict[str, Any]] = []
    for symbol, result in sorted(results.items()):
        if not isinstance(symbol, str) or not symbol.isupper() or len(symbol) > 12 or not isinstance(result, Mapping):
            raise FilterProjectionError("evaluation evidence is malformed")
        projected.append(_project_result(symbol, result))
    return projected


def static_filter_eliminations(evidence: Mapping[str, Any], *, symbols: Sequence[str]) -> frozenset[str]:
    """Return symbols authentically rejected by immutable opening filters.

    Missing, malformed, dynamic-gate, or contradictory facts establish no
    elimination. The watcher may stop only after this exact set has
    accumulated every configured symbol.
    """
    results = evidence.get("symbol_results")
    if not isinstance(results, Mapping):
        return frozenset()
    eliminated: set[str] = set()
    for symbol in symbols:
        result = results.get(symbol)
        if not isinstance(result, Mapping) or result.get("evaluation_state") != "REJECTED":
            continue
        diagnostics = result.get("strategy_diagnostics")
        failed_rule = diagnostics.get("failed_rule") if isinstance(diagnostics, Mapping) else None
        if failed_rule in _STATIC_REJECTIONS and result.get("no_signal_or_rejection_reason") == _STATIC_REJECTIONS[failed_rule]:
            eliminated.add(symbol)
    return frozenset(eliminated)


__all__ = ["FilterProjectionError", "project_filter_facts", "static_filter_eliminations"]
