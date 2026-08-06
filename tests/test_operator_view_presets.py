import pytest
from src.operator_view_presets import ViewPreset, list_presets,get_preset, validate_preset_routes
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

def test_preset_api_and_html_use_same_encoded_url(tmp_path):
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path, default_as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc)))
    result={}; api=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/api/views","QUERY_STRING":""},lambda status,headers: result.update(status=status))).decode()
    assert result["status"]=="200 OK" and '"url":"/incidents?active_only=true"' in api
    result={}; html=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/views","QUERY_STRING":""},lambda status,headers: result.update(status=status))).decode()
    assert 'href="/incidents?active_only=true"' in html

def test_all_presets_exercise_actual_target_parsers(tmp_path):
    from scripts.create_operator_demo import create_demo
    as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc)
    demo=create_demo(tmp_path / "demo", as_of)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path / "demo", database_path=demo["database"], backend_mode="SQLITE_READ_MODEL", expected_content_digest=demo["expected_content_digest"], default_as_of=as_of))
    results=validate_preset_routes(app)
    assert len(results)==10 and all(x["status"] for x in results)

def test_preset_drift_is_rejected_independently():
    bad=ViewPreset("bad","Bad","Bad","/removed",(),"TEST")
    app=object()
    import pytest
    with pytest.raises(ValueError): validate_preset_routes(app,(bad,))

def test_preset_parser_negative_controls(tmp_path):
    from dataclasses import replace
    from scripts.create_operator_demo import create_demo
    as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc); demo=create_demo(tmp_path / "demo", as_of)
    app=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path / "demo", database_path=demo["database"], backend_mode="SQLITE_READ_MODEL", expected_content_digest=demo["expected_content_digest"], default_as_of=as_of))
    for bad in (
        replace(list_presets()[0], target_route="/renamed"),
        replace(list_presets()[0], query_values=(("activeOnly", "true"),)),
        replace(list_presets()[0], query_values=(("active_only", "invalid"),)),
        replace(list_presets()[0], target_route="https://external.invalid/view"),
        replace(list_presets()[0], target_route="/absolute/view"),
    ):
        with pytest.raises(ValueError): validate_preset_routes(app, (bad,))

    app.config = replace(app.config, refresh_interval=15)
    with pytest.raises(ValueError): validate_preset_routes(app)

def test_silent_parser_drop_is_rejected(tmp_path):
    from scripts.create_operator_demo import create_demo
    as_of=datetime(2026, 8, 5, 16, tzinfo=timezone.utc); demo=create_demo(tmp_path / "demo", as_of)
    real=OperatorWebApp(OperatorWebAppConfig(runtime_root=tmp_path / "demo", database_path=demo["database"], backend_mode="SQLITE_READ_MODEL", expected_content_digest=demo["expected_content_digest"], default_as_of=as_of))
    class AlwaysSuccess:
        route_table=real.route_table; config=real.config
        def __call__(self, environ, start_response):
            start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")]); return [b"accepted"]
    with pytest.raises(ValueError): validate_preset_routes(AlwaysSuccess())
