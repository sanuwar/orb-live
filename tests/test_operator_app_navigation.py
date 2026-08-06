from datetime import datetime, timezone
from pathlib import Path
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from test_supervised_operator_console import AS_OF, _fixture
def test_current_dashboard_has_read_only_navigation(tmp_path):
    app=OperatorWebApp(OperatorWebAppConfig(runtime_root=_fixture(tmp_path / "runtime"),default_as_of=AS_OF))
    result={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/","QUERY_STRING":""},lambda s,h:result.update(status=s))).decode()
    assert result["status"]=="200 OK" and 'aria-label="Primary"' in body and 'aria-current="page"' in body and 'href="/history"' in body
def test_navigation_has_no_external_assets_or_safety_authority(tmp_path):
    app=OperatorWebApp(OperatorWebAppConfig(runtime_root=_fixture(tmp_path / "runtime"),default_as_of=AS_OF))
    out={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":"/","QUERY_STRING":""},lambda s,h:out.update(status=s))).decode()
    assert "http://" not in body and "https://" not in body and "LIVE_READY" not in body and "account number" not in body.casefold()
