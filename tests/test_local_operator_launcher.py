from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.local_operator_launcher import (
    CONTROL_COMMAND,
    FRONTEND_COMMAND,
    FRONTEND_URL,
    LauncherError,
    launch_operator_console,
)


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def sequential_probe(values):
    iterator = iter(values)
    return lambda: next(iterator, values[-1])


def test_fixed_commands_and_browser_target_are_not_user_configurable():
    assert Path(CONTROL_COMMAND[-1]).name == "run_local_watcher_control.py"
    assert FRONTEND_COMMAND == ("npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort")
    assert FRONTEND_URL == "http://127.0.0.1:5173/#/operations"
    source = Path("src/local_operator_launcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not ({"kill", "terminate", "RobinhoodCommissioningAdapter", "place_equity_order", "call_tool"} & names)
    assert "shell=False" in source


def test_existing_compatible_services_are_reused_without_starting_or_stopping():
    calls = []
    result = launch_operator_console(
        process_factory=lambda command, cwd: calls.append((command, cwd)),
        control_probe=lambda: True,
        frontend_probe=lambda: True,
        browser_opener=lambda url: url == FRONTEND_URL,
    )
    assert calls == []
    assert result.control_service == result.frontend == "REUSED"


def test_starts_only_fixed_control_then_fixed_frontend_and_waits_for_each():
    calls = []
    control = sequential_probe([False, True])
    frontend = sequential_probe([False, False, True])
    result = launch_operator_console(
        process_factory=lambda command, cwd: (calls.append((command, cwd)) or FakeProcess()),
        control_probe=control,
        frontend_probe=frontend,
        browser_opener=lambda _url: True,
        sleep=lambda _seconds: None,
    )
    assert [command for command, _cwd in calls] == [CONTROL_COMMAND, FRONTEND_COMMAND]
    assert result.control_service == result.frontend == "STARTED"


def test_startup_failure_is_clear_and_never_kills_unknown_processes():
    with pytest.raises(LauncherError, match="exited"):
        launch_operator_console(
            process_factory=lambda _command, _cwd: FakeProcess(returncode=1),
            control_probe=lambda: False,
            frontend_probe=lambda: False,
            browser_opener=lambda _url: True,
            sleep=lambda _seconds: None,
        )


def test_browser_failure_is_reported_after_fixed_services_are_ready():
    with pytest.raises(LauncherError, match="browser"):
        launch_operator_console(
            control_probe=lambda: True,
            frontend_probe=lambda: True,
            browser_opener=lambda _url: False,
        )
