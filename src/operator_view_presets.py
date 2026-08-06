"""Hardcoded, relative, non-persistent operator view presets."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ViewPreset:
    preset_id:str; title:str; description:str; target_route:str; query_values:tuple[tuple[str,str],...]; category:str
    def to_dict(self): return {"preset_id":self.preset_id,"title":self.title,"description":self.description,"target_route":self.target_route,"query_values":dict(self.query_values),"allowed_query_parameters":[k for k,v in self.query_values],"category":self.category,"read_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
PRESETS=tuple(ViewPreset(*x) for x in (("current-blockers","Current blockers","Observed blocker evidence","/blockers",(("active_only","true"),),"SAFETY"),("active-incidents","Active incidents","Currently observed incidents","/incidents",(("active_only","true"),),"INCIDENTS"),("stale-evidence","Stale evidence","Evidence marked stale","/freshness",(),"EVIDENCE"),("missing-evidence","Missing evidence","Missing evidence categories","/coverage",(),"EVIDENCE"),("recent-activity","Recent activity","Recent read-model activity","/activity",(),"ACTIVITY"),("recent-imports","Recent imports","Recent import provenance","/imports",(),"PROVENANCE"),("fail-closed-status","Fail-closed status","Read-model status","/data-status",(),"STATUS")))
def list_presets(): return PRESETS
def get_preset(preset_id):
    for preset in PRESETS:
        if preset.preset_id==preset_id:return preset
    raise KeyError("preset is unknown")
