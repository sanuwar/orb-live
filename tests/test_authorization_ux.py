"""Focused broker-free acceptance coverage for attended y/N authorization."""

import asyncio
import json

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import AtomicEvidenceStore, State
from tests.test_checkpoint_7c_incident_regression import (
    NOW,
    actual_binding,
    live_request,
    read_sanitized,
)
from tests.test_one_share_commissioning_orchestrator import make_orchestrator


def run(value):
    return asyncio.run(value.run())


def provider(tmp_path, run_id, reader):
    return launcher.InteractiveAuthorizationProvider(
        live_request(tmp_path, run_id=run_id),
        now_eastern=lambda: NOW,
        secret_reader=reader,
    )


def read_result(result):
    return json.loads(result.evidence_path.read_text(encoding="utf-8"))


def test_approved_entry_and_flatten_keep_all_write_roles_capped_at_one(tmp_path):
    supplied = iter(["y", "Y"])
    prompts = []
    approval = provider(
        tmp_path,
        "authorization-approved-path",
        lambda prompt: prompts.append(prompt) or next(supplied),
    )
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        run_id="authorization-approved-path",
        authorization_provider=approval,
    )

    result = run(instance)
    evidence = read_result(result)

    assert result.state is State.COMPLETE
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert all(adapter.write_roles.count(role) == 1 for role in adapter.write_roles)
    assert prompts == [
        launcher.ENTRY_AUTHORIZATION_PROMPT,
        launcher.FLATTEN_AUTHORIZATION_PROMPT,
    ]
    assert evidence["boolean_validations"]["verified_flat"] is True
    assert "ENTRY_AUTHORIZATION_APPROVED" in evidence["conservative_classifications"]
    assert "FLATTEN_AUTHORIZATION_APPROVED" in evidence["conservative_classifications"]
    assert handoffs == []


def test_declined_entry_has_one_prompt_zero_entry_writes_and_no_raw_input(tmp_path):
    raw_input = "PRIVATE_DECLINED_ENTRY_INPUT"
    prompts = []
    approval = provider(
        tmp_path,
        "authorization-entry-declined",
        lambda prompt: prompts.append(prompt) or raw_input,
    )
    instance, adapter, _, _ = make_orchestrator(
        tmp_path,
        run_id="authorization-entry-declined",
        authorization_provider=approval,
    )

    result = run(instance)
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)

    assert result.state is State.FAILED_CLOSED
    assert adapter.write_roles == []
    assert prompts == [launcher.ENTRY_AUTHORIZATION_PROMPT]
    assert evidence["failure_category"] == "ENTRY_AUTHORIZATION_DECLINED"
    assert raw_input not in rendered


def test_declined_flatten_has_no_cancel_or_exit_and_keeps_protection(tmp_path):
    raw_input = "PRIVATE_DECLINED_FLATTEN_INPUT"
    supplied = iter(["y", raw_input])
    prompts = []
    approval = provider(
        tmp_path,
        "authorization-flatten-declined",
        lambda prompt: prompts.append(prompt) or next(supplied),
    )
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path,
        run_id="authorization-flatten-declined",
        authorization_provider=approval,
    )

    result = run(instance)
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)

    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles == ["entry", "stop"]
    assert "cancel" not in adapter.write_roles
    assert "exit" not in adapter.write_roles
    assert prompts == [
        launcher.ENTRY_AUTHORIZATION_PROMPT,
        launcher.FLATTEN_AUTHORIZATION_PROMPT,
    ]
    assert evidence["failure_category"] == "FLATTEN_AUTHORIZATION_DECLINED"
    assert raw_input not in rendered
    assert len(handoffs) == 1


def test_interrupted_flatten_has_no_cancel_or_exit_and_session_closes(tmp_path):
    calls = 0

    def reader(_prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "y"
        raise KeyboardInterrupt

    binding, factory, _, store = actual_binding(
        tmp_path,
        run_id="authorization-flatten-interrupted",
        secret_reader=reader,
    )

    result = asyncio.run(binding.run())
    evidence = read_sanitized(store.partial / "commissioning_evidence.json")

    assert result.state is State.OPERATOR_REQUIRED
    assert calls == 2
    assert evidence["failure_category"] == "FLATTEN_AUTHORIZATION_INTERRUPTED"
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1,
        "stop_place": 1,
        "stop_cancel": 0,
        "exit_place": 0,
    }
    assert factory.created == 1
    assert factory.entered == 1
    assert factory.closed == 1


def test_declined_stage_cannot_be_prompted_again(tmp_path):
    prompts = []
    approval = provider(
        tmp_path,
        "authorization-no-reprompt",
        lambda prompt: prompts.append(prompt) or "n",
    )

    try:
        approval("entry")
    except Exception:
        pass
    try:
        approval("entry")
    except Exception:
        pass

    assert prompts == [launcher.ENTRY_AUTHORIZATION_PROMPT]