"""Loopback-only, allowlisted control for the canonical morning watcher."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from scripts.check_morning_watch_readiness import (
    AUTHORITY_CEILING,
    MorningReadiness,
    assess_morning_readiness,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 8766
CSRF_HEADER = "HTTP_X_ORB_CSRF"
CSRF_TOKEN = "local-control-v1"
DEFAULT_ALLOWED_ORIGINS = frozenset({"http://127.0.0.1:5173", "http://localhost:5173"})
WATCHER_COMMAND = (
    sys.executable,
    os.fspath(REPOSITORY_ROOT / "scripts" / "run_morning_live_shadow_watch.py"),
    "--production",
)
DEFAULT_OWNERSHIP_MARKER = Path(tempfile.gettempdir()) / "orb-live-morning-watcher-control-v1.json"
_SAFE_CODE = re.compile(r"^[A-Z0-9_:-]{1,96}$")


class LocalControlError(RuntimeError):
    """A fixed local-control operation could not be completed safely."""


class ProcessHandle(Protocol):
    returncode: int | None

    def poll(self) -> int | None: ...
    def communicate(self) -> tuple[str, str]: ...
    def send_signal(self, signal_number: int) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _moment(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_codes(values: tuple[str, ...]) -> list[str]:
    return [value if _SAFE_CODE.fullmatch(value) else "UNAVAILABLE_BLOCKER" for value in values]


def _readiness_projection(value: MorningReadiness) -> dict[str, Any]:
    return {
        "status": "READY_FOR_MORNING_WATCH" if value.ready else "BLOCKED_BY_LOCAL_CONFIGURATION",
        "ready": value.ready,
        "blockers": _safe_codes(value.blockers),
        "market_session": value.market_session,
        "market_reason": value.market_reason,
        "source_sha": value.source_sha,
        "implementation_digest": value.implementation_digest,
        "strategy": value.strategy_name,
        "authority": AUTHORITY_CEILING,
        "activation_approved": False,
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
        "broker_write_count": 0,
    }


def _default_process_factory() -> ProcessHandle:
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        WATCHER_COMMAND,
        cwd=REPOSITORY_ROOT,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        creationflags=creation_flags,
    )


@dataclass(frozen=True)
class LocalControlConfig:
    host: str = CONTROL_HOST
    port: int = CONTROL_PORT
    allowed_origins: frozenset[str] = DEFAULT_ALLOWED_ORIGINS

    def validate(self) -> "LocalControlConfig":
        if self.host != CONTROL_HOST:
            raise LocalControlError("local control must bind to 127.0.0.1")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise LocalControlError("local control port is invalid")
        if not self.allowed_origins or any(
            origin not in DEFAULT_ALLOWED_ORIGINS for origin in self.allowed_origins
        ):
            raise LocalControlError("local control origin is invalid")
        return self


class MorningWatcherController:
    """Own at most one fixed watcher child and expose sanitized state only."""

    def __init__(
        self,
        *,
        readiness_provider: Callable[[], MorningReadiness] = assess_morning_readiness,
        process_factory: Callable[[], ProcessHandle] = _default_process_factory,
        clock: Callable[[], datetime] = _utc_now,
        ownership_marker: Path = DEFAULT_OWNERSHIP_MARKER,
        summary_reader: Callable[[str], Mapping[str, str]] | None = None,
    ) -> None:
        self._readiness_provider = readiness_provider
        self._process_factory = process_factory
        self._clock = clock
        self._ownership_marker = Path(ownership_marker)
        self._summary_reader = summary_reader or self._summary_from_stdout
        self._lock = threading.RLock()
        self._process: ProcessHandle | None = None
        self._monitor_thread: threading.Thread | None = None
        self._instance_id = uuid.uuid4().hex
        self._has_started = False
        self._readiness: dict[str, Any] | None = None
        self._state = "OWNERSHIP_UNKNOWN" if self._ownership_marker.exists() else "NOT_RUNNING"
        self._result = "UNKNOWN" if self._state == "OWNERSHIP_UNKNOWN" else "NONE"
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._exit_code: int | None = None
        self._terminal_state: str | None = None
        self._stop_reason: str | None = None
        self._artifact_reference: str | None = None

    def check_readiness(self) -> dict[str, Any]:
        try:
            projected = _readiness_projection(self._readiness_provider())
        except Exception:
            projected = {
                "status": "BLOCKED_BY_LOCAL_CONFIGURATION",
                "ready": False,
                "blockers": ["READINESS_CHECK_FAILED"],
                "market_session": "UNKNOWN",
                "market_reason": "UNKNOWN",
                "source_sha": "UNAVAILABLE",
                "implementation_digest": "UNAVAILABLE",
                "strategy": "UNAVAILABLE",
                "authority": AUTHORITY_CEILING,
                "activation_approved": False,
                "permits_automated_action": False,
                "live_ready": False,
                "unattended_ready": False,
                "broker_write_count": 0,
            }
        with self._lock:
            self._readiness = projected
        return projected

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service": "LOCAL_CONTROL_ONLY",
                "process_state": self._state,
                "watcher_result": self._result,
                "started_at": self._started_at,
                "ended_at": self._ended_at,
                "exit_code": self._exit_code,
                "terminal_state": self._terminal_state,
                "stop_reason": self._stop_reason,
                "artifact_reference": self._artifact_reference,
                "readiness": self._readiness,
                "authority": AUTHORITY_CEILING,
                "activation_approved": False,
                "permits_automated_action": False,
                "live_ready": False,
                "unattended_ready": False,
                "broker_write_count": 0,
            }

    def start(self) -> tuple[bool, dict[str, Any]]:
        readiness = self.check_readiness()
        with self._lock:
            if self._state == "OWNERSHIP_UNKNOWN":
                return False, self.status()
            if self._process is not None and self._process.poll() is None:
                self._state = "RUNNING"
                return False, self.status()
            if self._has_started:
                self._state = "BLOCKED"
                self._result = "WATCH_ALREADY_STARTED_THIS_SERVICE_INSTANCE"
                return False, self.status()
            if not readiness["ready"]:
                self._state = "BLOCKED"
                self._result = "READINESS_BLOCKED"
                return False, self.status()
            try:
                self._ownership_marker.parent.mkdir(parents=True, exist_ok=True)
                self._ownership_marker.write_text(
                    json.dumps({"instance": self._instance_id, "owned": True}),
                    encoding="utf-8",
                )
                process = self._process_factory()
            except (OSError, ValueError):
                try:
                    self._ownership_marker.unlink(missing_ok=True)
                except OSError:
                    pass
                self._state = "FAILED"
                self._result = "WATCHER_LAUNCH_FAILED"
                return False, self.status()
            self._process = process
            self._has_started = True
            self._state = "RUNNING"
            self._result = "RUNNING"
            self._started_at = _moment(self._clock())
            self._ended_at = None
            self._exit_code = None
            monitor = threading.Thread(
                target=self._monitor_process,
                args=(process,),
                name="orb-live-morning-watcher-monitor",
                daemon=True,
            )
            self._monitor_thread = monitor
            monitor.start()
            return True, self.status()

    def stop(self) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False, self.status()
            try:
                interrupt = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
                process.send_signal(interrupt)
            except (OSError, ValueError):
                self._state = "FAILED"
                self._result = "WATCHER_STOP_FAILED"
                return False, self.status()
            self._state = "STOP_REQUESTED"
            self._result = "STOP_REQUESTED"
            return True, self.status()

    def close(self) -> None:
        self.stop()
        monitor = self._monitor_thread
        if monitor is not None:
            monitor.join(timeout=10)

    def _monitor_process(self, process: ProcessHandle) -> None:
        try:
            stdout, _stderr = process.communicate()
        except (OSError, ValueError):
            stdout = ""
        summary = self._summary_reader(stdout)
        with self._lock:
            if process is not self._process:
                return
            self._exit_code = process.returncode
            self._ended_at = _moment(self._clock())
            self._terminal_state = summary.get("final_state")
            self._stop_reason = summary.get("stop_reason")
            self._artifact_reference = summary.get("artifact_reference")
            if self._terminal_state == "WINDOW_COMPLETE_NO_CANDIDATE":
                self._state, self._result = "COMPLETE", "NO_CANDIDATE"
            elif self._terminal_state == "CANDIDATE_CAPTURED":
                self._state, self._result = "COMPLETE", "CANDIDATE"
            elif self._terminal_state == "OPERATOR_STOPPED":
                self._state, self._result = "COMPLETE", "OPERATOR_STOPPED"
            else:
                self._state, self._result = "FAILED", "WATCHER_FAILED"
            try:
                self._ownership_marker.unlink(missing_ok=True)
            except OSError:
                self._state, self._result = "OWNERSHIP_UNKNOWN", "OWNERSHIP_MARKER_UNAVAILABLE"

    def _summary_from_stdout(self, stdout: str) -> dict[str, str]:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return {}
        candidate = Path(lines[-1])
        try:
            resolved = candidate.resolve(strict=True)
            evidence_root = (REPOSITORY_ROOT / "artifacts" / "live-shadow-watch").resolve()
            if evidence_root not in resolved.parents:
                return {}
            raw = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        final_state = raw.get("final_state")
        stop_reason = raw.get("stop_reason")
        if not isinstance(final_state, str) or not _SAFE_CODE.fullmatch(final_state):
            return {}
        if not isinstance(stop_reason, str) or not _SAFE_CODE.fullmatch(stop_reason):
            return {}
        return {
            "final_state": final_state,
            "stop_reason": stop_reason,
            "artifact_reference": resolved.name,
        }


class LocalWatcherControlApp:
    """Small WSGI app exposing four fixed operations and no request payloads."""

    def __init__(self, controller: MorningWatcherController, config: LocalControlConfig = LocalControlConfig()) -> None:
        self.controller = controller
        self.config = config.validate()

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        origin = environ.get("HTTP_ORIGIN")
        if origin not in self.config.allowed_origins:
            return self._response(start_response, "403 Forbidden", {"status": "ORIGIN_REJECTED"})
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", ""))
        if method == "OPTIONS":
            if path not in {
                "/api/local-control/status",
                "/api/local-control/readiness",
                "/api/local-control/start",
                "/api/local-control/stop",
            }:
                return self._response(
                    start_response,
                    "404 Not Found",
                    {"status": "OPERATION_NOT_FOUND"},
                    origin=origin,
                )
            return self._response(start_response, "204 No Content", None, origin=origin)
        if environ.get("QUERY_STRING") or self._has_body(environ):
            return self._response(start_response, "400 Bad Request", {"status": "REQUEST_INPUT_REJECTED"}, origin=origin)
        if method == "GET" and path == "/api/local-control/status":
            return self._response(start_response, "200 OK", self.controller.status(), origin=origin)
        if method == "POST" and environ.get(CSRF_HEADER) == CSRF_TOKEN:
            if path == "/api/local-control/readiness":
                self.controller.check_readiness()
                return self._response(start_response, "200 OK", self.controller.status(), origin=origin)
            if path == "/api/local-control/start":
                changed, payload = self.controller.start()
                return self._response(start_response, "202 Accepted" if changed else "409 Conflict", payload, origin=origin)
            if path == "/api/local-control/stop":
                changed, payload = self.controller.stop()
                return self._response(start_response, "202 Accepted" if changed else "200 OK", payload, origin=origin)
        if method == "POST":
            return self._response(start_response, "403 Forbidden", {"status": "CSRF_REJECTED"}, origin=origin)
        return self._response(start_response, "404 Not Found", {"status": "OPERATION_NOT_FOUND"}, origin=origin)

    @staticmethod
    def _has_body(environ: Mapping[str, Any]) -> bool:
        try:
            return int(environ.get("CONTENT_LENGTH") or 0) != 0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _response(start_response, status: str, payload: Mapping[str, Any] | None, *, origin: str | None = None):
        body = b"" if payload is None else (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
        ]
        if origin is not None:
            headers.extend([
                ("Access-Control-Allow-Origin", origin),
                ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                ("Access-Control-Allow-Headers", "X-ORB-CSRF"),
                ("Vary", "Origin"),
            ])
        start_response(status, headers)
        return [body]


__all__ = [
    "CONTROL_HOST", "CONTROL_PORT", "CSRF_TOKEN", "DEFAULT_ALLOWED_ORIGINS",
    "DEFAULT_OWNERSHIP_MARKER", "LocalControlError", "LocalControlConfig",
    "LocalWatcherControlApp", "MorningWatcherController", "WATCHER_COMMAND",
]
