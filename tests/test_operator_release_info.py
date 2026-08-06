from pathlib import Path
from src.operator_release_info import read_release_info
from datetime import datetime, timezone
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
def test_release_info_is_privacy_safe_and_closed():
    data=read_release_info(Path.cwd()).to_dict(); assert data["build_identity_status"]=="VERIFIED" and data["pinned_file_count"]==20 and data["live_ready"] is False; assert "path" not in data and "remote_url" not in data

def test_release_routes_do_not_expose_runtime_context(tmp_path):
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path, default_as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc)))
    for path in ("/release", "/api/release"):
        result = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: result.update(status=status))).decode()
        assert result["status"] == "200 OK" and "C:\\dev" not in body and "https://" not in body
