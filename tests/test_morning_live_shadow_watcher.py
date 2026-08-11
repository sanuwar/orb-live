from __future__ import annotations

import ast
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.live_shadow_session import LiveShadowError
from src.morning_live_shadow_watcher import (
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_WATCHLIST,
    MAX_EVALUATIONS,
    MorningWatchError,
    WatchLimits,
    run_morning_watch,
    scheduled_evaluations,
)


WINDOW_START = datetime(2026, 8, 11, 13, 40, tzinfo=timezone.utc)  # 09:40 ET


async def no_sleep(_: float) -> None:
    return None


def evidence(*, run_id: str, candidate: bool = False, rejected: bool = False, error: str | None = None, requests: int = 3) -> dict:
    decision_type = "LONG_CANDIDATE" if candidate else ("REJECTED" if rejected else "NO_SIGNAL")
    return {
        "run_id": run_id,
        "request_count": requests,
        "quality_outcome": "VALID",
        "errors": [error] if error else [],
        "symbol_results": {
            "AAPL": {
                "shadow_trading_decision": {
                    "decision_type": decision_type,
                    "decision_id": f"decision-{run_id}",
                },
                "strategy_diagnostics": {"failed_rule": "OPENING_GAP" if rejected else None},
            }
        },
    }


def evaluator_for(values: list[dict]):
    calls: list[datetime] = []

    async def evaluate(moment: datetime):
        calls.append(moment)
        return values.pop(0), Path(f"underlying-{len(calls)}.json")

    return calls, evaluate


def test_default_watchlist_and_exact_five_minute_schedule():
    points = scheduled_evaluations(day=date(2026, 8, 11), interval_minutes=DEFAULT_INTERVAL_MINUTES)
    assert DEFAULT_WATCHLIST == ("AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "NFLX")
    assert len(points) == MAX_EVALUATIONS == 17
    assert points[0] == WINDOW_START
    assert points[-1] == datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)  # 11:00 ET
    assert all((right - left).total_seconds() == 300 for left, right in zip(points, points[1:]))


def test_interval_and_evaluation_bounds_fail_closed():
    with pytest.raises(MorningWatchError):
        WatchLimits(interval_minutes=4)
    with pytest.raises(MorningWatchError):
        WatchLimits(max_evaluations=18)
    with pytest.raises(LiveShadowError):
        asyncio.run(run_morning_watch(symbols=tuple(f"S{index}" for index in range(11)), now=WINDOW_START, production_confirmed=True))


def test_before_and_after_window_make_zero_evaluations(tmp_path):
    before, _ = asyncio.run(run_morning_watch(now=datetime(2026, 8, 11, 13, 35, tzinfo=timezone.utc), output_dir=tmp_path / "before", production_confirmed=True))
    after, _ = asyncio.run(run_morning_watch(now=datetime(2026, 8, 11, 15, 1, tzinfo=timezone.utc), output_dir=tmp_path / "after", production_confirmed=True))
    assert before["final_state"] == "BEFORE_ENTRY_WINDOW"
    assert after["final_state"] == "OUTSIDE_ENTRY_WINDOW"
    assert before["evaluation_count"] == after["evaluation_count"] == 0
    assert before["total_http_request_count"] == after["total_http_request_count"] == 0


def test_explicit_production_confirmation_is_required_before_evaluation(tmp_path):
    summary, _ = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=False))
    assert summary["final_state"] == "PRODUCTION_CONFIRMATION_REQUIRED"
    assert summary["evaluation_count"] == summary["total_http_request_count"] == 0


def test_no_candidate_runs_bounded_schedule_and_preserves_underlying_references(tmp_path):
    values = [evidence(run_id=f"run-{index}") for index in range(MAX_EVALUATIONS)]
    calls, evaluate = evaluator_for(values)
    summary, path = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=True, evaluator=evaluate, sleeper=no_sleep))
    saved = json.loads(path.read_text())
    assert len(calls) == summary["evaluation_count"] == MAX_EVALUATIONS
    assert summary["final_state"] == "WINDOW_COMPLETE_NO_CANDIDATE"
    assert summary["total_http_request_count"] == MAX_EVALUATIONS * 3
    assert saved["evaluations"][0]["evidence_artifact"] == "underlying-1.json"
    assert saved["evaluations"][0]["candidate_count"] == 0


def test_first_genuine_candidate_stops_and_keeps_its_artifact_reference(tmp_path):
    calls, evaluate = evaluator_for([evidence(run_id="rejected", rejected=True), evidence(run_id="candidate", candidate=True), evidence(run_id="unused", candidate=True)])
    summary, _ = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=True, evaluator=evaluate, sleeper=no_sleep))
    assert len(calls) == summary["evaluation_count"] == 2
    assert summary["final_state"] == "CANDIDATE_CAPTURED"
    assert summary["first_candidate_timestamp"] == WINDOW_START.replace(minute=45)
    assert summary["evaluations"][-1]["evidence_artifact"] == "underlying-2.json"
    assert summary["evaluations"][-1]["candidate_decisions"] == [{"symbol": "AAPL", "decision_id": "decision-candidate"}]


def test_rejected_and_no_signal_decisions_never_become_candidates(tmp_path):
    calls, evaluate = evaluator_for([evidence(run_id=f"rejected-{index}", rejected=True) for index in range(MAX_EVALUATIONS)])
    summary, _ = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=True, evaluator=evaluate, sleeper=no_sleep))
    assert len(calls) == MAX_EVALUATIONS
    assert summary["final_state"] == "WINDOW_COMPLETE_NO_CANDIDATE"


def test_shared_provider_failure_stops_fail_closed(tmp_path):
    calls, evaluate = evaluator_for([evidence(run_id="failed", error="PROVIDER_HTTP_ERROR")])
    summary, _ = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=True, evaluator=evaluate, sleeper=no_sleep))
    assert len(calls) == 1
    assert summary["final_state"] == "PROVIDER_FAILURE"
    assert summary["stop_reason"] == "SHARED_PROVIDER_FAILURE"


def test_operator_interrupt_persists_sanitized_partial_summary(tmp_path):
    async def interrupt(_: datetime):
        raise KeyboardInterrupt

    summary, path = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path, production_confirmed=True, evaluator=interrupt, sleeper=no_sleep))
    assert summary["final_state"] == "OPERATOR_STOPPED"
    assert json.loads(path.read_text())["broker_write_count"] == 0


def test_summary_is_deterministic_for_deterministic_inputs(tmp_path):
    values_one = [evidence(run_id=f"run-{index}") for index in range(MAX_EVALUATIONS)]
    values_two = [evidence(run_id=f"run-{index}") for index in range(MAX_EVALUATIONS)]
    _, first = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path / "one", production_confirmed=True, evaluator=evaluator_for(values_one)[1], sleeper=no_sleep))
    _, second = asyncio.run(run_morning_watch(now=WINDOW_START, output_dir=tmp_path / "two", production_confirmed=True, evaluator=evaluator_for(values_two)[1], sleeper=no_sleep))
    assert json.loads(first.read_text()) == json.loads(second.read_text())


def test_watcher_reuses_live_shadow_without_strategy_or_broker_reachability():
    root = Path(__file__).resolve().parents[1]
    for relative in ("src/morning_live_shadow_watcher.py", "scripts/run_morning_live_shadow_watch.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        forbidden = {"SignalEngine", "RobinhoodCommissioningAdapter", "place_equity_order", "cancel_equity_order", "replace", "flatten", "call_tool"}
        assert not (names & forbidden), (relative, names & forbidden)
    source = (root / "src/morning_live_shadow_watcher.py").read_text(encoding="utf-8")
    assert "run_session(" in source
