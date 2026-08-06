from dataclasses import replace

import pytest

from src.operator_framework import ApplicationRouteTable
from src.operator_route_contracts import construct_operator_application, discover_application_routes, route_contracts, route_matches, verify_contracts


def _app():
    return construct_operator_application()


def _safe_route(app, *, path="/injected", name="operator.injected"):
    return replace(app.route_table.routes[0], path=path, name=name, endpoint="operator_web_app:operator.injected")


def test_routes_are_unique_get_only_and_closed():
    rows=verify_contracts(); assert len(rows)==len(route_contracts()); assert all(x["method"]=="GET" and not x["permits_database_write"] and x["safety_authority"]=="NONE" for x in rows)


def test_registry_has_no_absolute_or_mutation_route():
    assert all(not x.route.startswith("http") and (x.route=="/" or not x.route.endswith("/")) for x in route_contracts())


def test_views_route_is_documented():
    assert "/views" in {x.route for x in route_contracts()}


def test_all_detail_and_export_variants_are_declared():
    routes={x.route for x in route_contracts()}
    assert {"/api/snapshot","/api/history/{snapshot_id}","/api/timeline/{event_id}","/api/incidents/{incident_id}","/api/imports/{batch_id}","/exports/{kind}/{id}.json"} <= routes


def test_unknown_route_is_not_contract_eligible():
    assert route_matches("/api/views") and not route_matches("/api/unsafe-write")


def test_application_inventory_is_framework_derived_and_parameterized():
    actual=discover_application_routes(_app())
    assert len(actual)==48
    assert "/api/history/{snapshot_id}" in {route.path for route in actual}
    assert "/exports/{kind}/{id}.json" in {route.path for route in actual}
    assert all(route.methods == ("GET",) and route.name and route.endpoint and route.route_type for route in actual)


@pytest.mark.parametrize("path", ["/undocumented", "/prefix/{item}"])
def test_injected_undocumented_routes_fail(path):
    app=_app(); app.route_table.add(_safe_route(app, path=path, name="operator.injected." + path.strip("/").replace("/", "_")))
    with pytest.raises(ValueError): verify_contracts(app)


def test_injected_undocumented_post_route_fails():
    app=_app(); app.route_table.add(replace(_safe_route(app), path="/undocumented-post", name="operator.undocumented_post", methods=("POST",)))
    with pytest.raises(ValueError): verify_contracts(app)


def test_mounted_route_is_discoverable_and_then_fails_policy():
    app=_app(); child=ApplicationRouteTable(); child.add(replace(app.route_table.routes[0], path="/{item}", name="operator.mounted_item"))
    app.route_table.mount("/mounted", child, name_prefix="mounted", endpoint_prefix="operator_web_app")
    assert "/mounted/{item}" in {route.path for route in discover_application_routes(app)}
    with pytest.raises(ValueError): verify_contracts(app)


def test_duplicate_route_name_fails():
    app=_app(); app.route_table.add(_safe_route(app, path="/another", name=app.route_table.routes[0].name))
    with pytest.raises(ValueError): discover_application_routes(app)


def test_duplicate_path_method_fails():
    app=_app(); app.route_table.add(_safe_route(app, path="/", name="operator.other_dashboard"))
    with pytest.raises(ValueError): discover_application_routes(app)


def test_stale_expected_contract_fails(monkeypatch):
    import src.operator_route_contracts as contracts
    monkeypatch.setattr(contracts, "ROUTES", contracts.ROUTES[:-1])
    with pytest.raises(ValueError): contracts.verify_contracts(_app())


def test_classification_mismatch_fails():
    app=_app(); app.route_table._routes[0]=replace(app.route_table.routes[0], classification="API")
    with pytest.raises(ValueError): verify_contracts(app)


@pytest.mark.parametrize("field", ["privacy_classification", "safety_authority", "permits_database_write", "permits_runtime_write", "permits_outbound_network"])
def test_route_permission_and_privacy_mismatch_fails(field):
    app=_app(); value = "PRIVATE" if field == "privacy_classification" else ("UNSAFE" if field == "safety_authority" else True)
    app.route_table._routes[0]=replace(app.route_table.routes[0], **{field:value})
    with pytest.raises(ValueError): verify_contracts(app)


def test_head_options_static_and_non_get_behavior_are_explicit():
    app=_app()
    def request(method, path):
        result={}; app({"REQUEST_METHOD":method,"PATH_INFO":path,"QUERY_STRING":""}, lambda status, headers: result.update(status=status)); return result["status"]
    assert request("HEAD", "/") == "405 Method Not Allowed"
    assert request("OPTIONS", "/") == "405 Method Not Allowed"
    assert request("POST", "/") == "405 Method Not Allowed"
    assert request("GET", "/static/operator.css") == "404 Not Found"
