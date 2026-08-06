from src.operator_view_presets import list_presets,get_preset
from datetime import datetime, timezone
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
def test_presets_are_stable_relative_and_closed():
    values=list_presets(); assert [x.preset_id for x in values]==["current-blockers","active-incidents","stale-evidence","missing-evidence","recent-activity","recent-imports","fail-closed-status","recent-high-severity-events","incomplete-evidence","integrity-review"]
    assert all(x.target_route.startswith("/") and not x.target_route.startswith("//") and x.to_dict()["read_only"] for x in values)
    assert get_preset("active-incidents").to_dict()["permits_automated_action"] is False

def test_view_catalog_routes_are_read_only(tmp_path):
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path, default_as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc)))
    for path in ("/views", "/api/views"):
        result = {}
        body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": ""}, lambda status, headers: result.update(status=status))).decode()
        assert result["status"] == "200 OK" and "LIVE_READY" not in body and "https://" not in body
