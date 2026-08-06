"""Bounded search over explicitly allowlisted operator read-model fields."""
from __future__ import annotations
from dataclasses import dataclass
import re
from datetime import datetime
from src.operator_app_store import list_all_table, OperatorAppStoreError
from src.operator_import_history import list_operator_imports
TYPES=frozenset({"ALL","SNAPSHOT","EVENT","INCIDENT","IMPORT_BATCH"})
@dataclass(frozen=True)
class SearchResult:
    result_type:str; result_id:str; occurred_at:str; headline:str; secondary_status:str; source_component:str; blocker_reasons:tuple[str,...]; detail_url:str
    def to_dict(self): return {"result_type":self.result_type,"result_id":self.result_id,"occurred_at":self.occurred_at,"headline":self.headline,"secondary_status":self.secondary_status,"source_component":self.source_component,"blocker_reasons":list(self.blocker_reasons),"detail_url":self.detail_url,"safety_state":"CLOSED","permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError
        return parsed
    except (AttributeError, ValueError):
        raise OperatorAppStoreError("search timestamp is invalid") from None

def search_store(database, query, *, result_type="ALL", from_value=None, to_value=None, page=1, page_size=25, expected_content_digest=None):
    q=" ".join(query.casefold().split())
    if not 2<=len(q)<=80 or result_type not in TYPES or type(page)!=int or page<1 or type(page_size)!=int or not 1<=page_size<=100: raise OperatorAppStoreError("search query is invalid")
    lower = _timestamp(from_value) if from_value else None
    upper = _timestamp(to_value) if to_value else None
    if lower and upper and lower > upper: raise OperatorAppStoreError("search time range is invalid")
    out=[]
    if result_type in {"ALL","SNAPSHOT"}:
        rows=list_all_table(database,"operator_snapshots")
        for d in rows:
            fields=[d["snapshot_id"],d["operational_status"],d["supervised_readiness_status"],d["recommended_operator_action"],d["blocker_reasons_json"],d["source_component_statuses_json"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("SNAPSHOT",d["snapshot_id"],d["generated_as_of"],d["operational_status"],d["supervised_readiness_status"],"operator",(),"/history/"+d["snapshot_id"]))
    if result_type in {"ALL","EVENT"}:
        rows=list_all_table(database,"operator_events")
        for d in rows:
            fields=[d["event_id"],d["event_type"],d["severity"],d["source_component"],d["headline"],d["detail_code"],d["blocker_reasons_json"],d["operator_action"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("EVENT",d["event_id"],d["occurred_at"],d["headline"],d["severity"],d["source_component"],tuple(),"/timeline/"+d["event_id"]))
    if result_type in {"ALL","INCIDENT"}:
        rows=list_all_table(database,"operator_incidents")
        for d in rows:
            fields=[d["incident_id"],d["incident_type"],d["severity"],d["observation_status"],d["source_component"],d["headline"],d["detail_code"],d["blocker_reasons_json"],d["recommended_operator_action"]]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("INCIDENT",d["incident_id"],d["latest_observed_at"],d["headline"],d["severity"],d["source_component"],tuple(),"/incidents/"+d["incident_id"]))
    if result_type in {"ALL", "IMPORT_BATCH"}:
        rows, _ = list_operator_imports(database, page=1, page_size=100, expected_content_digest=expected_content_digest)
        for d in rows:
            fields=[d.import_batch_id,d.consistency_status,d.blocker_reasons,d.source_release_commit_sha_short]
            if any(q in str(x).casefold() for x in fields): out.append(SearchResult("IMPORT_BATCH",d.import_batch_id,d.imported_at,d.consistency_status,d.consistency_status,"operator_app",tuple(d.blocker_reasons),"/imports/"+d.import_batch_id))
    if lower or upper:
        out=[x for x in out if (not lower or _timestamp(x.occurred_at)>=lower) and (not upper or _timestamp(x.occurred_at)<=upper)]
    out.sort(key=lambda x:(x.occurred_at,x.result_type,x.result_id),reverse=True); start=(page-1)*page_size; return tuple(out[start:start+page_size]),len(out)
