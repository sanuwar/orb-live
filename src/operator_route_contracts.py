"""Separate route policy from the inventory accepted by the WSGI application."""
from __future__ import annotations
import ast
import inspect
import re
import textwrap
from dataclasses import dataclass

@dataclass(frozen=True)
class RouteContract:
    route:str; response_kind:str; requires_store:bool; supports_file_fixture:bool; supports_sqlite:bool; auto_refresh_eligible:bool; expected_status_codes:tuple[str,...]
    def to_dict(self): return {"route":self.route,"method":"GET","response_kind":self.response_kind,"requires_store":self.requires_store,"supports_file_fixture":self.supports_file_fixture,"supports_sqlite":self.supports_sqlite,"permits_runtime_write":False,"permits_database_write":False,"permits_outbound_network":False,"exposes_private_data":False,"safety_authority":"NONE","auto_refresh_eligible":self.auto_refresh_eligible,"expected_status_codes":list(self.expected_status_codes)}

_BASE=("/","/about","/healthz","/history","/timeline","/incidents","/imports","/data-status","/search","/compare","/exports","/activity","/blockers","/freshness","/coverage","/views","/diagnostics","/trends","/release")
_API=("/api/snapshot",)+tuple("/api"+x for x in _BASE if x not in {"/","/about","/healthz"})
_VARIANTS=("/history/{snapshot_id}","/api/history/{snapshot_id}","/timeline/{event_id}","/api/timeline/{event_id}","/incidents/{incident_id}","/api/incidents/{incident_id}","/incidents/{incident_id}/export.html","/api/incidents/{incident_id}/export.json","/imports/{batch_id}","/api/imports/{batch_id}","/exports/{kind}/{id}.json","/exports/{kind}/{id}.html")
_ALL=tuple(dict.fromkeys(_BASE+_API+_VARIANTS))
ROUTES=tuple(RouteContract(route,"JSON_OR_HTML", "{" in route or route not in {"/","/about","/healthz"}, route in {"/","/history","/timeline"}, "{" in route or route not in {"/about","/healthz"}, route in {"/","/diagnostics","/activity","/blockers","/freshness","/coverage","/incidents","/history","/timeline","/imports","/data-status","/search","/trends"}, ("200 OK","400 Bad Request","404 Not Found","405 Method Not Allowed","503 Service Unavailable")) for route in _ALL)

def route_contracts(): return ROUTES

def discover_application_routes(dispatcher=None):
    """Discover path patterns from the constructed application's dispatcher.

    This intentionally does not import or call ``route_matches``.  The local
    application is WSGI rather than a framework with a route table, so its
    dispatcher comparisons are the runtime route definition.
    """
    if dispatcher is None:
        from src.operator_web_app import OperatorWebApp
        dispatcher=OperatorWebApp.__call__
    tree=ast.parse(textwrap.dedent(inspect.getsource(dispatcher)))
    found=set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not node.ops: continue
        left=node.left
        if not (isinstance(left, ast.Name) and left.id=="path"): continue
        if isinstance(node.ops[0], (ast.In, ast.NotIn)) and isinstance(node.comparators[0], (ast.Set, ast.Tuple)):
            found.update(x.value for x in node.comparators[0].elts if isinstance(x, ast.Constant) and isinstance(x.value,str) and x.value.startswith("/"))
        elif isinstance(node.ops[0], (ast.Eq, ast.NotEq)) and isinstance(node.comparators[0], ast.Constant) and isinstance(node.comparators[0].value,str) and node.comparators[0].value.startswith("/"):
            found.add(node.comparators[0].value)
    for prefix, suffixes in (("/history/", ("{snapshot_id}",)), ("/api/history/", ("{snapshot_id}",)), ("/timeline/", ("{event_id}",)), ("/api/timeline/", ("{event_id}",)), ("/incidents/", ("{incident_id}", "{incident_id}/export.html")), ("/api/incidents/", ("{incident_id}", "{incident_id}/export.json")), ("/imports/", ("{batch_id}",)), ("/api/imports/", ("{batch_id}",)), ("/exports/", ("{kind}/{id}.json", "{kind}/{id}.html"))):
        if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr=="startswith" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value==prefix for node in ast.walk(tree)):
            found.update(prefix+suffix for suffix in suffixes)
    return tuple(sorted(found))

def route_matches(path:str)->bool:
    if path in _ALL: return True
    parts=path.strip("/").split("/")
    if len(parts)==2 and parts[0] in {"history","timeline","incidents","imports"}:
        return bool(parts[1])
    if len(parts)==3 and parts[0] == "api" and parts[1] in {"history","timeline","incidents","imports"}:
        return bool(parts[2])
    if len(parts)==3 and parts[0] == "incidents" and parts[2] in {"export.json","export.html"}:
        return bool(parts[1])
    if len(parts)==4 and parts[:2] == ["api","incidents"] and parts[3] in {"export.json","export.html"}:
        return bool(parts[2])
    return False

def verify_contracts():
    rows=tuple(x.to_dict() for x in ROUTES)
    if len({x["route"] for x in rows})!=len(rows) or any(x["method"]!="GET" or x["permits_database_write"] or x["permits_outbound_network"] or x["exposes_private_data"] or x["safety_authority"]!="NONE" for x in rows): raise ValueError("operator route contract mismatch")
    actual=discover_application_routes()
    if set(actual)!=set(x.route for x in ROUTES): raise ValueError("operator route inventory mismatch")
    return rows
