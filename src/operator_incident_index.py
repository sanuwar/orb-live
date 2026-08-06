"""Read-only incident index built from the existing incident projection."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from src.operator_app_store import OperatorAppStoreError, get_incident_projection, list_incidents

@dataclass(frozen=True)
class IncidentIndexItem:
    projection:object
    def to_dict(self):
        d=self.projection.to_dict(); d.update({"detail_url":"/incidents/"+d["incident_id"],"json_export_url":"/api/incidents/"+d["incident_id"],"html_export_url":"/incidents/"+d["incident_id"]+"/export.html"}); return d

def _time(value):
    try:
        d=datetime.fromisoformat(value.replace("Z","+00:00"))
        if d.tzinfo is None or d.utcoffset() is None: raise ValueError
        return d
    except (AttributeError,ValueError): raise OperatorAppStoreError("incident time filter is invalid") from None

def list_incident_index(database, *, incident_type=None, severity=None, observation_status=None, source_component=None, active_only=False, from_value=None, to_value=None, page=1, page_size=25):
    if type(page) is not int or page<1 or type(page_size) is not int or not 1<=page_size<=100: raise OperatorAppStoreError("incident pagination is invalid")
    low=_time(from_value) if from_value else None; high=_time(to_value) if to_value else None
    if low and high and low>high: raise OperatorAppStoreError("incident time range is invalid")
    rows,_=list_incidents(database,incident_type=incident_type,severity=severity,page=1,page_size=100); out=[]
    for row in rows:
        p=get_incident_projection(database,row["incident_id"]); d=p.to_dict()
        if observation_status and d["active"]!=observation_status: continue
        if source_component and d["source_component"]!=source_component: continue
        if active_only and d["active"]!="ACTIVE": continue
        moment=_time(d["latest_observed_at"])
        if low and moment<low or high and moment>high: continue
        out.append(IncidentIndexItem(p))
    start=(page-1)*page_size; return tuple(out[start:start+page_size]),len(out)
