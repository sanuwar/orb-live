"""Strict, read-only-query SQLite store for operator projections."""
from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from src.operator_app_schema import create_schema, SCHEMA_VERSION
from src.offline_path_safety import is_repository_data_path, REPOSITORY_ROOT

class OperatorAppStoreError(ValueError): pass
_SNAP_FIELDS=("snapshot_id","generated_as_of","release_commit_sha","implementation_digest","operating_mode","operational_status","supervised_readiness_status","recommended_operator_action","evidence_completeness","evidence_freshness","suspension_status","heartbeat_status","alert_status","acknowledgment_status","recovery_status","blocker_reasons_json","source_component_statuses_json","strategy_enabled","activation_approvals_present","approved_protection_state_count","protection_grounded","activation_approved","permits_automated_action","live_ready","unattended_ready","canonical_digest","import_batch_id")
def _path(path: Path, *, allow_memory=False) -> Path|str:
    if str(path)==":memory:":
        if allow_memory:return ":memory:"
        raise OperatorAppStoreError("memory database is test-only")
    p=Path(path)
    if p.suffix.casefold() != ".sqlite" or not p.is_absolute(): raise OperatorAppStoreError("database must be an absolute SQLite path")
    root=p.parent.resolve(strict=False)
    if is_repository_data_path(p) or root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents or any(x.casefold() in {"src","tests","templates","static"} for x in p.parts): raise OperatorAppStoreError("database path is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.is_symlink(): raise OperatorAppStoreError("database symlink is unsafe")
    return p
def _conn(path, *, readonly=False):
    p=_path(path, allow_memory=not readonly)
    if readonly and not Path(p).is_file(): raise OperatorAppStoreError("database is unavailable")
    uri=f"file:{p.as_posix()}?mode=ro" if readonly else str(p)
    c=sqlite3.connect(uri, uri=readonly, timeout=2); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def initialize_store(database, *, release_commit_sha, implementation_digest):
    c=_conn(database)
    try:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata'").fetchone():
            row=c.execute("SELECT * FROM schema_metadata").fetchone()
            if row["schema_version"] != 1: raise OperatorAppStoreError("unknown schema version")
            if row["release_commit_sha"] != release_commit_sha or row["implementation_digest"] != implementation_digest: raise OperatorAppStoreError("store identity conflicts")
        else: create_schema(c, release_commit_sha=release_commit_sha, implementation_digest=implementation_digest)
        c.commit()
    except Exception: c.rollback(); raise
    finally:c.close()
def inspect_store_identity(database):
    c=_conn(database,readonly=True)
    try:return dict(c.execute("SELECT * FROM schema_metadata").fetchone())
    finally:c.close()
def _digest(kind, data): return hashlib.sha256((kind+"\0"+json.dumps(data,sort_keys=True,separators=(",",":"))).encode()).hexdigest()
def import_projection_batch(database, *, snapshots, events, incidents, imported_at, source_release_commit_sha, source_implementation_digest):
    c=_conn(database); batch_data={"snapshots":[x.to_dict() for x in snapshots],"events":[x.to_dict() for x in events],"incidents":[x.to_dict() for x in incidents]}; bd=_digest("batch",batch_data); bid="import-"+bd
    try:
        c.execute("BEGIN IMMEDIATE")
        old=c.execute("SELECT import_batch_id FROM import_batches WHERE batch_digest=?",(bd,)).fetchone()
        if old: c.commit(); return bid
        c.execute("INSERT INTO import_batches VALUES (?,?,?,?,?,?,?,?)",(bid,imported_at,source_release_commit_sha,source_implementation_digest,len(snapshots),len(snapshots),0,bd))
        for s in snapshots:
            d=s.to_dict(); raw={k:d[k] for k in d if k not in {"schema_version","scheduler_status","runner_status"}}; sd=_digest("snapshot",raw)
            vals=(d["snapshot_id"],d["generated_as_of"],d["release_commit_sha"],d["implementation_digest"],d["operating_mode"],d["operational_status"],d["supervised_readiness_status"],d["recommended_operator_action"],d["evidence_completeness"],d["evidence_freshness"],d["suspension_status"],d["heartbeat_status"],d["alert_status"],d["acknowledgment_status"],d["recovery_status"],json.dumps(d["blocker_reasons"],sort_keys=True,separators=(",",":")),json.dumps(d["source_component_statuses"],sort_keys=True,separators=(",",":")),0,0,0,0,0,0,0,0,sd,bid)
            c.execute("INSERT INTO operator_snapshots VALUES ("+",".join("?"*len(vals))+")",vals)
        for e in events:
            d=e.to_dict(); ed=_digest("event",d); vals=(d["event_id"],d["event_type"],d["severity"],d["occurred_at"],d["source_snapshot_id"],d["source_component"],d["headline"],d["detail_code"],d["operational_status"],d["supervised_readiness_status"],json.dumps(d["blocker_reasons"],sort_keys=True,separators=(",",":")),d["operator_action"],0,0,0,ed,bid); c.execute("INSERT INTO operator_events VALUES ("+",".join("?"*len(vals))+")",vals)
        for i in incidents:
            d=i.to_dict(); idig=_digest("incident",d); vals=(d["incident_id"],d["source_event_id"],d["incident_type"],d["severity"],d["opened_at"],d["latest_observed_at"],d["active"],d["source_component"],d["headline"],d["detail_code"],json.dumps(d["blocker_reasons"],sort_keys=True,separators=(",",":")),d["recommended_operator_action"],d["operational_status"],d["supervised_readiness_status"],d["evidence_completeness"],d["evidence_freshness"],0,0,0,0,0,0,0,idig,bid); c.execute("INSERT INTO operator_incidents VALUES ("+",".join("?"*len(vals))+")",vals)
            for n,eid in enumerate(d["supporting_event_ids"]): c.execute("INSERT INTO incident_evidence_links VALUES (?,?,?,?)",(d["incident_id"],"event",eid,n))
            for n,sid in enumerate(d["supporting_snapshot_ids"],len(d["supporting_event_ids"])): c.execute("INSERT INTO incident_evidence_links VALUES (?,?,?,?)",(d["incident_id"],"snapshot",sid,n))
        c.commit(); return bid
    except Exception: c.rollback(); raise
    finally:c.close()
def _list(database, table, *, where="", params=(), page=1, page_size=25):
    if type(page)!=int or page<1 or type(page_size)!=int or not 1<=page_size<=100: raise OperatorAppStoreError("pagination is invalid")
    c=_conn(database,readonly=True)
    try:
        total=c.execute(f"SELECT count(*) FROM {table}"+(" WHERE "+where if where else ""),params).fetchone()[0]; rows=c.execute(f"SELECT * FROM {table}"+(" WHERE "+where if where else "")+" ORDER BY "+("generated_as_of" if table=="operator_snapshots" else "occurred_at" if table=="operator_events" else "latest_observed_at")+" DESC, rowid DESC LIMIT ? OFFSET ?",(*params,page_size,(page-1)*page_size)).fetchall(); return [dict(x) for x in rows],total
    finally:c.close()
def list_snapshots(database, *, status=None, page=1, page_size=25): return _list(database,"operator_snapshots",where="operational_status=?" if status else "",params=(status,) if status else (),page=page,page_size=page_size)
def list_events(database, *, event_type=None, severity=None, page=1, page_size=25):
    w=[];p=[]
    if event_type:w.append("event_type=?");p.append(event_type)
    if severity:w.append("severity=?");p.append(severity)
    return _list(database,"operator_events",where=" AND ".join(w),params=tuple(p),page=page,page_size=page_size)
def list_incidents(database, *, incident_type=None, severity=None, page=1, page_size=25):
    w=[];p=[]
    if incident_type:w.append("incident_type=?");p.append(incident_type)
    if severity:w.append("severity=?");p.append(severity)
    return _list(database,"operator_incidents",where=" AND ".join(w),params=tuple(p),page=page,page_size=page_size)
def _get(database,table,key,value):
    c=_conn(database,readonly=True)
    try:
        r=c.execute(f"SELECT * FROM {table} WHERE {key}=?",(value,)).fetchone()
        if not r: raise OperatorAppStoreError("record not found")
        return dict(r)
    finally:c.close()
def get_snapshot(d,i):return _get(d,"operator_snapshots","snapshot_id",i)
def get_event(d,i):return _get(d,"operator_events","event_id",i)
def get_incident(d,i):return _get(d,"operator_incidents","incident_id",i)
def verify_store_integrity(database):
    c=_conn(database,readonly=True)
    try:return not c.execute("PRAGMA integrity_check").fetchone()[0] != "ok" and c.execute("SELECT count(*) FROM operator_events e LEFT JOIN operator_snapshots s ON s.snapshot_id=e.source_snapshot_id WHERE e.source_snapshot_id IS NOT NULL AND s.snapshot_id IS NULL").fetchone()[0]==0
    finally:c.close()
