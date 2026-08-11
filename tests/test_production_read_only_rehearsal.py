from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_production_mode_honestly_stops_for_saved_no_candidate_without_account_access(tmp_path: Path):
    shadow = tmp_path / "shadow.json"
    handoff = tmp_path / "unused-handoff.json"
    shadow.write_text(json.dumps({"run_mode": "LIVE_SHADOW", "provider": "alpaca", "shadow_trading_decision": {"decision_type": "REJECTED"}}), encoding="utf-8")
    handoff.write_text("{}", encoding="utf-8")
    command = [sys.executable, "scripts/run_production_read_only_rehearsal.py", "--production-read-only", "--live-shadow", str(shadow), "--handoff", str(handoff), "--release-sha", "a" * 40, "--suspension-db", str(tmp_path / "suspension.db")]
    for acknowledgement in ("acknowledges_loss_may_exceed_plan", "operator_present", "robinhood_ui_open", "no_second_trade"):
        command.extend(("--ack", acknowledgement))
    completed = subprocess.run(command, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result == {"broker_write_count": 0, "execution_authorized": False, "final_rehearsal_state": "NO_VALID_CANDIDATE", "protection_grounded": False}


def test_production_mode_requires_all_explicit_acknowledgements(tmp_path: Path):
    shadow = tmp_path / "shadow.json"
    shadow.write_text(json.dumps({"run_mode": "LIVE_SHADOW", "provider": "alpaca", "shadow_trading_decision": {"decision_type": "REJECTED"}}), encoding="utf-8")
    completed = subprocess.run([sys.executable, "scripts/run_production_read_only_rehearsal.py", "--production-read-only", "--live-shadow", str(shadow), "--handoff", str(tmp_path / "x.json"), "--release-sha", "a" * 40, "--suspension-db", str(tmp_path / "s.db")], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert completed.returncode != 0
    assert "acknowledgements" in completed.stderr
