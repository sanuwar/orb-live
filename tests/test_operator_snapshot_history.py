from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from src.operator_snapshot_history import HISTORY_DETAIL_KEYS, HISTORY_LIST_KEYS, HistoryError, HistoryNotFound, SnapshotHistory, compare_snapshots
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.supervised_operator_console import RESULT_KEYS, load_console_snapshot
from test_supervised_operator_console import AS_OF, _fixture


def _request(app, path="/", method="GET"):
    environ = {}
    setup_testing_defaults(environ)
    request_path, _, query = path.partition("?")
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": request_path, "QUERY_STRING": query})
    status, headers = [], []
    body = b"".join(app(environ, lambda value, fields: (status.append(value), headers.extend(fields))))
    return status[0], dict(headers), body


def _history_fixture(tmp_path: Path, count: int = 3) -> tuple[Path, list[str]]:
    root = _fixture(tmp_path / "runtime")
    history = root / "history"
    history.mkdir()
    ids = []
    for index in range(count):
        as_of = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc) - timedelta(days=index)
        evidence = root / "evidence/operator-console-evidence.json"
        raw = json.loads(evidence.read_text())
        raw["evidence_as_of"] = as_of.replace(hour=15).isoformat().replace("+00:00", "Z")
        if index == 1:
            raw["operational_status"] = "BLOCKED"
            raw["blocker_reasons"] = ["ACTIVE_SUSPENSION"]
        if index == 2:
            raw["operational_status"] = "OFFLINE_HEALTHY"
            raw["heartbeat_status"] = "STALE"
            raw["blocker_reasons"] = ["STALE_HEARTBEAT"]
        evidence.write_text(json.dumps(raw))
        result = load_console_snapshot(runtime_root=root, as_of=as_of)
        content = result.serialize().encode()
        digest = hashlib.sha256(content).hexdigest()
        (history / f"snapshot-{digest}.json").write_bytes(content)
        ids.append("snapshot-" + digest)
    return root, ids


def test_history_lists_newest_first_and_filters(tmp_path):
    root, ids = _history_fixture(tmp_path)
    history = SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    items, total = history.query(as_of=AS_OF)
    assert total == 3 and [item.snapshot_id for item in items] == ids
    items, total = history.query(as_of=AS_OF, status="BLOCKED")
    assert total == 1 and items[0].snapshot_id == ids[1]
    items, total = history.query(as_of=AS_OF, blocker="STALE_HEARTBEAT")
    assert total == 1 and items[0].snapshot_id == ids[2]
    items, total = history.query(as_of=AS_OF, from_value="2026-08-04T00:00:00Z", to_value="2026-08-05T23:00:00Z")
    assert total == 2


def test_history_pagination_and_validation(tmp_path):
    root, _ = _history_fixture(tmp_path, count=12)
    history = SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    page, total = history.query(as_of=AS_OF, page=2, page_size=10)
    assert len(page) == 2 and total == 12
    with pytest.raises(HistoryError):
        history.query(as_of=AS_OF, status="arbitrary")
    with pytest.raises(HistoryError):
        history.query(as_of=AS_OF, from_value="2026-08-05T00:00:00")
    with pytest.raises(HistoryError):
        history.query(as_of=AS_OF, page_size=1)


def test_history_api_and_detail_are_fixed_schema(tmp_path):
    root, ids = _history_fixture(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    status, _, body = _request(app, "/api/history?page_size=10")
    payload = json.loads(body)
    assert status == "200 OK" and set(payload) == HISTORY_LIST_KEYS and len(payload["items"]) == 3
    status, _, body = _request(app, f"/api/history/{ids[0]}")
    detail = json.loads(body)
    assert status == "200 OK" and set(detail) == HISTORY_DETAIL_KEYS and set(detail["snapshot"]) == RESULT_KEYS
    assert "causality" not in json.dumps(detail).casefold()


def test_history_pages_and_comparison_show_adverse_changes(tmp_path):
    root, ids = _history_fixture(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    assert _request(app, "/history")[0] == "200 OK"
    status, _, body = _request(app, f"/history/{ids[1]}")
    text = body.decode()
    assert status == "200 OK" and "Comparison with current snapshot" in text and "Blockers Added" in text


def test_invalid_history_files_fail_closed(tmp_path):
    root, ids = _history_fixture(tmp_path)
    history = root / "history"
    (history / f"snapshot-{'0' * 64}.json").write_text("{}")
    with pytest.raises(HistoryError):
        SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]).load(as_of=AS_OF)


def test_duplicate_or_oversized_or_excessive_history_fails(tmp_path):
    root, ids = _history_fixture(tmp_path)
    history = root / "history"
    (history / f"snapshot-{'1' * 64}.json").write_bytes(b"x" * 300_000)
    with pytest.raises(HistoryError):
        SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]).load(as_of=AS_OF)
    root, _ = _history_fixture(tmp_path / "many")
    history = root / "history"
    for index in range(101):
        (history / f"snapshot-{index:064x}.json").write_text("bad")
    with pytest.raises(HistoryError):
        SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]).load(as_of=AS_OF)


def test_missing_and_unsafe_history_are_not_exposed(tmp_path):
    root = _fixture(tmp_path / "runtime")
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    assert _request(app, "/api/history/snapshot-0000000000000000000000000000000000000000000000000000000000000000")[0] == "404 Not Found"
    assert _request(app, "/api/history/..%2Fsecret")[0] == "404 Not Found"
    with pytest.raises(HistoryError):
        SnapshotHistory(runtime_root=Path(__file__).resolve().parents[1], repository_root=Path(__file__).resolve().parents[1]).load(as_of=AS_OF)


def test_history_requests_are_read_only_and_methods_are_rejected(tmp_path):
    root, _ = _history_fixture(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    _request(app, "/api/history")
    _request(app, "/history")
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _request(app, "/history", method=method)[0] == "405 Method Not Allowed"


def test_comparison_reports_changes_without_causality(tmp_path):
    root, ids = _history_fixture(tmp_path)
    history = SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    old = history.get(snapshot_id=ids[1], as_of=AS_OF).snapshot
    current = history.get(snapshot_id=ids[0], as_of=AS_OF).snapshot
    comparison = compare_snapshots(current, old)
    assert comparison["blockers_added"] == [] and comparison["blockers_removed"] == ["ACTIVE_SUSPENSION"]
    assert all("cause" not in key.casefold() for key in comparison)
