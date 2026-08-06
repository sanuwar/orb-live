"""Deterministic contract metadata for read-only operator routes."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class RouteContract:
    route:str; response_kind:str; requires_store:bool; supports_file_fixture:bool; supports_sqlite:bool; auto_refresh_eligible:bool; expected_status_codes:tuple[str,...]
    def to_dict(self): return {"route":self.route,"method":"GET","response_kind":self.response_kind,"requires_store":self.requires_store,"supports_file_fixture":self.supports_file_fixture,"supports_sqlite":self.supports_sqlite,"permits_runtime_write":False,"permits_database_write":False,"permits_outbound_network":False,"exposes_private_data":False,"safety_authority":"NONE","auto_refresh_eligible":self.auto_refresh_eligible,"expected_status_codes":list(self.expected_status_codes)}
ROUTES=tuple(RouteContract(route,"JSON_OR_HTML",route not in {"/about","/healthz"},route in {"/","/history","/timeline"},route not in {"/about","/healthz"},route in {"/","/diagnostics","/activity","/blockers","/freshness","/coverage","/incidents","/history","/timeline","/imports","/data-status","/search"},("200 OK","503 Service Unavailable")) for route in ("/","/about","/healthz","/history","/timeline","/incidents","/imports","/data-status","/search","/compare","/exports","/activity","/blockers","/freshness","/coverage","/views","/diagnostics","/trends","/release"))
def route_contracts(): return ROUTES
def verify_contracts():
    if len({x.route for x in ROUTES})!=len(ROUTES) or any(x.to_dict()["method"]!="GET" or x.to_dict()["safety_authority"]!="NONE" for x in ROUTES): raise ValueError("operator route contract mismatch")
    return tuple(x.to_dict() for x in ROUTES)
