from types import SimpleNamespace
from src.operator_evidence_export import catalog_event,catalog_snapshot
def test_export_catalog_is_allowlisted_and_read_only():
    event=SimpleNamespace(event_id="event-1",occurred_at="2026-08-05T12:00:00Z",headline="Review required")
    data=catalog_event(event).to_dict(); assert data["privacy_classification"]=="OPERATOR_SAFE" and data["read_only"] is True and data["permits_automated_action"] is False
