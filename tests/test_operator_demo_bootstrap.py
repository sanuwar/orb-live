from datetime import datetime
from pathlib import Path
import json, pytest
from scripts.create_operator_demo import create_demo

AS_OF=datetime.fromisoformat("2026-08-05T16:00:00+00:00")
def test_demo_is_deterministic_and_safe(tmp_path):
    one=create_demo(tmp_path/"one",AS_OF); two=create_demo(tmp_path/"two",AS_OF)
    assert (one["snapshot_count"],one["event_count"],one["incident_count"]) == (6,one["event_count"],one["incident_count"])
    assert one["event_count"] >= 2 and one["incident_count"] >= 2
    assert json.loads((tmp_path/"one"/".operator_demo_owned").read_text())["tool_id"] == "orb-live.operator-demo.v1"
    assert one["import_batch_id"] == two["import_batch_id"]
def test_demo_rejects_unowned_and_public_roots(tmp_path):
    root=tmp_path/"existing"; root.mkdir(); (root/"user.txt").write_text("x")
    with pytest.raises(ValueError): create_demo(root,AS_OF)
