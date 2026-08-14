"""Fixed local read-only inputs for the remote monitoring allowlist."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from src.remote_monitoring_projection import RemoteProjectionError, build_remote_projection
from src.runtime_journal_projection import DEFAULT_HISTORY_PATH, RuntimeProjectionError, read_history


LOCAL_STATUS_URL = "http://127.0.0.1:8766/api/local-control/status"
LOCAL_STATUS_ORIGIN = "http://127.0.0.1:5173"


def read_local_watcher_status() -> Mapping[str, Any] | None:
    """Read one fixed sanitized GET. No method, URL, body, or headers are caller-controlled."""
    request = Request(LOCAL_STATUS_URL, method="GET", headers={"Origin": LOCAL_STATUS_ORIGIN})
    try:
        with urlopen(request, timeout=1.0) as response:  # noqa: S310 - fixed loopback allowlist above
            if response.status != 200:
                return None
            value = json.loads(response.read(65537))
    except (OSError, ValueError):
        return None
    if not isinstance(value, Mapping) or value.get("service") != "LOCAL_CONTROL_ONLY":
        return None
    return value


def collect_remote_projection(
    *,
    history_path: Path = DEFAULT_HISTORY_PATH,
    status_reader: Callable[[], Mapping[str, Any] | None] = read_local_watcher_status,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    try:
        history = read_history(history_path)
        watcher = status_reader()
        return build_remote_projection(history, generated_at=clock(), watcher_status=watcher)
    except (OSError, RuntimeProjectionError, RemoteProjectionError) as exc:
        raise RemoteProjectionError("local sanitized projection is unavailable") from exc


__all__ = ["LOCAL_STATUS_ORIGIN", "LOCAL_STATUS_URL", "collect_remote_projection", "read_local_watcher_status"]
