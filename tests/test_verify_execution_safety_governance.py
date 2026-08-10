"""Regression tests for the M14 Issue 4/6 independent governance verifier
(scripts/verify_execution_safety_governance.py).

These tests do not re-check the M14 Issue 1/6 governance logic itself --
tests/test_execution_safety_governance.py already does that. They check
that the *verifier* keeps behaving as designed: every check now passes,
including the three checks that used to document real, intentionally-
surfaced adversarial findings (a forged decision, a stale decision, and a
digest/binding-field mismatch, all reaching the adapter's write boundary
before M14 Issue 6 closed them via validate_execution_safety_decision), and
the report stays deterministic and offline. If any of these three checks
starts failing again, that is a real regression in the M14 Issue 6 fix, not
an update to make here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

import scripts.verify_execution_safety_governance as verifier

#: Every check must PASS today (M14 Issue 6 closed the three checks that
#: used to be expected, intentional adversarial findings: "9",
#: "tamper_stale_decision", "tamper_digest_binding_mismatch"). If any of
#: these move back into a "known finding" set, that documents a real
#: regression -- it should come with an explanation, not a silent test edit.
_EXPECTED_PASSING_CHECK_IDS = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13",
    "14", "15", "16", "17", "18", "19", "20_21_22", "23", "24", "25", "26",
    "write_capable_second_site_is_downstream_forwarding_only",
    "decision_construction_single_point",
    "safety_decision_single_supplier",
    "bypass_without_role",
    "launcher_no_direct_governance_reference",
    "self_test_wrapper",
    "self_test_second_site",
    "self_test_dynamic",
    "self_test_getattr_limitation_documented",
    "self_test_authority_grant",
    "self_test_hypothetical_paths",
    "decision_authenticity_gate",
    "tamper_stale_decision",
    "tamper_digest_binding_mismatch",
}

#: No known adversarial findings remain open after M14 Issue 6.
_EXPECTED_ADVERSARIAL_FINDING_IDS: set[str] = set()


def test_report_check_ids_match_expected_pass_and_fail_sets():
    report = verifier.build_report(REPOSITORY_ROOT)
    by_id = {c["check_id"]: c for c in report["checks"]}

    assert set(by_id) == _EXPECTED_PASSING_CHECK_IDS | _EXPECTED_ADVERSARIAL_FINDING_IDS, (
        "the set of check IDs the verifier reports has changed -- update this test's "
        "expected sets deliberately if a check was added, removed, or renamed"
    )

    unexpectedly_failing = {
        check_id for check_id in _EXPECTED_PASSING_CHECK_IDS
        if by_id[check_id]["status"] != "PASS"
    }
    assert not unexpectedly_failing, (
        f"expected-passing checks are no longer PASS: "
        f"{ {cid: by_id[cid]['status'] for cid in unexpectedly_failing} }"
    )

    unexpectedly_passing = {
        check_id for check_id in _EXPECTED_ADVERSARIAL_FINDING_IDS
        if by_id[check_id]["status"] != "FAIL"
    }
    assert not unexpectedly_passing, (
        "a known adversarial finding silently started passing -- this means either the "
        "underlying write-boundary gap was closed (update this test to reflect that) or "
        "the verifier's own detection broke (a real regression): "
        f"{ {cid: by_id[cid]['status'] for cid in unexpectedly_passing} }"
    )

    # No check ever silently reports N/A in this repository's current
    # architecture -- every one of the 25 requested checks was meaningful
    # here (see the PR description for the reasoning behind each).
    assert all(check["status"] != "N_A" for check in report["checks"])


def test_report_is_deterministic_and_offline():
    first = verifier.build_report(REPOSITORY_ROOT)
    second = verifier.build_report(REPOSITORY_ROOT)
    assert first == second


def test_pinned_implementation_closure_count_matches_live_manifest():
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    report = verifier.build_report(REPOSITORY_ROOT)
    # 36 as of M14 Issue 7 (commissioning suspension gate reaches the
    # deferred-gates ecosystem transitively -- see src/read_only_
    # authorization_manifest.py). Never copy a historical count forward.
    assert report["pinned_file_count"] == len(PINNED_IMPLEMENTATION_CLOSURE) == 36
    assert "scripts/verify_execution_safety_governance.py" not in PINNED_IMPLEMENTATION_CLOSURE


def test_cli_json_mode_matches_build_report():
    completed = subprocess.run(
        [sys.executable, "scripts/verify_execution_safety_governance.py", "--json"],
        cwd=REPOSITORY_ROOT, check=False, capture_output=True, text=True,
    )
    payload = json.loads(completed.stdout)
    in_process = verifier.build_report(REPOSITORY_ROOT)
    assert payload == in_process
    # M14 Issue 6 closed the three findings that used to make this FAIL;
    # every check is now expected to PASS and the process to exit 0.
    assert payload["final_result"] == "PASS"
    assert completed.returncode == 0


def test_dispatch_scanner_resolves_literal_and_cross_file_validated_sites():
    """Direct unit check on the scanner used by checks 1-4, independent of
    the full report: a literal call resolves; a call whose tool name is
    produced by an assert_tool_allowed-shaped validator imported from
    another file resolves via that file's own allowlist, not by assumption."""
    literal_source = "class X:\n    async def go(self, session):\n        return await session.call_tool('get_accounts', {})\n"
    sites = verifier._independent_dispatch_scan_source(literal_source, path="<test>")
    assert len(sites) == 1
    assert sites[0].classification == "literal"
    assert sites[0].tool == "get_accounts"

    dynamic_source = "class X:\n    async def go(self, session, tool_name):\n        return await session.call_tool(tool_name, {})\n"
    sites = verifier._independent_dispatch_scan_source(dynamic_source, path="<test>")
    assert len(sites) == 1
    assert sites[0].classification == "unresolved"

    real_sites = verifier._independent_dispatch_scan(REPOSITORY_ROOT)
    adapter_sites = [s for s in real_sites if s.path == "src/robinhood_commissioning_adapter.py"]
    assert any(s.classification == "validated_write_capable" for s in adapter_sites)
    session_sites = [s for s in real_sites if s.path == "src/robinhood_commissioning_session.py"]
    assert any(s.classification == "validated_write_capable" for s in session_sites)


def test_authority_grant_scanner_ignores_docstring_mentions():
    """The scanner used by check 23 must not fail on a module explaining,
    in prose, that it never sets an authority field -- only an actual
    `NAME = True`-shaped assignment should trip it."""
    prose_only = (
        '"""This module never sets protection_grounded = True anywhere."""\n'
        "protection_grounded = False\n"
    )
    assert verifier._authority_grant_scan(prose_only, verifier._AUTHORITY_FIELD_NAMES) is None

    actual_grant = "def f():\n    permits_automated_action = True\n"
    assert verifier._authority_grant_scan(actual_grant, verifier._AUTHORITY_FIELD_NAMES) == "permits_automated_action"


def test_forged_decision_finding_actually_reflects_a_broker_call():
    """Confirms check 9's PASS is backed by an observed refusal, not a
    mislabeled assertion -- re-runs the exact adversarial scenario (a
    hand-built decision with a bogus authority_ceiling and an unrecomputed
    decision_digest) and inspects the fake session directly."""
    import asyncio
    from datetime import datetime, timezone

    from src.execution_safety_governance import ExecutionSafetyOutcome
    from src.robinhood_commissioning_adapter import (
        ExecutionSafetyDeniedError, RobinhoodCommissioningAdapter,
    )

    forged = verifier._hand_built_decision(
        outcome=ExecutionSafetyOutcome.ALLOW, role="entry", arguments=verifier._ENTRY_ARGS,
        evaluated_at=datetime.now(timezone.utc),
        authority_ceiling="NOT_A_REAL_AUTHORITY_CEILING",
        decision_digest="never-computed-by-compute_decision_digest",
    )
    session = verifier._FakeSession(verifier._fake_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "verifier-account")
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.place_equity_order(verifier._ENTRY_ARGS, role="entry", safety_decision=forged))
    assert len(session.calls) == 0


def test_hand_built_decision_default_is_authentic_and_reaches_write():
    """The verifier's own convenience helper must default to an AUTHENTIC
    decision (real POLICY_VERSION/AUTHORITY_CEILING, correctly-computed
    digest) so checks 6/7/8/10/bypass_without_role/stale-tamper -- which
    are not themselves testing forgery -- still exercise real adapter
    enforcement rather than accidentally re-triggering the forged-decision
    pattern check 9 exists to catch."""
    import asyncio
    from datetime import datetime, timezone

    from src.execution_safety_governance import ExecutionSafetyOutcome
    from src.robinhood_commissioning_adapter import RobinhoodCommissioningAdapter

    authentic = verifier._hand_built_decision(
        outcome=ExecutionSafetyOutcome.ALLOW, role="entry", arguments=verifier._ENTRY_ARGS,
        evaluated_at=datetime.now(timezone.utc),
    )
    session = verifier._FakeSession(verifier._fake_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "verifier-account")
    asyncio.run(adapter.place_equity_order(verifier._ENTRY_ARGS, role="entry", safety_decision=authentic))
    assert len(session.calls) == 1
    assert session.calls[0][0] == "place_equity_order"
