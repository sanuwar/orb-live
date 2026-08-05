"""Bounded, read-only history of serialized operator-console snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.offline_path_safety import is_repository_data_path
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.supervised_operator_console import ConsoleError, SupervisedOperatorConsoleResult


SNAPSHOT_DIRECTORY_NAME = "history"
SNAPSHOT_PATTERN = re.compile(r"^snapshot-([0-9a-f]{64})\.json$")
MAX_SNAPSHOT_FILES = 100
MAX_SNAPSHOT_BYTES = 256 * 1024
HISTORY_SCHEMA_VERSION = 1
ALLOWED_STATUS_FILTERS = frozenset({"OFFLINE_HEALTHY", "CONFIGURATION_REVIEW_REQUIRED", "SUSPENDED", "BLOCKED", "REVIEW_REQUIRED", "UNKNOWN"})
ALLOWED_BLOCKER_FILTERS = frozenset({"ACTIVE_SUSPENSION", "STALE_HEARTBEAT", "UNCONFIRMED_ALERT", "AMBIGUOUS_RECOVERY", "MISSING_COMPONENT", "CONFLICTING_EVIDENCE", "OPERATOR_REVIEW_REQUIRED"})
HISTORY_LIST_KEYS = frozenset({"schema_version", "page", "page_size", "total_count", "items"})
HISTORY_DETAIL_KEYS = frozenset({"schema_version", "snapshot_id", "snapshot", "comparison"})
_ID_PREFIX = "snapshot-"


class HistoryError(ValueError):
    """The bounded snapshot history cannot be safely loaded or queried."""


class HistoryNotFound(HistoryError):
    """The requested valid-looking snapshot is not present."""


@dataclass(frozen=True)
class HistoryItem:
    snapshot_id: str
    snapshot: SupervisedOperatorConsoleResult

    def to_dict(self) -> dict[str, Any]:
        data = self.snapshot.to_dict()
        return {
            "snapshot_id": self.snapshot_id, "generated_as_of": data["generated_as_of"],
            "release_commit_sha": data["release_commit_sha"][:12], "operational_status": data["operational_status"],
            "supervised_readiness_status": data["supervised_readiness_status"], "recommended_operator_action": data["recommended_operator_action"],
            "evidence_freshness": data["evidence_freshness"], "evidence_completeness": data["evidence_completeness"],
            "blocker_reasons": data["blocker_reasons"], "suspension_status": data["suspension_status"],
            "heartbeat_status": data["heartbeat_status"], "alert_status": data["alert_status"], "recovery_status": data["recovery_status"],
            "permits_automated_action": False, "live_ready": False, "unattended_ready": False,
        }


def _moment(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HistoryError("timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoryError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _safe_history_root(runtime_root: Path) -> Path:
    lexical = Path(runtime_root) / SNAPSHOT_DIRECTORY_NAME
    if lexical.is_symlink():
        raise HistoryError("history directory is unsafe")
    try:
        root = (Path(runtime_root) / SNAPSHOT_DIRECTORY_NAME).resolve(strict=True)
    except OSError as exc:
        raise HistoryError("history directory is unavailable") from exc
    if not root.is_dir() or is_repository_data_path(root):
        raise HistoryError("history directory is unsafe")
    if any(part.casefold() in {"src", "tests", "test", "examples"} for part in root.parts):
        raise HistoryError("history directory is unsafe")
    return root


def _load_file(path: Path, *, as_of: datetime, repository_root: Path) -> HistoryItem:
    if path.is_symlink() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise HistoryError("snapshot file is unsafe")
    content = path.read_bytes()
    match = SNAPSHOT_PATTERN.fullmatch(path.name)
    if match is None or hashlib.sha256(content).hexdigest() != match.group(1):
        raise HistoryError("snapshot identity is invalid")
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HistoryError("snapshot is malformed") from exc
    snapshot = SupervisedOperatorConsoleResult.from_dict(raw)
    generated = _moment(snapshot.generated_as_of)
    if generated > as_of.astimezone(timezone.utc) or snapshot.evidence_freshness != "CURRENT":
        raise HistoryError("snapshot is future-dated or stale")
    if snapshot.release_commit_sha != resolve_head_sha(repository_root) or snapshot.implementation_digest != checkpoint_implementation_digest(repository_root):
        raise HistoryError("snapshot release identity conflicts")
    return HistoryItem(snapshot_id=_ID_PREFIX + match.group(1), snapshot=snapshot)


class SnapshotHistory:
    """Build a bounded deterministic index from one fixed directory per request."""

    def __init__(self, *, runtime_root: Path, repository_root: Path, max_files: int = MAX_SNAPSHOT_FILES):
        self.runtime_root = Path(runtime_root)
        self.repository_root = Path(repository_root)
        self.max_files = max_files

    def load(self, *, as_of: datetime) -> tuple[HistoryItem, ...]:
        root = _safe_history_root(self.runtime_root)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise HistoryError("as-of must be timezone-aware")
        candidates = sorted(root.glob("snapshot-*.json"), key=lambda path: path.name)
        if len(candidates) > self.max_files:
            raise HistoryError("history file count exceeds limit")
        items = [_load_file(path, as_of=as_of, repository_root=self.repository_root) for path in candidates]
        ids = [item.snapshot_id for item in items]
        if len(ids) != len(set(ids)):
            raise HistoryError("duplicate snapshot identity")
        return tuple(sorted(items, key=lambda item: (item.snapshot.generated_as_of, item.snapshot_id), reverse=True))

    @staticmethod
    def _validate_filter(value: str | None, allowed: frozenset[str], field: str) -> str | None:
        if value is None:
            return None
        if value not in allowed:
            raise HistoryError(f"{field} filter is invalid")
        return value

    def query(self, *, as_of: datetime, status: str | None = None, blocker: str | None = None, from_value: str | None = None, to_value: str | None = None, page: int = 1, page_size: int = 25) -> tuple[tuple[HistoryItem, ...], int]:
        if type(page) is not int or page < 1 or type(page_size) is not int or not 10 <= page_size <= 100:
            raise HistoryError("pagination is invalid")
        status = self._validate_filter(status, ALLOWED_STATUS_FILTERS, "status")
        blocker = self._validate_filter(blocker, ALLOWED_BLOCKER_FILTERS, "blocker")
        lower = _moment(from_value) if from_value is not None else None
        upper = _moment(to_value) if to_value is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise HistoryError("time range is invalid")
        items = list(self.load(as_of=as_of))
        filtered = []
        for item in items:
            data = item.to_dict()
            moment = _moment(data["generated_as_of"])
            if status is not None and data["operational_status"] != status and data["supervised_readiness_status"] != status:
                continue
            if blocker is not None and blocker not in data["blocker_reasons"]:
                continue
            if lower is not None and moment < lower:
                continue
            if upper is not None and moment > upper:
                continue
            filtered.append(item)
        start = (page - 1) * page_size
        return tuple(filtered[start:start + page_size]), len(filtered)

    def get(self, *, snapshot_id: str, as_of: datetime) -> HistoryItem:
        if SNAPSHOT_PATTERN.fullmatch(snapshot_id + ".json") is None:
            raise HistoryNotFound("snapshot id is invalid")
        try:
            _safe_history_root(self.runtime_root)
        except HistoryError as exc:
            if "unavailable" in str(exc):
                raise HistoryNotFound("snapshot is not present") from None
            raise
        for item in self.load(as_of=as_of):
            if item.snapshot_id == snapshot_id:
                return item
        raise HistoryNotFound("snapshot is not present")


def compare_snapshots(current: SupervisedOperatorConsoleResult, historical: SupervisedOperatorConsoleResult) -> dict[str, Any]:
    current_data, old_data = current.to_dict(), historical.to_dict()
    return {
        "status_changed": current_data["operational_status"] != old_data["operational_status"],
        "blockers_added": sorted(set(current_data["blocker_reasons"]) - set(old_data["blocker_reasons"])),
        "blockers_removed": sorted(set(old_data["blocker_reasons"]) - set(current_data["blocker_reasons"])),
        "heartbeat_changed": current_data["heartbeat_status"] != old_data["heartbeat_status"],
        "suspension_changed": current_data["suspension_status"] != old_data["suspension_status"],
        "alert_changed": current_data["alert_status"] != old_data["alert_status"],
        "recovery_changed": current_data["recovery_status"] != old_data["recovery_status"],
    }


__all__ = ["ALLOWED_BLOCKER_FILTERS", "ALLOWED_STATUS_FILTERS", "HISTORY_DETAIL_KEYS", "HISTORY_LIST_KEYS", "HistoryError", "HistoryItem", "HistoryNotFound", "MAX_SNAPSHOT_BYTES", "MAX_SNAPSHOT_FILES", "SnapshotHistory", "compare_snapshots"]
