import json
from datetime import datetime, timezone
from pathlib import Path
from src.operator_app_diagnostics import read_app_diagnostics
from src.operator_app_store import initialize_store
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig

def test_diagnostics_projection_is_closed_and_privacy_safe(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd()))
    config=OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=datetime(2026,8,5,16,tzinfo=timezone.utc),backend_mode="SQLITE_READ_MODEL",database_path=db)
    data=read_app_diagnostics(config,config.default_as_of.isoformat()).to_dict()
    assert data["application_status"]=="INVALID_CONFIGURATION" and data["integrity_status"]=="CONTENT_UNVERIFIED" and data["runtime_authority"]=="EXTERNAL" and data["read_model_authority"]=="NON_AUTHORITATIVE"
    assert data["permits_automated_action"] is False and data["live_ready"] is False and "database_path" not in data

def test_diagnostics_api_and_html_are_read_only(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd()))
    app=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path,default_as_of=datetime(2026,8,5,16,tzinfo=timezone.utc),backend_mode="SQLITE_READ_MODEL",database_path=db)); before=db.read_bytes(); out=[]
    body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/api/diagnostics","QUERY_STRING":""},lambda s,h:out.append(s)))
    assert out[0]=="200 OK" and json.loads(body)["external_resources_enabled"] is False and db.read_bytes()==before
