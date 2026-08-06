import json, sqlite3, pytest
from datetime import datetime, timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_import_history import list_operator_imports
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
AS_OF=datetime(2026,8,6,tzinfo=timezone.utc)
def app(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); return db,OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL",database_path=db))
def request(app,path):
    out={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":""},lambda s,h:out.update(status=s))); return out["status"],json.loads(body)
def test_empty_import_list_is_exact_and_read_only(tmp_path):
    db,application=app(tmp_path); before=db.read_bytes(); status,payload=request(application,"/api/imports"); assert status=="200 OK" and payload["items"]==[]; assert db.read_bytes()==before
def test_import_detail_invalid_id_is_private_safe(tmp_path):
    _,application=app(tmp_path); out={}; body=b"".join(application({"REQUEST_METHOD":"GET","PATH_INFO":"/api/imports/not-valid","QUERY_STRING":""},lambda s,h:out.update(status=s))); assert out["status"]=="404 Not Found" and b"Traceback" not in body
def test_file_mode_import_history_fails_closed(tmp_path):
    application=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF)); status,body=request(application,"/api/imports"); assert status=="503 Service Unavailable" and "FAIL_CLOSED" in json.dumps(body)
def test_invalid_store_status_is_not_consistent(tmp_path):
    db=tmp_path/"operator.sqlite"; sqlite3.connect(db).close()
    with pytest.raises(Exception): list_operator_imports(db)
