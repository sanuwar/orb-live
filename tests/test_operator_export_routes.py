from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from scripts.create_operator_demo import create_demo
from src.operator_app_store import compute_operator_content_digest, inspect_store_content
from src.operator_framework import ApplicationRoute, normalize_route_path
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig


AS_OF = datetime(2026, 8, 5, 16, tzinfo=timezone.utc)


def _request(app, path: str, method: str = "GET") -> tuple[str, dict[str, str], bytes]:
    environ = {}
    setup_testing_defaults(environ)
    request_path, _, query = path.partition("?")
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": request_path, "QUERY_STRING": query})
    status: list[str] = []
    headers: list[tuple[str, str]] = []
    body = b"".join(app(environ, lambda value, fields: (status.append(value), headers.extend(fields))))
    return status[0], dict(headers), body


def _app(tmp_path: Path) -> tuple[OperatorWebApp, dict]:
    demo = create_demo(tmp_path / "demo", AS_OF)
    app = OperatorWebApp(
        OperatorWebAppConfig(
            runtime_root=Path(demo["root"]),
            database_path=Path(demo["database"]),
            backend_mode="SQLITE_READ_MODEL",
            expected_content_digest=demo["expected_content_digest"],
            default_as_of=AS_OF,
        )
    )
    return app, demo


def test_embedded_export_parameters_match_and_execute_generated_links(tmp_path: Path) -> None:
    app, demo = _app(tmp_path)
    before_digest = compute_operator_content_digest(Path(demo["database"]))
    before_counts = inspect_store_content(Path(demo["database"]))

    status, _, body = _request(app, "/api/exports")
    assert status == "200 OK"
    items = json.loads(body)["items"]
    assert items
    assert all(item["download_url"].startswith("/exports/") for item in items)

    for item in items:
        url = item["download_url"]
        route = app.route_table.match(url)
        assert route is not None
        params = route.match_params(url)
        assert params is not None
        assert params["kind"] == item["export_type"].lower().replace("_batch", "")
        assert params["id"] == item["source_id"]
        assert not params["id"].endswith(".json")
        assert _request(app, url)[0] == "200 OK"

    snapshot_item = next(item for item in items if item["export_type"] == "SNAPSHOT")
    snapshot_html_url = snapshot_item["download_url"].removesuffix(".json") + ".html"
    html_route = app.route_table.match(snapshot_html_url)
    assert html_route is not None
    assert html_route.match_params(snapshot_html_url) == {"kind": "snapshot", "id": snapshot_item["source_id"]}
    assert _request(app, snapshot_html_url)[0] == "200 OK"

    assert _request(app, "/exports/unknown/id.json")[0] == "404 Not Found"
    assert _request(app, "/exports/snapshot/not-present.json")[0] == "404 Not Found"
    snapshot_id = snapshot_item["source_id"]
    assert _request(app, f"/exports/snapshot/{snapshot_id}")[0] == "404 Not Found"
    assert _request(app, f"/exports/snapshot/{snapshot_id}.txt")[0] == "404 Not Found"
    assert _request(app, f"/exports/snapshot/{snapshot_id}.json/extra")[0] == "404 Not Found"
    assert _request(app, f"/exports/snapshot/{snapshot_id}.json", method="POST")[0] == "405 Method Not Allowed"

    assert compute_operator_content_digest(Path(demo["database"])) == before_digest
    assert inspect_store_content(Path(demo["database"])) == before_counts


def test_route_template_literals_are_escaped_and_complete_parameters_remain_supported() -> None:
    route = ApplicationRoute(
        "/files/{file_id}.json",
        ("GET",),
        "files",
        "files",
        "API",
        "API",
        "PUBLIC_OPERATOR_EVIDENCE",
        "NONE",
        False,
        False,
        False,
        False,
        "JSON",
        False,
        False,
        False,
        False,
    ).normalized()
    assert route.match_params("/files/a+b?.json") == {"file_id": "a+b?"}
    assert not route.matches("/files/a+bXjson")

    complete = route.__class__(
        "/items/{item_id}",
        route.methods,
        "items",
        "items",
        route.route_type,
        route.classification,
        route.privacy_classification,
        route.safety_authority,
        route.permits_database_write,
        route.permits_runtime_write,
        route.permits_outbound_network,
        route.exposes_private_data,
        route.response_kind,
        route.requires_store,
        route.supports_file_fixture,
        route.supports_sqlite,
        route.auto_refresh_eligible,
    ).normalized()
    assert complete.match_params("/items/item-1") == {"item_id": "item-1"}


@pytest.mark.parametrize("path", ("/files/{file_id", "/files/{bad-name}.json", "/files/{id}/{id}"))
def test_malformed_or_ambiguous_parameter_templates_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        normalize_route_path(path)
