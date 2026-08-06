import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_store_status import read_store_status
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
AS_OF=datetime(2026,8,6,tzinfo=timezone.utc)
def make(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); return db
def call(app,path):
    out={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":""},lambda s,h:out.update(status=s))); return out["status"],json.loads(body)
def test_empty_store_status(tmp_path):
    db=make(tmp_path); s=read_store_status(db); assert s.status=="EMPTY" and s.database_mode=="READ_MODEL_ONLY" and not s.live_ready
def test_status_api_fixed_fields_and_no_path(tmp_path):
    db=make(tmp_path); app=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL",database_path=db)); before=db.read_bytes(); status,data=call(app,"/api/data-status"); assert status=="200 OK"; assert data["runtime_authority"]=="EXTERNAL" and data["rebuildable"] is True and "database_path" not in data; assert db.read_bytes()==before
def test_status_html_is_privacy_safe(tmp_path):
    db=make(tmp_path); app=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL",database_path=db)); out={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/data-status","QUERY_STRING":""},lambda s,h:out.update(status=s))).decode(); assert "operator.sqlite" not in body and "READ_MODEL_ONLY" in body and "broker" in body
def test_invalid_store_fails_closed(tmp_path):
    db=tmp_path/"operator.sqlite"; sqlite3.connect(db).close(); s=read_store_status(db); assert s.status=="UNKNOWN_FAIL_CLOSED" and s.blocker_reasons
def test_file_mode_status_fails_closed(tmp_path):
    from src.operator_web_app import OperatorWebAppConfig
    app=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF)); status,data=call(app,"/api/data-status"); assert status=="200 OK" and data["status"]=="UNKNOWN_FAIL_CLOSED"
