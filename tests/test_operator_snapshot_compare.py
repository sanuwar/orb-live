from types import SimpleNamespace
from src.operator_snapshot_compare import compare_snapshot_items
def item(data): return SimpleNamespace(snapshot_id=data["id"],snapshot=SimpleNamespace(to_dict=lambda:data))
def test_comparison_is_observational_and_closed():
    base={"id":"left","generated_as_of":"2026-08-05T12:00:00+00:00","release_commit_sha":"a"*40,"implementation_digest":"b"*64,"operational_status":"A","supervised_readiness_status":"R","recommended_operator_action":"X","evidence_completeness":"COMPLETE","evidence_freshness":"CURRENT","suspension_status":"CLEAR","heartbeat_status":"CURRENT","alert_status":"CONFIRMED","acknowledgment_status":"COMPLETE","recovery_status":"NONE","blocker_reasons":[],"source_component_statuses":{"scheduler":"AVAILABLE"},"strategy_enabled":False,"activation_approvals_present":False,"protection_grounded":False,"activation_approved":False,"permits_automated_action":False,"live_ready":False,"unattended_ready":False}
    right=dict(base,id="right",generated_as_of="2026-08-05T13:00:00+00:00",operational_status="B",blocker_reasons=["REVIEW"])
    out=compare_snapshot_items(item(base),item(right)).to_dict(); assert out["chronological_order"]=="LEFT_BEFORE_RIGHT" and out["blockers_added"]==["REVIEW"] and out["permits_automated_action"] is False
