"""Bounded search over explicitly allowlisted operator read-model fields."""
from __future__ import annotations
from dataclasses import dataclass
import re
from src.operator_app_store import list_snapshots, list_events, list_incidents, OperatorAppStoreError
TYPES=frozenset({"ALL","SNAPSHOT","EVENT","INCIDENT","IMPORT_BATCH"})
@dataclass(frozen=True)
class SearchResult:
    result_type:str; result_id:str; occurred_at:str; headline:str; secondary_status:str; source_component:str; blocker_reasons:tuple[str,...]; detail_url:str
    def to_dict(self): return {"result_type":self.result_type,"result_id":self.result_id,"occurred_at":self.occurred_at,"headline":self.headline,"secondary_status":self.secondary_status,"source_component":self.source_component,"blocker_reasons":list(self.blocker_reasons),"detail_url":self.detail_url,"safety_state":"CLOSED","permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def search_store(database, query, *, result_type="ALL", page=1, page_size=25):
    q=" ".join(query.casefold().split())
    if not 2<=len(q)<=80 or result_type not in TYPES or type(page)!=int or page<1 or type(page_size)!=int or not 1<=page_size<=100: raise OperatorAppStoreError("search query is invalid")
    out=[]
    if result_type in {"ALL","SNAPSHOT"}:
        rows,_=list_snapshots(database,page=1,page_size=100)
        for d in rows:
            fields=[d["snapshot_id"],d["operational_status"],d["supervised_readiness_status"],d["recommended_operator_action"],d["blocker_reasons_json"],d["source_component_statuses_json"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("SNAPSHOT",d["snapshot_id"],d["generated_as_of"],d["operational_status"],d["supervised_readiness_status"],"operator",(),"/history/"+d["snapshot_id"]))
    if result_type in {"ALL","EVENT"}:
        rows,_=list_events(database,page=1,page_size=100)
        for d in rows:
            fields=[d["event_id"],d["event_type"],d["severity"],d["source_component"],d["headline"],d["detail_code"],d["blocker_reasons_json"],d["operator_action"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("EVENT",d["event_id"],d["occurred_at"],d["headline"],d["severity"],d["source_component"],tuple(),"/timeline/"+d["event_id"]))
    if result_type in {"ALL","INCIDENT"}:
        rows,_=list_incidents(database,page=1,page_size=100)
        for d in rows:
            fields=[d["incident_id"],d["incident_type"],d["severity"],d["observation_status"],d["source_component"],d["headline"],d["detail_code"],d["blocker_reasons_json"],d["recommended_operator_action"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("INCIDENT",d["incident_id"],d["latest_observed_at"],d["headline"],d["severity"],d["source_component"],tuple(),"/incidents/"+d["incident_id"]))
    out.sort(key=lambda x:(x.occurred_at,x.result_type,x.result_id),reverse=True); start=(page-1)*page_size; return tuple(out[start:start+page_size]),len(out)
