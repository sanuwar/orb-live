"""Contract-driven inventory for every public read-only dispatcher variant."""
from __future__ import annotations
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
    if not all(route_matches(x.route) for x in ROUTES): raise ValueError("operator route discovery mismatch")
    return rows
