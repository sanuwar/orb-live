import pytest
from src.operator_evidence_trends import summarize_trends
from datetime import datetime, timezone
from pathlib import Path
from src.operator_app_store import initialize_store
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
def test_trends_are_descriptive_and_bounded():
    out=summarize_trends((("2026-08-05T12:00:00Z",2),("2026-08-05T13:00:00Z",3)),"2026-08-05T14:00:00Z").to_dict(); assert out["latest_count"]==3 and out["descriptive_only"] and out["permits_automated_action"] is False
def test_unknown_metric_rejected():
    with pytest.raises(ValueError): summarize_trends((),"2026-08-05T14:00:00Z",metric="forecast")

def test_trend_routes_fail_closed_without_external_resources(tmp_path):
    as_of = datetime(2026, 8, 5, 16, tzinfo=timezone.utc)
    database = tmp_path / "operator.sqlite"
    initialize_store(database, release_commit_sha=resolve_head_sha(Path.cwd()), implementation_digest=checkpoint_implementation_digest(Path.cwd()))
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path, default_as_of=as_of, backend_mode="SQLITE_READ_MODEL", database_path=database))
    for path in ("/trends", "/api/trends"):
        result = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: result.update(status=status))).decode()
        assert result["status"] == "200 OK" and "https://" not in body and "LIVE_READY" not in body
