"""Strict, read-only-query SQLite store for operator projections."""
from __future__ import annotations
import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from src.operator_app_schema import create_schema, SCHEMA_VERSION
from src.offline_path_safety import is_repository_data_path, REPOSITORY_ROOT
from src.supervised_operator_console import SupervisedOperatorConsoleResult
from src.operator_snapshot_history import HistoryItem
from src.operator_event_timeline import OperatorEvent, EventType, EventSeverity
from src.operator_incident_export import OperatorIncident

class OperatorAppStoreError(ValueError): pass
CONTENT_VERIFIED="CONTENT_VERIFIED"
CONTENT_MISMATCH="CONTENT_MISMATCH"
CONTENT_UNVERIFIED="CONTENT_UNVERIFIED"
SCHEMA_INVALID="SCHEMA_INVALID"
IDENTITY_MISMATCH="IDENTITY_MISMATCH"
UNKNOWN_FAIL_CLOSED="UNKNOWN_FAIL_CLOSED"
_SNAP_FIELDS=("snapshot_id","generated_as_of","release_commit_sha","implementation_digest","operating_mode","operational_status","supervised_readiness_status","recommended_operator_action","evidence_completeness","evidence_freshness","suspension_status","heartbeat_status","alert_status","acknowledgment_status","recovery_status","blocker_reasons_json","source_component_statuses_json","strategy_enabled","activation_approvals_present","approved_protection_state_count","protection_grounded","activation_approved","permits_automated_action","live_ready","unattended_ready","canonical_digest","import_batch_id")
_TABLE_FIELDS={
    "schema_metadata": ("singleton_id","schema_version","release_commit_sha","implementation_digest"),
    "import_batches": ("import_batch_id","imported_at","source_release_commit_sha","source_implementation_digest","source_count","accepted_count","rejected_count"),
    "operator_snapshots": tuple(x for x in _SNAP_FIELDS if x != "canonical_digest"),
    "operator_events": ("event_id","event_type","severity","occurred_at","source_snapshot_id","source_component","headline","detail_code","operational_status","supervised_readiness_status","blocker_reasons_json","operator_action","permits_automated_action","live_ready","unattended_ready","import_batch_id"),
    "operator_incidents": ("incident_id","source_event_id","incident_type","severity","opened_at","latest_observed_at","observation_status","source_component","headline","detail_code","blocker_reasons_json","recommended_operator_action","operational_status","supervised_readiness_status","evidence_completeness","evidence_freshness","strategy_enabled","activation_approvals_present","protection_grounded","activation_approved","permits_automated_action","live_ready","unattended_ready","import_batch_id"),
    "incident_evidence_links": ("incident_id","evidence_type","evidence_id","ordinal"),
}
_ROW_DIGEST_COLUMNS={"operator_snapshots":"canonical_digest","operator_events":"canonical_digest","operator_incidents":"canonical_digest","import_batches":"batch_digest"}
_EXPECTED_TABLES=frozenset(_TABLE_FIELDS)
_TABLE_COLUMNS={
    "schema_metadata": ("singleton_id","schema_version","created_at","release_commit_sha","implementation_digest","content_digest"),
    "import_batches": ("import_batch_id","imported_at","source_release_commit_sha","source_implementation_digest","source_count","accepted_count","rejected_count","batch_digest"),
    "operator_snapshots": _SNAP_FIELDS,
    "operator_events": ("event_id","event_type","severity","occurred_at","source_snapshot_id","source_component","headline","detail_code","operational_status","supervised_readiness_status","blocker_reasons_json","operator_action","permits_automated_action","live_ready","unattended_ready","canonical_digest","import_batch_id"),
    "operator_incidents": ("incident_id","source_event_id","incident_type","severity","opened_at","latest_observed_at","observation_status","source_component","headline","detail_code","blocker_reasons_json","recommended_operator_action","operational_status","supervised_readiness_status","evidence_completeness","evidence_freshness","strategy_enabled","activation_approvals_present","protection_grounded","activation_approved","permits_automated_action","live_ready","unattended_ready","canonical_digest","import_batch_id"),
    "incident_evidence_links": ("incident_id","evidence_type","evidence_id","ordinal"),
}
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
            if row["schema_version"] != 1 or "content_digest" not in row.keys(): raise OperatorAppStoreError("unknown schema version")
            if row["release_commit_sha"] != release_commit_sha or row["implementation_digest"] != implementation_digest: raise OperatorAppStoreError("store identity conflicts")
        else: create_schema(c, release_commit_sha=release_commit_sha, implementation_digest=implementation_digest)
        content_digest=_canonical_content_digest(c)
        c.execute("UPDATE schema_metadata SET content_digest=? WHERE singleton_id=1", (content_digest,))
        c.commit()
        return content_digest
    except Exception: c.rollback(); raise
    finally:c.close()
def inspect_store_identity(database):
    c=_conn(database,readonly=True)
    try:return dict(c.execute("SELECT * FROM schema_metadata").fetchone())
    finally:c.close()
def _digest(kind, data): return hashlib.sha256((kind+"\0"+json.dumps(data,sort_keys=True,separators=(",",":"))).encode()).hexdigest()

def _canonical_value(value):
    if isinstance(value, bytes): return {"type":"bytes","value":value.hex()}
    if value is None: return {"type":"null","value":None}
    if isinstance(value, bool): return {"type":"bool","value":value}
    if isinstance(value, int): return {"type":"int","value":value}
    if isinstance(value, float): return {"type":"float","value":value}
    return {"type":"text","value":str(value)}

def _row_payload(table, row):
    columns=_TABLE_FIELDS[table]
    return {"table":table,"identity":_canonical_value(row[columns[0]]),"fields":[[name,_canonical_value(row[name])] for name in columns]}

def _row_digest(table, row): return _digest("operator-row", _row_payload(table,row))

def _canonical_content_digest(connection):
    content={"schema_version":SCHEMA_VERSION,"tables":[]}
    for table in sorted(_EXPECTED_TABLES):
        rows=connection.execute("SELECT * FROM "+table).fetchall()
        payloads=[]
        for row in rows:
            d=dict(row)
            if table=="schema_metadata": d={k:d[k] for k in _TABLE_FIELDS[table]}
            else: d={k:d[k] for k in _TABLE_FIELDS[table]}
            payloads.append(_row_payload(table,d))
        payloads.sort(key=lambda x: json.dumps(x["identity"],sort_keys=True,separators=(",",":")))
        content["tables"].append({"table":table,"fields":list(_TABLE_FIELDS[table]),"rows":payloads})
    return _digest("operator-content",content)

def _validate_timestamp(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError): raise OperatorAppStoreError("invalid read-model timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise OperatorAppStoreError("invalid read-model timestamp")

def _validate_json(value, expected):
    try: parsed=json.loads(value)
    except (TypeError,ValueError): raise OperatorAppStoreError("invalid read-model JSON") from None
    if not isinstance(parsed, expected): raise OperatorAppStoreError("invalid read-model JSON")
    return parsed

def _validate_operator_content(connection, *, check_content_digest=True):
    tables={r["name"] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != _EXPECTED_TABLES: raise OperatorAppStoreError("unsupported read-model schema")
    for table, fields in _TABLE_FIELDS.items():
        actual=tuple(r["name"] for r in connection.execute("PRAGMA table_info("+table+")"))
        expected=_TABLE_COLUMNS[table]
        if actual != expected: raise OperatorAppStoreError("unsupported read-model table shape")
    meta=connection.execute("SELECT * FROM schema_metadata").fetchall()
    if len(meta)!=1 or meta[0]["schema_version"]!=SCHEMA_VERSION or not meta[0]["content_digest"]: raise OperatorAppStoreError("read-model identity unavailable")
    for table in ("import_batches","operator_snapshots","operator_events","operator_incidents"):
        rows=connection.execute("SELECT * FROM "+table).fetchall()
        identities=set()
        for row in rows:
            d=dict(row); identity=d[_TABLE_FIELDS[table][0]]
            if identity in identities: raise OperatorAppStoreError("duplicate read-model identity")
            identities.add(identity)
            if table=="import_batches":
                for key in ("imported_at",): _validate_timestamp(d[key])
                if any(type(d[k]) is not int or d[k] < 0 for k in ("source_count","accepted_count","rejected_count")): raise OperatorAppStoreError("invalid read-model count")
            elif table=="operator_snapshots":
                _validate_timestamp(d["generated_as_of"]); _validate_json(d["blocker_reasons_json"],list); _validate_json(d["source_component_statuses_json"],dict)
            elif table=="operator_events":
                _validate_timestamp(d["occurred_at"]); _validate_json(d["blocker_reasons_json"],list)
            else:
                _validate_timestamp(d["opened_at"]); _validate_timestamp(d["latest_observed_at"]); _validate_json(d["blocker_reasons_json"],list)
            if table != "import_batches" and any(d[k] != 0 for k in ("permits_automated_action","live_ready","unattended_ready")): raise OperatorAppStoreError("unsafe read-model invariant")
            if table=="operator_snapshots" and any(d[k] != 0 for k in ("strategy_enabled","activation_approvals_present","approved_protection_state_count","protection_grounded","activation_approved")): raise OperatorAppStoreError("unsafe read-model invariant")
            if table=="operator_incidents" and any(d[k] != 0 for k in ("strategy_enabled","activation_approvals_present","protection_grounded","activation_approved")): raise OperatorAppStoreError("unsafe read-model invariant")
            if d[_ROW_DIGEST_COLUMNS[table]] != _row_digest(table,d): raise OperatorAppStoreError("read-model row digest mismatch")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None: raise OperatorAppStoreError("read-model relationship mismatch")
    if check_content_digest and _canonical_content_digest(connection) != meta[0]["content_digest"]: raise OperatorAppStoreError("read-model content digest mismatch")

def compute_operator_content_digest(database):
    c=_conn(database,readonly=True)
    try:
        _validate_operator_content(c,check_content_digest=False)
        return _canonical_content_digest(c)
    finally:c.close()

def read_model_integrity_status(database, *, expected_content_digest=None, expected_release_commit_sha=None):
    c=None
    try:
        c=_conn(database,readonly=True)
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok": return UNKNOWN_FAIL_CLOSED
        _validate_operator_content(c,check_content_digest=False)
        meta=dict(c.execute("SELECT * FROM schema_metadata WHERE singleton_id=1").fetchone() or {})
        if expected_release_commit_sha is not None and meta.get("release_commit_sha") != expected_release_commit_sha: return IDENTITY_MISMATCH
        if expected_content_digest is None: return CONTENT_UNVERIFIED
        if not isinstance(expected_content_digest,str) or len(expected_content_digest)!=64:
            return CONTENT_MISMATCH
        try: int(expected_content_digest,16)
        except ValueError: return CONTENT_MISMATCH
        computed=_canonical_content_digest(c)
        return CONTENT_VERIFIED if computed == expected_content_digest and meta.get("content_digest") == computed else CONTENT_MISMATCH
    except OperatorAppStoreError as exc:
        return SCHEMA_INVALID if "schema" in str(exc) or "shape" in str(exc) or "identity unavailable" in str(exc) else CONTENT_MISMATCH
    except (sqlite3.DatabaseError,TypeError,ValueError,KeyError,OSError):
        return UNKNOWN_FAIL_CLOSED
    finally:
        if c is not None: c.close()

def operator_content_digest(database):
    c=_conn(database,readonly=True)
    try:
        _validate_operator_content(c)
        return c.execute("SELECT content_digest FROM schema_metadata WHERE singleton_id=1").fetchone()[0]
    finally:c.close()

def list_trend_records(database, metric):
    queries={
        "event_count": ("SELECT occurred_at, 1 AS value FROM operator_events ORDER BY occurred_at ASC, event_id ASC", lambda r:int(r["value"])),
        "blocker_occurrences": ("SELECT occurred_at, blocker_reasons_json FROM operator_events ORDER BY occurred_at ASC, event_id ASC", lambda r:len(_validate_json(r["blocker_reasons_json"],list))),
        "active_incidents": ("SELECT latest_observed_at, observation_status FROM operator_incidents ORDER BY latest_observed_at ASC, incident_id ASC", lambda r:1 if r["observation_status"]=="ACTIVE" else 0),
        "stale_evidence": ("SELECT generated_as_of, evidence_freshness FROM operator_snapshots ORDER BY generated_as_of ASC, snapshot_id ASC", lambda r:1 if r["evidence_freshness"]=="STALE" else 0),
        "missing_evidence": ("SELECT generated_as_of, evidence_completeness FROM operator_snapshots ORDER BY generated_as_of ASC, snapshot_id ASC", lambda r:1 if r["evidence_completeness"]!="COMPLETE" else 0),
        "import_count": ("SELECT imported_at, 1 AS value FROM import_batches ORDER BY imported_at ASC, import_batch_id ASC", lambda r:int(r["value"])),
    }
    if metric not in queries: raise OperatorAppStoreError("trend metric is invalid")
    sql, value=queries[metric]; c=_conn(database,readonly=True)
    try:
        rows=c.execute(sql).fetchall(); return tuple((r[0],value(r)) for r in rows)
    finally:c.close()
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
            row=dict(c.execute("SELECT * FROM operator_snapshots WHERE snapshot_id=?",(d["snapshot_id"],)).fetchone()); c.execute("UPDATE operator_snapshots SET canonical_digest=? WHERE snapshot_id=?",(_row_digest("operator_snapshots",row),d["snapshot_id"]))
        for e in events:
            d=e.to_dict(); ed=_digest("event",d); vals=(d["event_id"],d["event_type"],d["severity"],d["occurred_at"],d["source_snapshot_id"],d["source_component"],d["headline"],d["detail_code"],d["operational_status"],d["supervised_readiness_status"],json.dumps(d["blocker_reasons"],sort_keys=True,separators=(",",":")),d["operator_action"],0,0,0,ed,bid); c.execute("INSERT INTO operator_events VALUES ("+",".join("?"*len(vals))+")",vals); row=dict(c.execute("SELECT * FROM operator_events WHERE event_id=?",(d["event_id"],)).fetchone()); c.execute("UPDATE operator_events SET canonical_digest=? WHERE event_id=?",(_row_digest("operator_events",row),d["event_id"]))
        for i in incidents:
            d=i.to_dict(); idig=_digest("incident",d); vals=(d["incident_id"],d["source_event_id"],d["incident_type"],d["severity"],d["opened_at"],d["latest_observed_at"],d["active"],d["source_component"],d["headline"],d["detail_code"],json.dumps(d["blocker_reasons"],sort_keys=True,separators=(",",":")),d["recommended_operator_action"],d["operational_status"],d["supervised_readiness_status"],d["evidence_completeness"],d["evidence_freshness"],0,0,0,0,0,0,0,idig,bid); c.execute("INSERT INTO operator_incidents VALUES ("+",".join("?"*len(vals))+")",vals); row=dict(c.execute("SELECT * FROM operator_incidents WHERE incident_id=?",(d["incident_id"],)).fetchone()); c.execute("UPDATE operator_incidents SET canonical_digest=? WHERE incident_id=?",(_row_digest("operator_incidents",row),d["incident_id"]))
            for n,eid in enumerate(d["supporting_event_ids"]): c.execute("INSERT INTO incident_evidence_links VALUES (?,?,?,?)",(d["incident_id"],"event",eid,n))
            for n,sid in enumerate(d["supporting_snapshot_ids"],len(d["supporting_event_ids"])): c.execute("INSERT INTO incident_evidence_links VALUES (?,?,?,?)",(d["incident_id"],"snapshot",sid,n))
        batch_row=dict(c.execute("SELECT * FROM import_batches WHERE import_batch_id=?",(bid,)).fetchone())
        c.execute("UPDATE import_batches SET batch_digest=? WHERE import_batch_id=?",(_row_digest("import_batches",batch_row),bid))
        c.execute("UPDATE schema_metadata SET content_digest=? WHERE singleton_id=1", (_canonical_content_digest(c),))
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

def list_all_incidents(database, *, incident_type=None, severity=None):
    w=[]; p=[]
    if incident_type: w.append("incident_type=?"); p.append(incident_type)
    if severity: w.append("severity=?"); p.append(severity)
    c=_conn(database,readonly=True)
    try: return [dict(x) for x in c.execute("SELECT * FROM operator_incidents"+(" WHERE "+" AND ".join(w) if w else "")+" ORDER BY latest_observed_at DESC, rowid DESC",tuple(p)).fetchall()]
    finally: c.close()

def list_all_table(database, table):
    if table not in {"operator_snapshots","operator_events","operator_incidents"}: raise OperatorAppStoreError("table is invalid")
    c=_conn(database,readonly=True)
    try: return [dict(x) for x in c.execute("SELECT * FROM "+table+" ORDER BY rowid DESC").fetchall()]
    finally: c.close()
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
def get_incident_projection(d,i):
    c=_conn(d,readonly=True)
    try:
        row=dict(c.execute("SELECT * FROM operator_incidents WHERE incident_id=?",(i,)).fetchone() or {})
        if not row: raise OperatorAppStoreError("record not found")
        links=c.execute("SELECT evidence_type,evidence_id,ordinal FROM incident_evidence_links WHERE incident_id=? ORDER BY ordinal",(i,)).fetchall()
        row["supporting_snapshot_ids"]=[x["evidence_id"] for x in links if x["evidence_type"]=="snapshot"]; row["supporting_event_ids"]=[x["evidence_id"] for x in links if x["evidence_type"]=="event"]; return incident_projection(row)
    finally:c.close()
def snapshot_projection(row):
    d=dict(row); components=json.loads(d["source_component_statuses_json"]); blockers=json.loads(d["blocker_reasons_json"])
    raw={"schema_version":1,"release_commit_sha":d["release_commit_sha"],"implementation_digest":d["implementation_digest"],"generated_as_of":d["generated_as_of"],"operating_mode":d["operating_mode"],"scheduler_status":components["scheduler"],"runner_status":components["runner"],"suspension_status":d["suspension_status"],"heartbeat_status":d["heartbeat_status"],"alert_status":d["alert_status"],"acknowledgment_status":d["acknowledgment_status"],"recovery_status":d["recovery_status"],"operational_status":d["operational_status"],"supervised_readiness_status":d["supervised_readiness_status"],"blocker_reasons":blockers,"recommended_operator_action":d["recommended_operator_action"],"strategy_enabled":False,"activation_approvals_present":False,"approved_protection_state_count":0,"protection_grounded":False,"activation_approved":False,"permits_automated_action":False,"live_ready":False,"unattended_ready":False,"evidence_completeness":d["evidence_completeness"],"evidence_freshness":d["evidence_freshness"],"source_component_statuses":components}
    return HistoryItem(snapshot_id=d["snapshot_id"],snapshot=SupervisedOperatorConsoleResult.from_dict(raw))
def event_projection(row):
    d=dict(row); return OperatorEvent(1,d["event_id"],EventType(d["event_type"]),EventSeverity(d["severity"]),d["occurred_at"],d["source_snapshot_id"],d["source_component"],d["headline"],d["detail_code"],d["operational_status"],d["supervised_readiness_status"],tuple(json.loads(d["blocker_reasons_json"])),d["operator_action"])
def incident_projection(row):
    d=dict(row); return OperatorIncident(1,d["incident_id"],d["source_event_id"],d["incident_type"],d["severity"],d["opened_at"],d["latest_observed_at"],d["observation_status"],d["source_component"],d["headline"],d["detail_code"],tuple(json.loads(d["blocker_reasons_json"])),d["recommended_operator_action"],tuple(d.get("supporting_snapshot_ids",())),tuple(d.get("supporting_event_ids",())),d["operational_status"],d["supervised_readiness_status"],d["evidence_completeness"],d["evidence_freshness"])
def verify_store_integrity(database, expected_content_digest=None):
    return read_model_integrity_status(database,expected_content_digest=expected_content_digest)==CONTENT_VERIFIED
def inspect_store_content(database):
    c=_conn(database,readonly=True)
    try:
        def one(sql): return c.execute(sql).fetchone()[0]
        return {"import_batch_count":one("SELECT count(*) FROM import_batches"),"snapshot_count":one("SELECT count(*) FROM operator_snapshots"),"event_count":one("SELECT count(*) FROM operator_events"),"incident_count":one("SELECT count(*) FROM operator_incidents"),"evidence_link_count":one("SELECT count(*) FROM incident_evidence_links"),"newest_imported_at":one("SELECT max(imported_at) FROM import_batches"),"newest_snapshot_at":one("SELECT max(generated_as_of) FROM operator_snapshots"),"newest_event_at":one("SELECT max(occurred_at) FROM operator_events"),"newest_incident_at":one("SELECT max(latest_observed_at) FROM operator_incidents")}
    finally:c.close()
def list_import_batches(database, *, page=1, page_size=25, where="", params=()):
    if type(page)!=int or page<1 or type(page_size)!=int or not 1<=page_size<=100: raise OperatorAppStoreError("pagination is invalid")
    c=_conn(database,readonly=True)
    try:
        clause=(" WHERE "+where) if where else ""; total=c.execute("SELECT count(*) FROM import_batches"+clause,params).fetchone()[0]; rows=c.execute("SELECT * FROM import_batches"+clause+" ORDER BY imported_at DESC, import_batch_id DESC LIMIT ? OFFSET ?",(*params,page_size,(page-1)*page_size)).fetchall(); return [dict(x) for x in rows],total
    finally:c.close()
def get_import_batch(database, batch_id):
    c=_conn(database,readonly=True)
    try:
        row=c.execute("SELECT * FROM import_batches WHERE import_batch_id=?",(batch_id,)).fetchone()
        if not row: raise OperatorAppStoreError("record not found")
        d=dict(row); d["snapshot_count"]=c.execute("SELECT count(*) FROM operator_snapshots WHERE import_batch_id=?",(batch_id,)).fetchone()[0]; d["event_count"]=c.execute("SELECT count(*) FROM operator_events WHERE import_batch_id=?",(batch_id,)).fetchone()[0]; d["incident_count"]=c.execute("SELECT count(*) FROM operator_incidents WHERE import_batch_id=?",(batch_id,)).fetchone()[0]; d["evidence_link_count"]=c.execute("SELECT count(*) FROM incident_evidence_links l JOIN operator_incidents i ON i.incident_id=l.incident_id WHERE i.import_batch_id=?",(batch_id,)).fetchone()[0]; return d
    finally:c.close()
