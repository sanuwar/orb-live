import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).with_name("interactive_fake_authorization_harness.py")


def run_harness(tmp_path, case, run_id, supplied=None):
    return subprocess.run(
        [sys.executable, str(HARNESS), "--case", case,
         "--evidence-root", str(tmp_path), "--run-id", run_id],
        input=supplied, text=True, capture_output=True, check=False,
    )


def test_invalid_stdin_fails_before_write_and_closes_session(tmp_path):
    result = run_harness(tmp_path, "invalid", "fake-harness-invalid", "incorrect\n")
    assert result.returncode == 0
    assert "failure_category=AUTHORIZATION_INPUT_FAILED" in result.stdout
    assert "sanitized_exception_type=AuthorizationPhraseMismatchError" in result.stdout
    assert "write_boundary_reached=false" in result.stdout
    assert "entry_place_attempts=0" in result.stdout
    assert "session_closed=1" in result.stdout
    assert "authorization_phrase_leakage=0" in result.stdout


def test_eof_fails_before_write_and_closes_session(tmp_path):
    result = run_harness(tmp_path, "eof", "fake-harness-eof", "")
    assert result.returncode == 0
    assert "failure_category=AUTHORIZATION_INPUT_FAILED" in result.stdout
    assert "sanitized_exception_type=AuthorizationReaderError" in result.stdout
    assert "write_boundary_reached=false" in result.stdout
    assert "entry_place_attempts=0" in result.stdout
    assert "session_closed=1" in result.stdout


def test_valid_stdin_completes_once_and_run_id_cannot_be_reused(tmp_path):
    run_id = "fake-harness-valid"
    result = run_harness(tmp_path, "valid", run_id, "FAKE AUTHORIZATION ACCEPTED\n")
    assert result.returncode == 0
    assert "fake_state=COMPLETE" in result.stdout
    assert "verified_flat=true" in result.stdout
    assert "entry_place_attempts=1" in result.stdout
    assert "stop_place_attempts=1" in result.stdout
    assert "stop_cancel_attempts=1" in result.stdout
    assert "exit_place_attempts=1" in result.stdout
    assert "authorization_consumed=1" in result.stdout
    assert "emergency_handoffs=0" in result.stdout
    assert "authorization_phrase_leakage=0" in result.stdout

    reused = run_harness(tmp_path, "reuse", run_id)
    assert reused.returncode == 0
    assert "fake_result=RUN_ID_REUSE_REJECTED" in reused.stdout
    assert "session_created=0" in reused.stdout
    assert "entry_place_attempts=0" in reused.stdout
