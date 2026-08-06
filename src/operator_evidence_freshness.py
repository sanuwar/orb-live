"""Read-only freshness view using the validated projection freshness field."""
from __future__ import annotations
from dataclasses import dataclass
from src.operator_app_store import list_snapshots
@dataclass(frozen=True)
class EvidenceFreshness:
    generated_as_of:str; overall_freshness_status:str; freshest_evidence_at:str|None; stalest_evidence_at:str|None; evidence_rows:tuple[dict,...]; stale_count:int; missing_count:int; future_dated_count:int; blocker_reasons:tuple[str,...]
    def to_dict(self): return {"generated_as_of":self.generated_as_of,"overall_freshness_status":self.overall_freshness_status,"freshest_evidence_at":self.freshest_evidence_at,"stalest_evidence_at":self.stalest_evidence_at,"evidence_rows":[dict(x) for x in self.evidence_rows],"stale_count":self.stale_count,"missing_count":self.missing_count,"future_dated_count":self.future_dated_count,"blocker_reasons":list(self.blocker_reasons),"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def summarize_freshness(database,generated_as_of):
    rows=list_snapshots(database,page=1,page_size=100)[0]; out=[]
    for row in rows:
        cls="FRESH" if row["evidence_freshness"]=="CURRENT" else "STALE" if row["evidence_freshness"]=="STALE" else "UNKNOWN_FAIL_CLOSED"; out.append({"component":"operator_console","evidence_type":"snapshot","observed_at":row["generated_as_of"],"age_seconds":0,"freshness_class":cls,"threshold_code":"EXISTING_PROJECTION","source_id":row["snapshot_id"],"detail_url":"/history/"+row["snapshot_id"],"blocker_reasons":[] if cls=="FRESH" else ["EVIDENCE_FRESHNESS_UNAVAILABLE"]})
    out.sort(key=lambda x:(x["observed_at"],x["source_id"])); classes=[x["freshness_class"] for x in out]; status="FRESH" if out and all(x=="FRESH" for x in classes) else "UNKNOWN_FAIL_CLOSED" if not out else "STALE"
    return EvidenceFreshness(generated_as_of,status,out[0]["observed_at"] if out else None,out[-1]["observed_at"] if out else None,tuple(out),sum(x=="STALE" for x in classes),0,0,tuple(sorted({b for x in out for b in x["blocker_reasons"]})))
