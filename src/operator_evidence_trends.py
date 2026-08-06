"""Descriptive, bounded evidence trend projection; never predictive."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
METRICS=frozenset({"blocker_occurrences","active_incidents","event_count","stale_evidence","missing_evidence","import_count"})
@dataclass(frozen=True)
class EvidenceTrends:
    schema_version:int; generated_as_of:str; window_start:str; window_end:str; bucket_size:str; selected_metric:str; buckets:tuple[dict,...]; total_count:int; minimum_count:int; maximum_count:int; latest_count:int
    def to_dict(self): return {"schema_version":self.schema_version,"generated_as_of":self.generated_as_of,"window_start":self.window_start,"window_end":self.window_end,"bucket_size":self.bucket_size,"selected_metric":self.selected_metric,"buckets":[dict(x) for x in self.buckets],"total_count":self.total_count,"minimum_count":self.minimum_count,"maximum_count":self.maximum_count,"latest_count":self.latest_count,"data_completeness":"COMPLETE","descriptive_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def summarize_trends(values,generated_as_of,metric="event_count",bucket="HOUR",max_buckets=None):
    if metric not in METRICS or bucket not in {"HOUR","DAY"} or (max_buckets is not None and not 1 <= max_buckets <= 100): raise ValueError("trend selection is invalid")
    ordered=tuple(sorted(values,key=lambda x:x[0])); ordered=ordered[-max_buckets:] if max_buckets is not None else ordered; buckets=tuple({"bucket_start":t,"bucket_end":t,"value":int(v),"source_record_count":1,"completeness_status":"COMPLETE"} for t,v in ordered); nums=[x["value"] for x in buckets]; return EvidenceTrends(1,generated_as_of,ordered[0][0] if ordered else generated_as_of,ordered[-1][0] if ordered else generated_as_of,bucket,metric,buckets,sum(nums) if nums else 0,min(nums) if nums else 0,max(nums) if nums else 0,nums[-1] if nums else 0)
