"""Fixed local launcher for the ORB-LIVE operator console."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from src.local_watcher_control import CONTROL_HOST, CONTROL_PORT


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
CONTROL_STATUS_URL = f"http://{CONTROL_HOST}:{CONTROL_PORT}/api/local-control/status"
FRONTEND_URL = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/#/operations"
CONTROL_COMMAND = (
    sys.executable,
    os.fspath(REPOSITORY_ROOT / "scripts" / "run_local_watcher_control.py"),
)
FRONTEND_COMMAND = ("npm", "run", "dev", "--", "--host", FRONTEND_HOST, "--port", str(FRONTEND_PORT), "--strictPort")


class LauncherError(RuntimeError):
    """A fixed local operator process did not become safely available."""


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class LauncherResult:
    control_service: str
    frontend: str
    browser_url: str


def _request_json(url: str) -> Mapping[str, Any] | None:
    request = urllib.request.Request(url, headers={"Origin": f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"})
    try:
        with urllib.request.urlopen(request, timeout=1) as response:  # nosec B310: fixed loopback URL
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _control_service_is_compatible() -> bool:
    payload = _request_json(CONTROL_STATUS_URL)
    return payload is not None and payload.get("service") == "LOCAL_CONTROL_ONLY"


def _frontend_is_available() -> bool:
    try:
        with urllib.request.urlopen(f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/", timeout=1) as response:  # nosec B310: fixed loopback URL
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _start_fixed_process(command: tuple[str, ...], *, cwd: Path) -> ProcessHandle:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=flags,
    )


def _wait_for(
    probe: Callable[[], bool],
    process: ProcessHandle | None,
    *,
    sleep: Callable[[float], None],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if probe():
            return
        if process is not None and process.poll() is not None:
            raise LauncherError("fixed local process exited before it became available")
        sleep(0.1)
    raise LauncherError("fixed local process did not become available before timeout")


def launch_operator_console(
    *,
    process_factory: Callable[[tuple[str, ...], Path], ProcessHandle] | None = None,
    control_probe: Callable[[], bool] = _control_service_is_compatible,
    frontend_probe: Callable[[], bool] = _frontend_is_available,
    browser_opener: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 15,
) -> LauncherResult:
    """Start only fixed loopback services, then open the Operations page.

    Existing compatible services are reused. Unknown listeners are never killed
    or replaced; a listener on the control port that does not identify as the
    fixed local-control service fails closed.
    """
    factory = process_factory or (lambda command, cwd: _start_fixed_process(command, cwd=cwd))
    if control_probe():
        control_state = "REUSED"
    else:
        control_process = factory(CONTROL_COMMAND, REPOSITORY_ROOT)
        _wait_for(control_probe, control_process, sleep=sleep, timeout_seconds=timeout_seconds)
        control_state = "STARTED"

    if frontend_probe():
        frontend_state = "REUSED"
    else:
        frontend_process = factory(FRONTEND_COMMAND, REPOSITORY_ROOT / "site")
        _wait_for(frontend_probe, frontend_process, sleep=sleep, timeout_seconds=timeout_seconds)
        frontend_state = "STARTED"

    if not browser_opener(FRONTEND_URL):
        raise LauncherError("browser could not be opened; services remain available at the fixed Operations URL")
    return LauncherResult(control_state, frontend_state, FRONTEND_URL)


__all__ = [
    "CONTROL_COMMAND", "CONTROL_STATUS_URL", "FRONTEND_COMMAND", "FRONTEND_HOST",
    "FRONTEND_PORT", "FRONTEND_URL", "LauncherError", "LauncherResult",
    "launch_operator_console",
]
