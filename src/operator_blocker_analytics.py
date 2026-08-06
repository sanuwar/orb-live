"""Deterministic aggregation of existing privacy-safe blocker codes."""
from __future__ import annotations
from dataclasses import dataclass
import json
from src.operator_app_store import list_events,list_incidents,list_import_batches,list_snapshots
@dataclass(frozen=True)
class BlockerAnalytics:
    generated_as_of:str; total_blocker_occurrences:int; unique_blocker_count:int; blocker_rows:tuple[dict,...]
    def to_dict(self): return {"generated_as_of":self.generated_as_of,"total_blocker_occurrences":self.total_blocker_occurrences,"unique_blocker_count":self.unique_blocker_count,"blocker_rows":[dict(x) for x in self.blocker_rows],"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def summarize_blockers(database,generated_as_of,active_only=False):
    groups={}
    def add(code,kind,when,component="operator"):
        g=groups.setdefault(code,{"blocker_code":code,"occurrence_count":0,"snapshot_count":0,"event_count":0,"incident_count":0,"import_batch_count":0,"first_observed_at":when,"latest_observed_at":when,"currently_observed":False,"affected_components":set(),"severity_counts":{}}); g["occurrence_count"]+=1; g[kind+"_count"]+=1; g["first_observed_at"]=min(g["first_observed_at"],when); g["latest_observed_at"]=max(g["latest_observed_at"],when); g["affected_components"].add(component)
    for row in list_snapshots(database,page=1,page_size=100)[0]:
        for code in json.loads(row["blocker_reasons_json"]): add(code,"snapshot",row["generated_as_of"])
    for row in list_events(database,page=1,page_size=100)[0]:
        for code in json.loads(row["blocker_reasons_json"]): add(code,"event",row["occurred_at"],row["source_component"])
    for row in list_incidents(database,page=1,page_size=100)[0]:
        for code in json.loads(row["blocker_reasons_json"]): add(code,"incident",row["latest_observed_at"],row["source_component"])
    rows=[]
    for code,g in sorted(groups.items()): g["affected_components"]=sorted(g["affected_components"]); g["severity_counts"]={}; rows.append(g)
    if active_only: rows=[x for x in rows if x["currently_observed"]]
    return BlockerAnalytics(generated_as_of,sum(x["occurrence_count"] for x in rows),len(rows),tuple(rows))
