"""Reproducible, disposable read-only operator milestone verification."""
from __future__ import annotations
import hashlib, json, re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.operator_app_store import inspect_store_content, operator_content_digest
from src.operator_route_contracts import route_contracts, verify_contracts
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from scripts.create_operator_demo import create_demo

def _request(app, path, query=""):
    result={}; body=b"".join(app({"REQUEST_METHOD":"GET","PATH_INFO":path,"QUERY_STRING":query},lambda status,headers: result.update(status=status))).decode("utf-8")
    if result.get("status") not in {"200 OK","400 Bad Request","404 Not Found","503 Service Unavailable"}: raise RuntimeError("unexpected route status")
    if re.search(r"[A-Za-z]:\\|/dev/|Traceback|SELECT |sqlite3",body,re.I): raise RuntimeError("privacy leakage")
    return result["status"],body

def main():
    as_of=datetime(2026,8,5,16,tzinfo=timezone.utc)
    with TemporaryDirectory(prefix="orb-live-m12-") as temporary:
        demo=create_demo(Path(temporary)/"demo",as_of); database=Path(demo["database"])
        app=OperatorWebApp(OperatorWebAppConfig(runtime_root=Path(demo["root"]),database_path=database,backend_mode="SQLITE_READ_MODEL",default_as_of=as_of))
        before_digest=operator_content_digest(database); before_counts=inspect_store_content(database)
        paths=[x.route for x in route_contracts() if "{" not in x.route and x.route not in {"/api/snapshot"}]
        statuses={path:_request(app,path)[0] for path in paths}
        for metric in ("blocker_occurrences","active_incidents","event_count","stale_evidence","missing_evidence","import_count"):
            for bucket in ("HOUR","DAY"): _request(app,"/api/trends",f"metric={metric}&bucket={bucket}")
        for preset in ("/views","/api/views","/freshness","/api/freshness","/api/release","/api/data-status","/api/diagnostics"): _request(app,preset)
        after_digest=operator_content_digest(database); after_counts=inspect_store_content(database)
        if before_digest!=after_digest or before_counts!=after_counts: raise RuntimeError("GET mutated read model")
        if not all(not row["permits_database_write"] and not row["permits_outbound_network"] and not row["exposes_private_data"] for row in verify_contracts()): raise RuntimeError("route safety contract mismatch")
        report={"status":"PASS","demo":"deterministic synthetic SQLite read model","route_count":len(paths),"contract_route_count":len(verify_contracts()),"route_statuses":statuses,"trend_metrics":6,"trend_bucket_modes":["HOUR","DAY"],"before_database_content_digest":before_digest,"after_database_content_digest":after_digest,"before_row_counts":before_counts,"after_row_counts":after_counts,"implementation_digest_scope":"SOURCE_CHECKPOINT_CLOSURE","read_model_authority":"NON_AUTHORITATIVE","safety":{"strategy_enabled":False,"activation_approved":False,"permits_automated_action":False,"live_ready":False,"unattended_ready":False},"no_broker":True,"no_outbound_network":True,"no_private_data":True}
        print(json.dumps(report,sort_keys=True,separators=(",",":")))
    return 0

if __name__=="__main__": raise SystemExit(main())
