"""Restrictive route policy compared with the constructed operator app."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class RouteContract:
    route: str
    response_kind: str
    requires_store: bool
    supports_file_fixture: bool
    supports_sqlite: bool
    auto_refresh_eligible: bool
    route_type: str
    classification: str
    privacy_classification: str = "PUBLIC_OPERATOR_EVIDENCE"
    name: str = ""
    endpoint: str = ""
    expected_status_codes: tuple[str, ...] = ("200 OK", "400 Bad Request", "404 Not Found", "405 Method Not Allowed", "503 Service Unavailable")

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "path": self.route,
            "method": "GET",
            "methods": ["GET"],
            "name": self.name,
            "endpoint": self.endpoint,
            "route_type": self.route_type,
            "classification": self.classification,
            "privacy_classification": self.privacy_classification,
            "response_kind": self.response_kind,
            "requires_store": self.requires_store,
            "supports_file_fixture": self.supports_file_fixture,
            "supports_sqlite": self.supports_sqlite,
            "permits_runtime_write": False,
            "permits_database_write": False,
            "permits_outbound_network": False,
            "exposes_private_data": False,
            "safety_authority": "NONE",
            "auto_refresh_eligible": self.auto_refresh_eligible,
            "expected_status_codes": list(self.expected_status_codes),
        }


# This is policy, not discovery.  It intentionally remains separately authored
# so a permissive application table cannot make itself self-validating.
_BASE = ("/", "/about", "/healthz", "/history", "/timeline", "/incidents", "/imports", "/data-status", "/search", "/compare", "/exports", "/activity", "/blockers", "/freshness", "/coverage", "/views", "/diagnostics", "/trends", "/release")
_API = ("/api/snapshot",) + tuple("/api" + path for path in _BASE if path not in {"/", "/about", "/healthz"})
_VARIANTS = ("/history/{snapshot_id}", "/api/history/{snapshot_id}", "/timeline/{event_id}", "/api/timeline/{event_id}", "/incidents/{incident_id}", "/incidents/{incident_id}/export.json", "/incidents/{incident_id}/export.html", "/api/incidents/{incident_id}", "/api/incidents/{incident_id}/export.json", "/imports/{batch_id}", "/api/imports/{batch_id}", "/exports/{kind}/{id}.json", "/exports/{kind}/{id}.html")
_ALL = tuple(dict.fromkeys(_BASE + _API + _VARIANTS))


def _policy_name(path: str) -> str:
    if path == "/":
        return "operator.dashboard"
    for prefix in ("/api/incidents/", "/incidents/", "/api/history/", "/history/", "/api/timeline/", "/timeline/", "/api/imports/", "/imports/", "/exports/"):
        if path.startswith(prefix):
            child = path[len(prefix):]
            child_name = "operator." + child.replace("/", ".").replace("{", "by_").replace("}", "").replace("-", "_").replace(".", "_")
            return prefix.strip("/").replace("/", "_") + "." + child_name
    value = path.strip("/").replace("/", ".").replace("{", "by_").replace("}", "")
    return "operator." + value.replace("-", "_").replace(".", "_")


def _policy_route_type(path: str) -> str:
    if path in _VARIANTS:
        return "MOUNTED"
    if path == "/healthz":
        return "HEALTH"
    if path == "/exports":
        return "EXPORT"
    return "API" if path.startswith("/api/") else "HTML"


def _policy_classification(path: str) -> str:
    if path == "/healthz":
        return "HEALTH"
    if path.startswith("/api/") or path.endswith(".json"):
        return "API"
    return "HTML"


ROUTES = tuple(
    RouteContract(
        route,
        "JSON_OR_HTML",
        "{" in route or route not in {"/", "/about", "/healthz"},
        route in {"/", "/history", "/timeline"},
        "{" in route or route not in {"/about", "/healthz"},
        route in {"/", "/diagnostics", "/activity", "/blockers", "/freshness", "/coverage", "/incidents", "/history", "/timeline", "/imports", "/data-status", "/search", "/trends"},
        _policy_route_type(route),
        _policy_classification(route),
        name=_policy_name(route),
        endpoint=f"operator_web_app:{_policy_name(route)}" if route not in _VARIANTS else f"operator_web_app:operator_web_app:{_policy_name(route).split('.', 1)[-1]}",
    )
    for route in _ALL
)


def route_contracts() -> tuple[RouteContract, ...]:
    return ROUTES


def construct_operator_application():
    """Construct the real application used for framework-table discovery."""
    from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig

    temporary = TemporaryDirectory(prefix="orb-live-route-discovery-")
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=Path(temporary.name), default_as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc)))
    # Keep the disposable runtime root alive for the caller's inspection.
    app._route_discovery_temporary = temporary
    return app


def discover_application_routes(application=None):
    """Enumerate route objects registered by a constructed operator app.

    This function intentionally does not inspect dispatcher source, the manual
    contract registry, or the runtime path matcher.
    """
    application = construct_operator_application() if application is None else application
    table = getattr(application, "route_table", None)
    if table is None:
        raise ValueError("constructed operator application has no route table")
    table.validate()
    return table.routes


def route_matches(path: str) -> bool:
    """Compatibility helper; route discovery never uses this matcher."""
    return construct_operator_application().route_table.match(path, "GET") is not None


def verify_contracts(application=None):
    rows = tuple(contract.to_dict() for contract in ROUTES)
    if len({row["route"] for row in rows}) != len(rows):
        raise ValueError("operator route policy contains duplicate paths")
    if any(row["method"] != "GET" or row["permits_database_write"] or row["permits_runtime_write"] or row["permits_outbound_network"] or row["exposes_private_data"] or row["safety_authority"] != "NONE" for row in rows):
        raise ValueError("operator route policy contains unsafe declaration")

    actual = discover_application_routes(application)
    actual_paths = [route.path for route in actual]
    if len({route.name for route in actual}) != len(actual):
        raise ValueError("duplicate operator route name")
    path_methods = [(route.path, method) for route in actual for method in route.methods]
    if len(set(path_methods)) != len(path_methods):
        raise ValueError("duplicate operator route path/method")

    expected = {contract.route: contract for contract in ROUTES}
    if set(actual_paths) != set(expected):
        raise ValueError("operator route inventory mismatch")
    for route in actual:
        contract = expected[route.path]
        if route.methods != ("GET",):
            raise ValueError("operator route method mismatch")
        checks = {
            "name": route.name == contract.name,
            "endpoint": route.endpoint == contract.endpoint,
            "route_type": route.route_type == contract.route_type,
            "classification": route.classification == contract.classification,
            "privacy_classification": route.privacy_classification == contract.privacy_classification,
            "response_kind": route.response_kind == contract.response_kind,
            "requires_store": route.requires_store == contract.requires_store,
            "supports_file_fixture": route.supports_file_fixture == contract.supports_file_fixture,
            "supports_sqlite": route.supports_sqlite == contract.supports_sqlite,
            "auto_refresh_eligible": route.auto_refresh_eligible == contract.auto_refresh_eligible,
            "safety_authority": route.safety_authority == "NONE",
            "database_write": route.permits_database_write is False,
            "runtime_write": route.permits_runtime_write is False,
            "outbound_network": route.permits_outbound_network is False,
            "private_data": route.exposes_private_data is False,
        }
        if not all(checks.values()):
            failed = next(key for key, value in checks.items() if not value)
            raise ValueError(f"operator route contract mismatch: {failed}")
    return rows
