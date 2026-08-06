"""Hardcoded, relative, non-persistent operator view presets."""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlencode
from src.operator_route_contracts import discover_application_routes
@dataclass(frozen=True)
class ViewPreset:
    preset_id:str; title:str; description:str; target_route:str; query_values:tuple[tuple[str,str],...]; category:str
    def url(self): return self.target_route + (("?"+urlencode(self.query_values,safe="")) if self.query_values else "")
    def to_dict(self): return {"preset_id":self.preset_id,"title":self.title,"description":self.description,"target_route":self.target_route,"url":self.url(),"query_values":dict(self.query_values),"allowed_query_parameters":[k for k,v in self.query_values],"category":self.category,"read_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
PRESETS=tuple(ViewPreset(*x) for x in (("current-blockers","Current blockers","Observed blocker evidence","/blockers",(("active_only","true"),),"SAFETY"),("active-incidents","Active incidents","Currently observed incidents","/incidents",(("active_only","true"),),"INCIDENTS"),("stale-evidence","Stale evidence","Evidence marked stale","/freshness",(),"EVIDENCE"),("missing-evidence","Missing evidence","Missing evidence categories","/coverage",(),"EVIDENCE"),("recent-activity","Recent activity","Recent read-model activity","/activity",(),"ACTIVITY"),("recent-imports","Recent imports","Recent import provenance","/imports",(),"PROVENANCE"),("fail-closed-status","Fail-closed status","Read-model status","/data-status",(),"STATUS"),("recent-high-severity-events","Recent high-severity events","Observed high-severity timeline evidence","/timeline",(("severity","HIGH"),),"TIMELINE"),("incomplete-evidence","Incomplete evidence","Evidence completeness requiring review","/history",(("status","INCOMPLETE"),),"EVIDENCE"),("integrity-review","Integrity review","Read-model identity and integrity status","/data-status",(),"STATUS")))
def list_presets(): return PRESETS
def get_preset(preset_id):
    for preset in PRESETS:
        if preset.preset_id==preset_id:return preset
    raise KeyError("preset is unknown")

def validate_preset_routes(application, presets=None):
    """Exercise catalog URLs against the application's independent routes."""
    results=[]
    for preset in list(PRESETS if presets is None else presets):
        url=preset.url()
        if not preset.target_route.startswith("/") or preset.target_route.startswith("//") or preset.target_route not in discover_application_routes():
            raise ValueError("preset target route is not an application route")
        path, _, query=url.partition("?"); captured={}
        body=b"".join(application({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":query}, lambda status, headers: captured.update(status=status)))
        status=captured.get("status", "")
        if status.startswith("400") or status.startswith("404") or status.startswith("405"):
            raise ValueError("preset query was rejected by its target parser")
        results.append({"preset_id":preset.preset_id,"status":status,"body_length":len(body)})
    return tuple(results)
