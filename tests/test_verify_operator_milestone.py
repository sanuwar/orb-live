import json
import subprocess
import sys

def test_tracked_milestone_verifier_is_deterministic_and_closed():
    command=[sys.executable,"scripts/verify_operator_milestone.py"]
    first=json.loads(subprocess.check_output(command,text=True))
    second=json.loads(subprocess.check_output(command,text=True))
    assert first==second
    assert first["status"]=="PASS" and first["before_database_content_digest"]==first["after_database_content_digest"]
    assert first["safety"]=={"activation_approved":False,"live_ready":False,"permits_automated_action":False,"strategy_enabled":False,"unattended_ready":False}
