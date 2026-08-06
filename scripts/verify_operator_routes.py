"""Verify registered operator routes and their emitted links through the real app."""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.create_operator_demo import create_demo
from src.operator_route_contracts import ROUTES, construct_operator_application, discover_application_routes, verify_contracts
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig


_ALLOWED_STATUSES = {"200 OK", "400 Bad Request", "405 Method Not Allowed", "503 Service Unavailable"}
_HTML_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)


def request(application, link: str) -> tuple[str, dict[str, str], bytes]:
    parsed = urlsplit(link)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        raise RuntimeError(f"generated link is not a relative application URL: {link}")
    captured: dict[str, object] = {}
    environ = {}
    setup_testing_defaults(environ)
    environ.update({"REQUEST_METHOD": "GET", "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query})
    body = b"".join(application(environ, lambda status, headers: captured.update(status=status, headers=headers)))
    status = str(captured.get("status", ""))
    headers = dict(captured.get("headers", []))
    if status.startswith("3") or status not in _ALLOWED_STATUSES:
        raise RuntimeError(f"unexpected generated-link status: {link} -> {status}")
    if re.search(r"[A-Za-z]:\\|/dev/|Traceback|SELECT |sqlite3", body.decode("utf-8", "replace"), re.IGNORECASE):
        raise RuntimeError("generated-link response leaks private or path data")
    return status, headers, body


def _links_from_value(value: object, links: set[str]) -> None:
    if isinstance(value, str):
        if value.startswith("/") and not value.startswith("//"):
            links.add(value)
    elif isinstance(value, dict):
        for child in value.values():
            _links_from_value(child, links)
    elif isinstance(value, list):
        for child in value:
            _links_from_value(child, links)


def collect_generated_links(application) -> tuple[str, ...]:
    """Collect links from actual HTML and JSON catalogue responses."""
    pages = (
        "/",
        "/views",
        "/exports",
        "/history",
        "/timeline",
        "/incidents",
        "/imports",
        "/freshness",
        "/coverage",
        "/diagnostics",
        "/release",
        "/search?q=event",
        "/api/views",
        "/api/exports",
        "/api/history",
        "/api/timeline",
        "/api/incidents",
        "/api/imports",
        "/api/freshness",
        "/api/coverage",
        "/api/search?q=event",
    )
    links: set[str] = set()
    for page in pages:
        status, headers, body = request(application, page)
        if status != "200 OK":
            raise RuntimeError(f"catalogue page was not executable: {page} -> {status}")
        content_type = headers.get("Content-Type", "")
        if "html" in content_type:
            for link in _HTML_HREF.findall(body.decode("utf-8", "replace")):
                link = unescape(link)
                if link.startswith("/") and not link.startswith("//"):
                    links.add(link)
        if "json" in content_type:
            try:
                _links_from_value(json.loads(body.decode("utf-8")), links)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"catalogue response is not JSON: {page}") from exc
    return tuple(sorted(links))


def _route_probe_links(routes, generated_links: tuple[str, ...]) -> dict[str, str]:
    """Select concrete emitted links for each registered route."""
    candidates = set(generated_links)
    for link in tuple(candidates):
        parsed = urlsplit(link)
        path, query = parsed.path, parsed.query
        if path.startswith(("/history/", "/timeline/", "/incidents/", "/imports/")):
            candidates.add("/api" + path + (("?" + query) if query else ""))
        if path.startswith(("/api/history/", "/api/timeline/", "/api/incidents/", "/api/imports/")):
            candidates.add(path.removeprefix("/api") + (("?" + query) if query else ""))
        if path.startswith("/api/incidents/") and "/export." not in path:
            candidates.add(path + "/export.json" + (("?" + query) if query else ""))
            candidates.add(path.removeprefix("/api") + "/export.html" + (("?" + query) if query else ""))
        if path.startswith("/exports/") and path.endswith(".json"):
            candidates.add(path.removesuffix(".json") + ".html" + (("?" + query) if query else ""))
    candidates.update({"/search?q=event", "/api/search?q=event", "/compare?left=missing&right=missing", "/api/compare?left=missing&right=missing"})

    selected: dict[str, str] = {}
    for route in routes:
        for link in sorted(candidates):
            if route.match_params(urlsplit(link).path) is not None:
                selected[route.path] = link
                break
        if route.path not in selected and "{" not in route.path:
            selected[route.path] = route.path
    return selected


def verify_runtime_routes(application) -> dict[str, object]:
    routes = discover_application_routes(application)
    contract_rows = verify_contracts(application)
    generated_links = collect_generated_links(application)
    for link in generated_links:
        status, _, _ = request(application, link)
        if status == "404 Not Found":
            raise RuntimeError(f"generated link is registered but not executable: {link}")

    probes = _route_probe_links(routes, generated_links)
    contract_by_path = {row["route"]: row for row in contract_rows}
    details = []
    for route in routes:
        probe = probes.get(route.path)
        matchable = probe is not None and route.match_params(urlsplit(probe).path) is not None
        executable = False
        status = "NOT_PROBED"
        if matchable:
            status, _, _ = request(application, probe)
            executable = status != "404 Not Found"
        contract = contract_by_path.get(route.path)
        contract_compliant = contract is not None and route.methods == ("GET",) and route.name == contract["name"] and route.endpoint == contract["endpoint"] and route.route_type == contract["route_type"] and route.classification == contract["classification"] and route.privacy_classification == contract["privacy_classification"] and route.safety_authority == "NONE" and not route.permits_database_write and not route.permits_runtime_write and not route.permits_outbound_network and not route.exposes_private_data
        details.append({"route": route.path, "probe": probe, "status": status, "registered": True, "matchable": matchable, "executable": executable, "contract_compliant": contract_compliant})

    counts = {
        "registered": len(routes),
        "matchable": sum(item["matchable"] for item in details),
        "executable": sum(item["executable"] for item in details),
        "contract_compliant": sum(item["contract_compliant"] for item in details),
    }
    if counts["matchable"] != counts["registered"] or counts["executable"] != counts["registered"] or counts["contract_compliant"] != counts["registered"]:
        raise RuntimeError(f"route classification incomplete: {counts}")
    return {"route_counts": counts, "generated_link_count": len(generated_links), "generated_links": list(generated_links), "routes": details}


def build_demo_application(as_of):
    from tempfile import TemporaryDirectory

    temporary = TemporaryDirectory(prefix="orb-live-route-verification-")
    demo = create_demo(Path(temporary.name) / "demo", as_of)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=Path(demo["root"]), database_path=Path(demo["database"]), backend_mode="SQLITE_READ_MODEL", expected_content_digest=demo["expected_content_digest"], default_as_of=as_of))
    app._route_verification_temporary = temporary
    return app, demo


if __name__ == "__main__":
    from datetime import datetime, timezone

    application, _ = build_demo_application(datetime(2026, 8, 5, 16, tzinfo=timezone.utc))
    report = verify_runtime_routes(application)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
