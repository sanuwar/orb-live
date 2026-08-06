import pytest
from src.operator_evidence_trends import summarize_trends
def test_trends_are_descriptive_and_bounded():
    out=summarize_trends((("2026-08-05T12:00:00Z",2),("2026-08-05T13:00:00Z",3)),"2026-08-05T14:00:00Z").to_dict(); assert out["latest_count"]==3 and out["descriptive_only"] and out["permits_automated_action"] is False
def test_unknown_metric_rejected():
    with pytest.raises(ValueError): summarize_trends((),"2026-08-05T14:00:00Z",metric="forecast")
