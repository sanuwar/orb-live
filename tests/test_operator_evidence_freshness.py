from datetime import datetime,timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_evidence_freshness import summarize_freshness
from src.read_only_authorization_manifest import checkpoint_implementation_digest,resolve_head_sha
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from scripts.create_operator_demo import create_demo
def test_empty_freshness_fails_closed(tmp_path):
    db=tmp_path/"operator.sqlite"; initialize_store(db,release_commit_sha=resolve_head_sha(Path.cwd()),implementation_digest=checkpoint_implementation_digest(Path.cwd())); out=summarize_freshness(db,datetime(2026,8,5,16,tzinfo=timezone.utc).isoformat()).to_dict(); assert out["overall_freshness_status"]=="UNKNOWN_FAIL_CLOSED" and out["live_ready"] is False

def test_sqlite_freshness_routes_are_read_only(tmp_path):
    as_of = datetime(2026, 8, 5, 16, tzinfo=timezone.utc)
    demo = create_demo(tmp_path / "demo", as_of)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path / "demo", default_as_of=as_of, backend_mode="SQLITE_READ_MODEL", database_path=Path(demo["database"])))
    for path in ("/freshness", "/api/freshness"):
        result = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: result.update(status=status))).decode()
        assert result["status"] == "200 OK" and "LIVE_READY" not in body and "database" not in body.casefold()
