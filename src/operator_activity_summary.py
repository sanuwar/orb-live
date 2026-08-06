"""Bounded read-only summary of existing operator read-model projections."""
from __future__ import annotations
from dataclasses import dataclass
from src.operator_app_store import inspect_store_content, list_events, list_incidents, list_import_batches, list_snapshots
@dataclass(frozen=True)
class OperatorActivitySummary:
    generated_as_of:str; window_start:str|None; window_end:str|None; snapshot_count:int; event_count:int; incident_count:int; active_incident_count:int; import_count:int; latest_snapshot_at:str|None; latest_event_at:str|None; latest_incident_at:str|None; latest_import_at:str|None; dominant_blocker_codes:tuple[str,...]; recent_severity_counts:tuple[tuple[str,int],...]; recent_event_type_counts:tuple[tuple[str,int],...]; unresolved_observation_count:int; data_completeness:str; data_freshness:str
    def to_dict(self):
        return {"generated_as_of":self.generated_as_of,"window_start":self.window_start,"window_end":self.window_end,"snapshot_count":self.snapshot_count,"event_count":self.event_count,"incident_count":self.incident_count,"active_incident_count":self.active_incident_count,"import_count":self.import_count,"latest_snapshot_at":self.latest_snapshot_at,"latest_event_at":self.latest_event_at,"latest_incident_at":self.latest_incident_at,"latest_import_at":self.latest_import_at,"dominant_blocker_codes":list(self.dominant_blocker_codes),"recent_severity_counts":dict(self.recent_severity_counts),"recent_event_type_counts":dict(self.recent_event_type_counts),"unresolved_observation_count":self.unresolved_observation_count,"data_completeness":self.data_completeness,"data_freshness":self.data_freshness,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def summarize_store(database, generated_as_of):
    content=inspect_store_content(database); snapshots,_=list_snapshots(database,page=1,page_size=100); events,_=list_events(database,page=1,page_size=100); incidents,_=list_incidents(database,page=1,page_size=100); imports,_=list_import_batches(database,page=1,page_size=100); blockers={}; severities={}; types={}
    for row in snapshots:
        import json
        for code in json.loads(row["blocker_reasons_json"]): blockers[code]=blockers.get(code,0)+1
    for row in events: severities[row["severity"]]=severities.get(row["severity"],0)+1; types[row["event_type"]]=types.get(row["event_type"],0)+1
    active_count=sum(1 for x in incidents if x["observation_status"]=="ACTIVE")
    return OperatorActivitySummary(generated_as_of,None,None,content["snapshot_count"],content["event_count"],content["incident_count"],active_count,content["import_batch_count"],content["newest_snapshot_at"],content["newest_event_at"],content["newest_incident_at"],content["newest_imported_at"],tuple(sorted(blockers)),tuple(sorted(severities.items())),tuple(sorted(types.items())),active_count,"COMPLETE","CURRENT")
