from __future__ import annotations

import ast
import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from scripts.check_morning_watch_readiness import MorningReadiness
from src.local_watcher_control import (
    CONTROL_HOST,
    CONTROL_PORT,
    CSRF_TOKEN,
    LocalControlConfig,
    LocalControlError,
    LocalWatcherControlApp,
    MorningWatcherController,
    WATCHER_COMMAND,
)


NOW = datetime(2026, 8, 13, 13, 30, tzinfo=timezone.utc)


def readiness(*, ready: bool = True) -> MorningReadiness:
    return MorningReadiness(
        source_sha="a" * 40,
        implementation_digest="b" * 64,
        pinned_closure_count=51,
        strategy_name="orb_long_v0",
        strategy_version="0.1.0",
        watchlist=("AAPL",),
        entry_start="09:40",
        entry_end="11:00",
        market_session="PRE_MARKET",
        market_reason="OK",
        alpaca_key_present=ready,
        alpaca_secret_present=ready,
        local_config_present=True,
        account_fingerprint_resolvable=True,
        suspension_db_status="NOT CHECKED",
        blockers=() if ready else ("ALPACA_API_KEY_ID_MISSING",),
    )


class FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "done"):
        self.returncode = None
        self.final_returncode = returncode
        self.stdout = stdout
        self.done = threading.Event()
        self.signals: list[int] = []

    def poll(self):
        return self.returncode

    def communicate(self):
        self.done.wait(timeout=2)
        self.returncode = self.final_returncode
        return self.stdout, "private stderr must not be returned"

    def send_signal(self, value):
        self.signals.append(value)
        self.done.set()

    def finish(self):
        self.done.set()


def controller(tmp_path, *, ready=True, process=None, marker=None, summary=None):
    child = process or FakeProcess()
    value = MorningWatcherController(
        readiness_provider=lambda: readiness(ready=ready),
        process_factory=lambda: child,
        clock=lambda: NOW,
        ownership_marker=marker or tmp_path / "owner.json",
        summary_reader=lambda _stdout: summary or {},
    )
    return value, child


def wait_for(value, state):
    for _ in range(100):
        if value.status()["process_state"] == state:
            return value.status()
        time.sleep(0.01)
    raise AssertionError(value.status())


def call_app(app, path, *, method="GET", origin="http://127.0.0.1:5173", csrf=None, body=b""):
    environ = {}
    setup_testing_defaults(environ)
    environ.update({
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "HTTP_ORIGIN": origin,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    })
    if csrf is not None:
        environ["HTTP_X_ORB_CSRF"] = csrf
    captured = {}
    result = b"".join(app(environ, lambda status, headers: captured.update(status=status, headers=dict(headers))))
    return captured["status"], json.loads(result) if result else None


def test_config_binds_only_to_fixed_loopback():
    assert LocalControlConfig().validate().host == CONTROL_HOST == "127.0.0.1"
    assert LocalControlConfig().port == CONTROL_PORT == 8766
    with pytest.raises(LocalControlError):
        LocalControlConfig(host="0.0.0.0").validate()


def test_readiness_success_allows_one_watcher_and_duplicate_start_is_rejected(tmp_path):
    value, child = controller(tmp_path)
    started, status = value.start()
    assert started and status["process_state"] == "RUNNING"
    duplicate, status = value.start()
    assert not duplicate and status["process_state"] == "RUNNING"
    child.finish()
    wait_for(value, "NOT_RUNNING")


def test_readiness_failure_blocks_without_launching(tmp_path):
    calls = []
    value = MorningWatcherController(
        readiness_provider=lambda: readiness(ready=False),
        process_factory=lambda: calls.append(True),
        ownership_marker=tmp_path / "owner.json",
    )
    started, status = value.start()
    assert not started and status["process_state"] == "BLOCKED"
    assert status["readiness"]["blockers"] == ["ALPACA_API_KEY_ID_MISSING"]
    assert calls == []


def test_stop_signals_only_owned_child_and_no_process_is_truthful_noop(tmp_path):
    value, child = controller(tmp_path)
    changed, status = value.stop()
    assert not changed and status["process_state"] == "NOT_RUNNING"
    assert value.start()[0]
    changed, status = value.stop()
    assert changed and status["process_state"] == "STOP_REQUESTED"
    assert len(child.signals) == 1


def test_restart_marker_blocks_start_without_attaching_or_killing(tmp_path):
    marker = tmp_path / "owner.json"
    marker.write_text('{"owned":true}', encoding="utf-8")
    calls = []
    value = MorningWatcherController(
        readiness_provider=lambda: readiness(),
        process_factory=lambda: calls.append(True),
        ownership_marker=marker,
    )
    started, status = value.start()
    assert not started and status["process_state"] == "OWNERSHIP_UNKNOWN"
    assert calls == []


@pytest.mark.parametrize("terminal,expected", [
    ("WINDOW_COMPLETE_NO_CANDIDATE", "NO_CANDIDATE"),
    ("CANDIDATE_CAPTURED", "CANDIDATE"),
])
def test_terminal_state_is_captured_without_continuing_to_handoff(tmp_path, terminal, expected):
    summary = {"final_state": terminal, "stop_reason": "DONE", "artifact_reference": "safe.json"}
    value, child = controller(tmp_path, summary=summary)
    assert value.start()[0]
    child.finish()
    status = wait_for(value, "NOT_RUNNING")
    assert status["watcher_result"] == "NONE"
    assert status["last_run"]["result"] == expected
    assert status["last_run"]["terminal_state"] == terminal
    assert status["broker_write_count"] == 0
    assert status["activation_approved"] is False
    assert status["permits_automated_action"] is False
    assert status["live_ready"] is False
    assert status["unattended_ready"] is False
    assert status["authority"] == "ATTENDED_COMMISSIONING_ONLY"
    source = Path("src/local_watcher_control.py").read_text(encoding="utf-8")
    assert "build_live_candidate_commissioning_handoff" not in source
    assert "run_production_read_only_rehearsal" not in source


def test_nonzero_or_unrecognized_exit_fails_without_restart(tmp_path):
    child = FakeProcess(returncode=2)
    value, child = controller(tmp_path, process=child)
    assert value.start()[0]
    child.finish()
    status = wait_for(value, "NOT_RUNNING")
    assert status["last_run"]["result"] == "FAILED"
    assert value.start()[0] is True


def test_api_exposes_only_fixed_operations_and_rejects_input_pid_or_command(tmp_path):
    value, _ = controller(tmp_path)
    app = LocalWatcherControlApp(value)
    status, payload = call_app(app, "/api/local-control/status")
    assert status == "200 OK" and payload["service"] == "LOCAL_CONTROL_ONLY"
    status, payload = call_app(app, "/api/local-control/readiness", method="POST", csrf=CSRF_TOKEN)
    assert status == "200 OK" and payload["readiness"]["ready"] is True
    status, _ = call_app(app, "/api/local-control/run-command", method="POST", csrf=CSRF_TOKEN)
    assert status in {"403 Forbidden", "404 Not Found"}
    status, payload = call_app(app, "/api/local-control/stop", method="POST", csrf=CSRF_TOKEN, body=b'{"pid":1,"command":"python"}')
    assert status == "400 Bad Request" and payload == {"status": "REQUEST_INPUT_REJECTED"}


def test_origin_and_csrf_are_required_and_responses_are_sanitized(tmp_path):
    value, _ = controller(tmp_path, ready=False)
    app = LocalWatcherControlApp(value)
    assert call_app(app, "/api/local-control/status", origin="https://remote.example")[0] == "403 Forbidden"
    assert call_app(app, "/api/local-control/start", method="POST")[0] == "403 Forbidden"
    _, payload = call_app(app, "/api/local-control/readiness", method="POST", csrf=CSRF_TOKEN)
    serialized = json.dumps(payload).casefold()
    assert "private stderr must not be returned" not in serialized
    assert not any(field in payload for field in ("pid", "environment", "stdout", "stderr", "account_id", "credentials"))


def test_watcher_command_and_module_have_no_generic_or_broker_dispatch():
    assert WATCHER_COMMAND[-2:] == (str(Path("scripts/run_morning_live_shadow_watch.py").resolve()), "--production")
    tree = ast.parse(Path("src/local_watcher_control.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not ({"RobinhoodCommissioningAdapter", "call_tool", "place_equity_order", "cancel_equity_order", "flatten"} & names)
    source = Path("src/local_watcher_control.py").read_text(encoding="utf-8")
    assert "shell=False" in source
    assert "args =" not in source and "command = environ" not in source
