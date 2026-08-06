"""Descriptive, bounded evidence trend projection; never predictive."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
# Historical incident population cannot be reconstructed from observation rows;
# reject that misleading metric rather than repeating a current count by bucket.
METRICS=frozenset({"blocker_occurrences","event_count","stale_evidence","missing_evidence","import_count"})
@dataclass(frozen=True)
class EvidenceTrends:
    schema_version:int; generated_as_of:str; window_start:str; window_end:str; bucket_size:str; selected_metric:str; buckets:tuple[dict,...]; total_count:int; minimum_count:int; maximum_count:int; latest_count:int
    def to_dict(self): return {"schema_version":self.schema_version,"generated_as_of":self.generated_as_of,"window_start":self.window_start,"window_end":self.window_end,"bucket_size":self.bucket_size,"selected_metric":self.selected_metric,"buckets":[dict(x) for x in self.buckets],"total_count":self.total_count,"minimum_count":self.minimum_count,"maximum_count":self.maximum_count,"latest_count":self.latest_count,"data_completeness":"COMPLETE","descriptive_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def _moment(value):
    try: parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError): raise ValueError("trend timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError("trend timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)

def _floor(value,bucket):
    return value.replace(minute=0,second=0,microsecond=0) if bucket=="HOUR" else value.replace(hour=0,minute=0,second=0,microsecond=0)

def summarize_trends(values,generated_as_of,metric="event_count",bucket="HOUR",max_buckets=None,window_start=None,window_end=None):
    if metric not in METRICS or bucket not in {"HOUR","DAY"} or (max_buckets is not None and not 1 <= max_buckets <= 100): raise ValueError("trend selection is invalid")
    if window_start is not None and window_end is not None and _moment(window_start)>_moment(window_end): raise ValueError("trend range is invalid")
    parsed=tuple((_moment(t),int(v)) for t,v in values)
    start=_floor(_moment(window_start),bucket) if window_start else (_floor(min((x[0] for x in parsed)),bucket) if parsed else _moment(generated_as_of))
    end=_moment(window_end) if window_end else ((_floor(max((x[0] for x in parsed)),bucket)+ (timedelta(hours=1) if bucket=="HOUR" else timedelta(days=1))) if parsed else start+(timedelta(hours=1) if bucket=="HOUR" else timedelta(days=1)))
    step=timedelta(hours=1) if bucket=="HOUR" else timedelta(days=1); starts=[]; cursor=start
    while cursor<end:
        starts.append(cursor); cursor+=step
        if len(starts)>1000: raise ValueError("trend range is too large")
    if max_buckets is not None and len(starts)>max_buckets: starts=starts[-max_buckets:]; start=starts[0]
    totals={x: [0,0] for x in starts}
    for moment,value in parsed:
        if moment<start or moment>=end: continue
        bucket_start=_floor(moment,bucket)
        if bucket_start in totals: totals[bucket_start][0]+=value; totals[bucket_start][1]+=1
    result=tuple({"bucket_start":x.isoformat().replace("+00:00","Z"),"bucket_end":(x+step).isoformat().replace("+00:00","Z"),"value":totals[x][0],"source_record_count":totals[x][1],"completeness_status":"COMPLETE"} for x in starts)
    nums=[x["value"] for x in result]
    return EvidenceTrends(1,generated_as_of,start.isoformat().replace("+00:00","Z"),end.isoformat().replace("+00:00","Z"),bucket,metric,result,sum(nums),min(nums) if nums else 0,max(nums) if nums else 0,nums[-1] if nums else 0)
