"""Hardcoded, relative, non-persistent operator view presets."""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit


@dataclass(frozen=True)
class ViewPreset:
    preset_id:str; title:str; description:str; target_route:str; query_values:tuple[tuple[str,str],...]; category:str
    def url(self): return self.target_route + (("?"+urlencode(self.query_values,safe="")) if self.query_values else "")
    def to_dict(self): return {"preset_id":self.preset_id,"title":self.title,"description":self.description,"target_route":self.target_route,"url":self.url(),"query_values":dict(self.query_values),"allowed_query_parameters":[k for k,v in self.query_values],"category":self.category,"read_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}


PRESETS=tuple(ViewPreset(*x) for x in (("current-blockers","Current blockers","Observed blocker evidence","/blockers",(("active_only","true"),),"SAFETY"),("active-incidents","Active incidents","Currently observed incidents","/incidents",(("active_only","true"),),"INCIDENTS"),("stale-evidence","Stale evidence","Evidence marked stale","/freshness",(),"EVIDENCE"),("missing-evidence","Missing evidence","Missing evidence categories","/coverage",(),"EVIDENCE"),("recent-activity","Recent activity","Recent read-model activity","/activity",(),"ACTIVITY"),("recent-imports","Recent imports","Recent import provenance","/imports",(),"PROVENANCE"),("fail-closed-status","Fail-closed status","Read-model status","/data-status",(),"STATUS"),("recent-high-severity-events","Recent high-severity events","Observed high-severity timeline evidence","/timeline",(("severity","HIGH"),),"TIMELINE"),("incomplete-evidence","Incomplete evidence","Evidence completeness requiring review","/history",(("status","INCOMPLETE"),),"EVIDENCE"),("integrity-review","Integrity review","Read-model identity and integrity status","/data-status",(),"STATUS")))

# Independently authored expected normalized state. This is not generated from
# PRESETS or from the application route table.
_EXPECTED_QUERY_STATE = {
    "current-blockers": {"active_only": "true"}, "active-incidents": {"active_only": "true"},
    "stale-evidence": {}, "missing-evidence": {}, "recent-activity": {}, "recent-imports": {},
    "fail-closed-status": {}, "recent-high-severity-events": {"severity": "HIGH"},
    "incomplete-evidence": {"status": "INCOMPLETE"}, "integrity-review": {},
}


def list_presets(): return PRESETS
def get_preset(preset_id):
    for preset in PRESETS:
        if preset.preset_id==preset_id:return preset
    raise KeyError("preset is unknown")


def validate_preset_routes(application, presets=None):
    """Exercise every preset against actual routes and actual request parsers."""
    table = getattr(application, "route_table", None)
    if table is None:
        raise ValueError("preset validation requires a constructed application")
    routes = {route.path: route for route in table.routes}
    selected = tuple(PRESETS if presets is None else presets)
    if presets is None and {preset.preset_id for preset in selected} != set(_EXPECTED_QUERY_STATE):
        raise ValueError("preset catalog is incomplete")
    results=[]
    for preset in selected:
        url=preset.url(); parsed_url=urlsplit(url)
        if parsed_url.scheme or parsed_url.netloc or not preset.target_route.startswith("/") or preset.target_route.startswith("//") or parsed_url.path != preset.target_route:
            raise ValueError("preset target route is not an application route")
        route = routes.get(preset.target_route)
        if route is None or route.methods != ("GET",) or route.classification != "HTML":
            raise ValueError("preset target route contract mismatch")
        expected = _EXPECTED_QUERY_STATE.get(preset.preset_id)
        if expected is None or dict(preset.query_values) != expected:
            raise ValueError("preset normalized query contract mismatch")
        normalized = parse_qs(parsed_url.query, keep_blank_values=True)
        if normalized != {key: [value] for key, value in expected.items()}:
            raise ValueError("preset URL normalization mismatch")
        path, _, query=url.partition("?"); captured={}
        body=b"".join(application({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":query}, lambda status, headers: captured.update(status=status, headers=headers)))
        status=captured.get("status", "")
        if status != "200 OK":
            raise ValueError("preset query was not accepted by its target parser")
        if not any(value.lower().startswith("text/html") for key, value in captured.get("headers", []) if key.lower() == "content-type"):
            raise ValueError("preset target did not return HTML")
        if any(key.lower() == "set-cookie" for key, _ in captured.get("headers", [])) or b"https://" in body or b"http://" in body:
            raise ValueError("preset response is not local and stateless")
        unknown = dict(expected); unknown["__unknown_preset_key"] = "1"
        invalid_capture={}; application({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":urlencode(unknown,safe="")}, lambda status, headers: invalid_capture.update(status=status))
        if invalid_capture.get("status") == "200 OK":
            raise ValueError("unknown preset query survived normalization")
        invalid_values={key:"__invalid_preset_value__" for key in expected} or {"__invalid_preset_value":"1"}
        invalid_capture={}; application({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":urlencode(invalid_values,safe="")}, lambda status, headers: invalid_capture.update(status=status))
        if invalid_capture.get("status") == "200 OK":
            raise ValueError("invalid preset query value survived normalization")
        post_capture={}; application({"REQUEST_METHOD":"POST","PATH_INFO":path,"QUERY_STRING":query}, lambda status, headers: post_capture.update(status=status))
        if post_capture.get("status") != "405 Method Not Allowed":
            raise ValueError("preset target accepted a non-GET method")
        results.append({"preset_id":preset.preset_id,"target_route":preset.target_route,"status":status,"normalized_query":expected,"body_length":len(body)})
    if application.config.refresh_interval != 0:
        raise ValueError("operator auto-refresh is enabled by default")
    catalog={}
    for catalog_path in ("/views", "/api/views"):
        captured={}; body=b"".join(application({"REQUEST_METHOD":"GET","PATH_INFO":catalog_path,"QUERY_STRING":""}, lambda status, headers: captured.update(status=status))).decode("utf-8")
        if captured.get("status") != "200 OK": raise ValueError("preset catalog route failed")
        catalog[catalog_path]=body
    for preset in selected:
        if preset.url() not in catalog["/api/views"] or f'href="{preset.url()}"' not in catalog["/views"]:
            raise ValueError("HTML/API preset catalog mismatch")
        flags=preset.to_dict()
        if not flags["read_only"] or flags["permits_automated_action"] or flags["live_ready"] or flags["unattended_ready"]:
            raise ValueError("preset safety declaration mismatch")
    return tuple(results)
