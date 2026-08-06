import pytest
from src.operator_route_contracts import route_contracts,verify_contracts
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
    from src.operator_route_contracts import route_matches
    assert route_matches("/api/views") and not route_matches("/api/unsafe-write")
