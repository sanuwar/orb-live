"""Read-only freshness view using the validated projection freshness field."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from src.operator_app_store import list_snapshots
FRESHNESS_THRESHOLD_SECONDS=3600
@dataclass(frozen=True)
class EvidenceFreshness:
    generated_as_of:str; overall_freshness_status:str; freshest_evidence_at:str|None; stalest_evidence_at:str|None; evidence_rows:tuple[dict,...]; stale_count:int; missing_count:int; future_dated_count:int; blocker_reasons:tuple[str,...]
    def to_dict(self): return {"generated_as_of":self.generated_as_of,"overall_freshness_status":self.overall_freshness_status,"freshest_evidence_at":self.freshest_evidence_at,"stalest_evidence_at":self.stalest_evidence_at,"evidence_rows":[dict(x) for x in self.evidence_rows],"stale_count":self.stale_count,"missing_count":self.missing_count,"future_dated_count":self.future_dated_count,"blocker_reasons":list(self.blocker_reasons),"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def _moment(value):
    try: parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError): raise ValueError("freshness timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError("freshness timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)

def summarize_freshness(database,generated_as_of):
    as_of=_moment(generated_as_of)
    rows=list_snapshots(database,page=1,page_size=100)[0]; out=[]
    for row in rows:
        observed=_moment(row["generated_as_of"]); age=(as_of-observed).total_seconds(); blockers=[]
        if age < 0: cls="UNKNOWN_FAIL_CLOSED"; blockers.append("FUTURE_DATED_EVIDENCE"); age_value=age
        elif row["evidence_freshness"] in {"MISSING","UNKNOWN","UNKNOWN_FAIL_CLOSED"}: cls="UNKNOWN_FAIL_CLOSED"; blockers.append("MISSING_FRESHNESS_EVIDENCE"); age_value=age
        else: cls="FRESH" if age <= FRESHNESS_THRESHOLD_SECONDS else "STALE"; age_value=age
        out.append({"component":"operator_console","evidence_type":"snapshot","observed_at":row["generated_as_of"],"age_seconds":age_value,"freshness_class":cls,"threshold_code":"ONE_HOUR_OBSERVATION_AGE","source_id":row["snapshot_id"],"detail_url":"/history/"+row["snapshot_id"],"blocker_reasons":blockers})
    out.sort(key=lambda x:(_moment(x["observed_at"]),x["source_id"])); classes=[x["freshness_class"] for x in out]; status="UNKNOWN_FAIL_CLOSED" if not out or any(x=="UNKNOWN_FAIL_CLOSED" for x in classes) else "FRESH" if all(x=="FRESH" for x in classes) else "STALE"
    return EvidenceFreshness(generated_as_of,status,out[-1]["observed_at"] if out else None,out[0]["observed_at"] if out else None,tuple(out),sum(x["freshness_class"]=="STALE" for x in out),sum(x["freshness_class"]=="UNKNOWN_FAIL_CLOSED" and "MISSING_FRESHNESS_EVIDENCE" in x["blocker_reasons"] for x in out),sum("FUTURE_DATED_EVIDENCE" in x["blocker_reasons"] for x in out),tuple(sorted({b for x in out for b in x["blocker_reasons"]})))
