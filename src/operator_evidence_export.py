"""Allowlisted, direct-response evidence export projections."""
from __future__ import annotations
from dataclasses import dataclass
import json
@dataclass(frozen=True)
class EvidenceExport:
    export_type:str; source_id:str; generated_as_of:str; source_timestamp:str; title:str; format:str; content_type:str; download_url:str; source_detail_url:str
    def to_dict(self): return {"export_type":self.export_type,"source_id":self.source_id,"generated_as_of":self.generated_as_of,"source_timestamp":self.source_timestamp,"title":self.title,"format":self.format,"content_type":self.content_type,"download_url":self.download_url,"source_detail_url":self.source_detail_url,"privacy_classification":"OPERATOR_SAFE","read_only":True,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
def catalog_snapshot(item):
    d=item.to_dict(); return EvidenceExport("SNAPSHOT",item.snapshot_id,d["generated_as_of"],d["generated_as_of"],"Operator snapshot","JSON","application/json",f"/exports/snapshot/{item.snapshot_id}.json",f"/history/{item.snapshot_id}")
def catalog_event(event): return EvidenceExport("EVENT",event.event_id,event.occurred_at,event.occurred_at,event.headline,"JSON","application/json",f"/exports/event/{event.event_id}.json",f"/timeline/{event.event_id}")
def catalog_incident(incident): return EvidenceExport("INCIDENT",incident.incident_id,incident.opened_at,incident.latest_observed_at,incident.headline,"JSON","application/json",f"/exports/incident/{incident.incident_id}.json",f"/incidents/{incident.incident_id}")
def catalog_import(batch): return EvidenceExport("IMPORT_BATCH",batch.import_batch_id,batch.imported_at,batch.imported_at,"Operator import provenance","JSON","application/json",f"/exports/import/{batch.import_batch_id}.json",f"/imports/{batch.import_batch_id}")
def safe_json(value): return json.dumps(value,sort_keys=True,separators=(",",":"))+"\n"
