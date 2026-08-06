import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from src.operator_app_store import initialize_store
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig, WebAppConfigError
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha

AS_OF=datetime(2026,8,5,20,tzinfo=timezone.utc)
def app(tmp_path, db):
    initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd()))
    return OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL",database_path=db))
def request(application,path):
    result={}
    def start(status,headers): result["status"]=status; result["headers"]=headers
    body=b"".join(application({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":""},start))
    return result["status"],body
def test_empty_sqlite_history_and_timeline_are_read_only(tmp_path):
    db=tmp_path/"operator.sqlite"; application=app(tmp_path,db); before=db.read_bytes()
    status,body=request(application,"/api/history"); assert status=="200 OK" and json.loads(body)["items"]==[]
    status,body=request(application,"/api/timeline"); assert status=="200 OK" and json.loads(body)["items"]==[]
    assert db.read_bytes()==before
def test_sqlite_mode_requires_explicit_database(tmp_path):
    with pytest.raises(WebAppConfigError): OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL"))
def test_missing_sqlite_fails_closed(tmp_path):
    with pytest.raises(WebAppConfigError): OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="SQLITE_READ_MODEL",database_path=tmp_path/"missing.sqlite"))
def test_invalid_backend_mode_rejected(tmp_path):
    with pytest.raises(WebAppConfigError): OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=AS_OF,backend_mode="AUTO"))
