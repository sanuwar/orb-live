"""Reproducible, disposable read-only operator milestone verification."""
from __future__ import annotations

import gc
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.create_operator_demo import create_demo
from src.operator_app_store import CONTENT_VERIFIED, compute_operator_content_digest, inspect_store_content, read_model_integrity_status
from src.operator_framework import ApplicationRouteTable
from src.operator_route_contracts import construct_operator_application, discover_application_routes, verify_contracts
from src.operator_view_presets import PRESETS, validate_preset_routes
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.read_only_authorization_manifest import checkpoint_implementation_digest


def _request(app, path, query="", method="GET"):
    result = {}
    body = b"".join(app({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query}, lambda status, headers: result.update(status=status, headers=headers))).decode("utf-8")
    if result.get("status") not in {"200 OK", "400 Bad Request", "404 Not Found", "405 Method Not Allowed", "503 Service Unavailable"}:
        raise RuntimeError("unexpected route status")
    if re.search(r"[A-Za-z]:\\|/dev/|Traceback|SELECT |sqlite3", body, re.I):
        raise RuntimeError("privacy leakage")
    return result["status"], body


def _safety_hash(database: Path) -> str:
    fields = ("strategy_enabled", "activation_approvals_present", "approved_protection_state_count", "protection_grounded", "activation_approved", "permits_automated_action", "live_ready", "unattended_ready")
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("SELECT snapshot_id," + ",".join(fields) + " FROM operator_snapshots ORDER BY snapshot_id").fetchall()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()


def _expect_route_failure(application):
    def fail():
        verify_contracts(application)
    try:
        fail()
    except ValueError:
        return
    raise RuntimeError("undocumented route negative control unexpectedly passed")


def _route_negative_controls():
    undocumented = construct_operator_application()
    base = undocumented.route_table.routes[0]
    undocumented.route_table.add(replace(base, path="/undocumented-verifier-get", name="operator.undocumented_verifier_get", endpoint="operator_web_app:operator.undocumented_verifier_get"))
    _expect_route_failure(undocumented)

    prefix = construct_operator_application()
    prefix.route_table.add(replace(prefix.route_table.routes[0], path="/prefix-verifier/{item}", name="operator.prefix_verifier_item"))
    if "/prefix-verifier/{item}" not in {route.path for route in discover_application_routes(prefix)}:
        raise RuntimeError("prefix route was not discovered")

    mounted = construct_operator_application()
    child = ApplicationRouteTable()
    child.add(replace(mounted.route_table.routes[0], path="/{item}", name="operator.mounted_verifier_item"))
    mounted.route_table.mount("/mounted-verifier", child, name_prefix="mounted_verifier", endpoint_prefix="operator_web_app")
    if "/mounted-verifier/{item}" not in {route.path for route in discover_application_routes(mounted)}:
        raise RuntimeError("mounted route was not discovered")

    source = (Path(__file__).resolve().parents[1] / "src/operator_route_contracts.py").read_text(encoding="utf-8")
    app_source = (Path(__file__).resolve().parents[1] / "src/operator_web_app.py").read_text(encoding="utf-8")
    if any(token in source for token in ("ast.parse", "inspect.getsource", "textwrap.dedent", "OperatorWebApp.__call__")):
        raise RuntimeError("source parsing remains route discovery authority")
    if "route_matches" in app_source or "route_table.match(path, method)" not in app_source:
        raise RuntimeError("application route table is not the runtime route gate")


class _AlwaysSuccessParser:
    def __init__(self, application):
        self.route_table = application.route_table
        self.config = application.config

    def __call__(self, environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [b"<html><body>accepted</body></html>"]


def _preset_negative_controls(application):
    from dataclasses import replace
    def expect_failure(action, label):
        try:
            action()
        except ValueError:
            return
        raise RuntimeError(label + " negative control unexpectedly passed")
    expect_failure(lambda: validate_preset_routes(application, (replace(PRESETS[0], target_route="/removed-verifier-route"),)), "missing preset route")
    expect_failure(lambda: validate_preset_routes(application, (replace(PRESETS[0], target_route="/renamed-verifier-route"),)), "renamed preset route")
    expect_failure(lambda: validate_preset_routes(_AlwaysSuccessParser(application)), "parser drift")


def main():
    as_of = datetime(2026, 8, 5, 16, tzinfo=timezone.utc)
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="orb-live-m12-") as temporary:
        demo = create_demo(Path(temporary) / "demo", as_of)
        database = Path(demo["database"])
        app = OperatorWebApp(OperatorWebAppConfig(runtime_root=Path(demo["root"]), database_path=database, backend_mode="SQLITE_READ_MODEL", expected_content_digest=demo["expected_content_digest"], default_as_of=as_of))
        routes = discover_application_routes(app)
        if len(routes) != 48:
            raise RuntimeError("framework-derived route count changed")
        rows = verify_contracts(app)
        _route_negative_controls()
        _preset_negative_controls(app)
        before_digest = compute_operator_content_digest(database)
        before_counts = inspect_store_content(database)
        before_safety_hash = _safety_hash(database)
        if read_model_integrity_status(database, expected_content_digest=demo["expected_content_digest"]) != CONTENT_VERIFIED:
            raise RuntimeError("independent content anchor is not verified")
        paths = [route.path for route in routes if "{" not in route.path and route.path != "/api/snapshot"]
        statuses = {path: _request(app, path)[0] for path in paths}
        if _request(app, "/api/trends", "metric=active_incidents&bucket=HOUR")[0] != "400 Bad Request":
            raise RuntimeError("unsupported active incident history did not fail closed")
        for metric in ("blocker_occurrences", "event_count", "stale_evidence", "missing_evidence", "import_count"):
            for bucket in ("HOUR", "DAY"):
                _request(app, "/api/trends", f"metric={metric}&bucket={bucket}")
        preset_results = validate_preset_routes(app)
        after_digest = compute_operator_content_digest(database)
        after_counts = inspect_store_content(database)
        after_safety_hash = _safety_hash(database)
        if before_digest != after_digest or before_counts != after_counts or before_safety_hash != after_safety_hash:
            raise RuntimeError("GET mutated read model or safety fields")
        if not all(row["method"] == "GET" and not row["permits_database_write"] and not row["permits_runtime_write"] and not row["permits_outbound_network"] and not row["exposes_private_data"] and row["safety_authority"] == "NONE" for row in rows):
            raise RuntimeError("route safety contract mismatch")
        app_source = (repository / "src/operator_web_app.py").read_text(encoding="utf-8")
        if any(token in app_source for token in ("requests", "httpx", "socket", "robin_stocks", "INSERT ", "UPDATE ", "DELETE ")):
            raise RuntimeError("operator app has a forbidden mutation or network surface")
        report = {"status":"PASS","demo":"deterministic synthetic SQLite read model","route_discovery":"CONSTRUCTED_APPLICATION_ROUTE_TABLE","route_count":len(routes),"contract_route_count":len(rows),"route_statuses":statuses,"preset_count":len(preset_results),"preset_validation":"ALL_CURRENT_PRESETS_ACTUAL_ROUTES_AND_PARSERS","negative_controls":{"undocumented_route":"PASS","prefix_route":"PASS","mounted_route":"PASS","missing_preset_route":"PASS","parser_drift":"PASS"},"trend_metrics":5,"trend_bucket_modes":["HOUR","DAY"],"before_database_content_digest":before_digest,"after_database_content_digest":after_digest,"independent_anchor_status":CONTENT_VERIFIED,"before_row_counts":before_counts,"after_row_counts":after_counts,"before_safety_hash":before_safety_hash,"after_safety_hash":after_safety_hash,"implementation_digest":checkpoint_implementation_digest(repository),"implementation_digest_scope":"SOURCE_CHECKPOINT_CLOSURE","read_model_authority":"NON_AUTHORITATIVE","safety":{"strategy_enabled":False,"activation_approved":False,"permits_automated_action":False,"live_ready":False,"unattended_ready":False},"no_broker":True,"no_outbound_network":True,"no_private_data":True}
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
