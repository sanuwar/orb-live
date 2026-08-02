"""Offline restart recovery — journal durability and fail-closed decisions.

Every test is local and uses `tmp_path`. Nothing opens a session, reaches a
broker, or touches the network.

The properties that matter most:

1. recovery never authorizes anything;
2. a journal that fails its own checksum is refused, not best-effort parsed;
3. every disagreement between the persisted and observed views escalates.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import src.offline_reconciliation as recovery
from src.offline_reconciliation import (
    JOURNAL_VERSION,
    NON_ESCALATING_OUTCOMES,
    RESUME_AUTHORITY_EXPLANATION,
    JournalRecord,
    OrderRole,
    RecoveryError,
    RecoveryOutcome,
    RecoveryResult,
    RunJournal,
    normalize_role_states,
    recover,
)
from src.order_lifecycle_model import LifecycleState as S

MODULE_PATH = pathlib.Path(recovery.__file__)

PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)

RUN = "m10-run-0001"


def record(**overrides):
    values = {
        "run_id": RUN,
        "sequence": 1,
        "recorded_monotonic": 100.0,
        "role_states": {
            OrderRole.ENTRY.value: S.FILLED.value,
            OrderRole.PROTECTIVE_STOP.value: S.WORKING.value,
        },
    }
    values.update(overrides)
    return JournalRecord(**values)


def run_recover(journal, observed, *, now=110.0, run_id=RUN, **kwargs):
    return recover(
        journal, observed, expected_run_id=run_id, current_monotonic=now, **kwargs
    )


# --------------------------------------------------------------------------
# 1. Journal records.
# --------------------------------------------------------------------------


def test_a_well_formed_record_round_trips(tmp_path):
    journal = RunJournal(tmp_path / "state")
    original = record()
    journal.write(original)
    assert journal.read() == original


def test_an_absent_journal_reads_as_none(tmp_path):
    assert RunJournal(tmp_path / "state").read() is None


@pytest.mark.parametrize("run_id", ["", "   ", None, 7])
def test_a_record_needs_a_run_identity(run_id):
    with pytest.raises(RecoveryError):
        record(run_id=run_id)


@pytest.mark.parametrize("sequence", [-1, 1.5, True, "1", None])
def test_a_record_needs_a_valid_sequence(sequence):
    with pytest.raises(RecoveryError):
        record(sequence=sequence)


def test_a_record_rejects_an_unknown_role():
    with pytest.raises(RecoveryError):
        record(role_states={"SOMETHING_ELSE": S.WORKING.value})


def test_a_record_rejects_an_unknown_lifecycle_state():
    with pytest.raises(RecoveryError):
        record(role_states={OrderRole.ENTRY.value: "ARMED"})


def test_a_record_rejects_a_non_mapping_role_states():
    with pytest.raises(RecoveryError):
        record(role_states=[("ENTRY", "FILLED")])


def test_the_checksum_covers_the_content():
    first = record()
    second = record(sequence=2)
    assert first.checksum() != second.checksum()
    assert first.checksum() == record().checksum()


# --------------------------------------------------------------------------
# 2. Torn and corrupt journals.
# --------------------------------------------------------------------------


def test_a_tampered_record_fails_its_checksum(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record())
    payload = json.loads(journal.path.read_text(encoding="utf-8"))
    payload["record"]["sequence"] = 99          # checksum now stale
    journal.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RecoveryError):
        journal.read()


def test_a_truncated_journal_is_refused(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record())
    text = journal.path.read_text(encoding="utf-8")
    journal.path.write_text(text[: len(text) // 2], encoding="utf-8")
    with pytest.raises(RecoveryError):
        journal.read()


@pytest.mark.parametrize(
    "content",
    ['{"record": {}}', "[]", "null", '{"record": 1, "checksum": "x"}',
     '{"checksum": "x"}', '{"record": {}, "checksum": "x", "extra": 1}'],
)
def test_a_malformed_envelope_is_refused(tmp_path, content):
    journal = RunJournal(tmp_path / "state")
    journal.root.mkdir(parents=True, exist_ok=True)
    journal.path.write_text(content, encoding="utf-8")
    with pytest.raises(RecoveryError):
        journal.read()


def test_a_record_with_missing_fields_is_refused(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record())
    payload = json.loads(journal.path.read_text(encoding="utf-8"))
    payload["record"].pop("sequence")
    journal.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RecoveryError):
        journal.read()


def test_an_unsupported_version_is_refused(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record(version=JOURNAL_VERSION + 1))
    with pytest.raises(RecoveryError):
        journal.read()


def test_no_temporary_file_survives_a_successful_write(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record())
    assert not list(journal.root.glob("*.tmp"))


def test_a_rewrite_replaces_rather_than_appends(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record(sequence=1))
    journal.write(record(sequence=2))
    assert journal.read().sequence == 2
    assert len(list(journal.root.iterdir())) == 1


def test_write_rejects_a_non_record(tmp_path):
    with pytest.raises(RecoveryError):
        RunJournal(tmp_path / "state").write({"run_id": RUN})


def test_a_journal_needs_a_path():
    with pytest.raises(RecoveryError):
        RunJournal("state")


# --------------------------------------------------------------------------
# 3. Observation normalization.
# --------------------------------------------------------------------------


def test_broker_labels_normalize_to_lifecycle_states():
    observed = normalize_role_states({OrderRole.ENTRY: "filled", "EXIT": "cancelled"})
    assert observed[OrderRole.ENTRY] is S.FILLED
    assert observed[OrderRole.EXIT] is S.CANCELED


def test_lifecycle_states_pass_through():
    observed = normalize_role_states({OrderRole.ENTRY: S.WORKING})
    assert observed[OrderRole.ENTRY] is S.WORKING


def test_an_unrecognized_label_becomes_unknown_and_is_kept():
    """Dropping it would make an incomplete view look complete."""
    observed = normalize_role_states({OrderRole.ENTRY: "wibble"})
    assert observed[OrderRole.ENTRY] is S.UNKNOWN
    assert OrderRole.ENTRY in observed


def test_an_unknown_role_is_refused():
    with pytest.raises(RecoveryError):
        normalize_role_states({"SOMETHING": "filled"})


def test_non_mapping_observations_are_refused():
    with pytest.raises(RecoveryError):
        normalize_role_states([("ENTRY", "filled")])


# --------------------------------------------------------------------------
# 4. Recovery decisions.
# --------------------------------------------------------------------------


def test_agreement_with_work_remaining_is_a_resume_candidate():
    result = run_recover(
        record(),
        {OrderRole.ENTRY: S.FILLED, OrderRole.PROTECTIVE_STOP: S.WORKING},
    )
    assert result.outcome is RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE
    assert result.pending_roles == (OrderRole.PROTECTIVE_STOP,)
    assert result.requires_operator is False


def test_a_resume_candidate_is_not_permission():
    result = run_recover(
        record(), {OrderRole.ENTRY: S.FILLED, OrderRole.PROTECTIVE_STOP: S.WORKING}
    )
    assert result.permits_automated_action is False
    assert "does not authorize resumption" in result.explanation


def test_everything_terminal_is_no_pending_work():
    result = run_recover(
        record(), {OrderRole.ENTRY: S.FILLED, OrderRole.PROTECTIVE_STOP: S.CANCELED}
    )
    assert result.outcome is RecoveryOutcome.NO_PENDING_WORK
    assert result.pending_roles == ()
    assert result.requires_operator is False


def test_an_absent_journal_requires_an_operator():
    assert (
        run_recover(None, {}).outcome
        is RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_ABSENT
    )


def test_a_journal_for_another_run_requires_an_operator():
    result = run_recover(record(run_id="other-run"), {})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_RUN_ID_MISMATCH


def test_a_sequence_regression_requires_an_operator():
    result = run_recover(record(sequence=1), {}, minimum_sequence=5)
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_SEQUENCE_REGRESSION


def test_a_journal_from_the_future_is_stale():
    result = run_recover(record(recorded_monotonic=500.0), {}, now=100.0)
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_STALE


def test_a_journal_beyond_the_age_bound_is_stale():
    result = run_recover(record(recorded_monotonic=1.0), {}, now=100000.0)
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_STALE


def test_an_unexpected_role_at_the_broker_requires_an_operator():
    """Something else acted, or correlation is wrong. Either way, a human."""
    result = run_recover(
        record(),
        {
            OrderRole.ENTRY: S.FILLED,
            OrderRole.PROTECTIVE_STOP: S.WORKING,
            OrderRole.EXIT: S.WORKING,
        },
    )
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_UNEXPECTED_ROLE


def test_a_vanished_non_terminal_role_requires_an_operator():
    result = run_recover(record(), {OrderRole.ENTRY: S.FILLED})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_MISSING_ROLE


def test_a_vanished_terminal_role_is_not_alarming():
    """A finished order dropping out of a listing is ordinary."""
    journal = record(
        role_states={
            OrderRole.ENTRY.value: S.FILLED.value,
            OrderRole.PROTECTIVE_STOP.value: S.CANCELED.value,
        }
    )
    result = run_recover(journal, {OrderRole.ENTRY: S.FILLED})
    assert result.outcome is RecoveryOutcome.NO_PENDING_WORK


def test_an_unknown_observed_state_requires_an_operator():
    result = run_recover(
        record(),
        {OrderRole.ENTRY: S.FILLED, OrderRole.PROTECTIVE_STOP: "wibble"},
    )
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_UNKNOWN_STATE


def test_an_impossible_transition_requires_an_operator():
    journal = record(role_states={OrderRole.ENTRY.value: S.FILLED.value})
    result = run_recover(journal, {OrderRole.ENTRY: S.CANCELED})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_IMPOSSIBLE_TRANSITION


def test_a_backward_transition_requires_an_operator():
    journal = record(
        role_states={OrderRole.PROTECTIVE_STOP.value: S.WORKING.value}
    )
    result = run_recover(journal, {OrderRole.PROTECTIVE_STOP: S.ACCEPTED})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_BACKWARD_TRANSITION


def test_a_forward_transition_is_accepted():
    journal = record(
        role_states={OrderRole.PROTECTIVE_STOP.value: S.WORKING.value}
    )
    result = run_recover(journal, {OrderRole.PROTECTIVE_STOP: S.FILLED})
    assert result.outcome is RecoveryOutcome.NO_PENDING_WORK


def test_a_replaced_role_requires_an_operator_rather_than_reading_as_done():
    """Self-review finding: this used to report NO_PENDING_WORK.

    `REPLACED` is terminal for the old identifier, so a naive terminal check
    concluded there was nothing left to do — while the successor order, which
    the journal never recorded, may have been resting at the venue.
    """
    journal = record(
        role_states={OrderRole.PROTECTIVE_STOP.value: S.WORKING.value}
    )
    result = run_recover(journal, {OrderRole.PROTECTIVE_STOP: S.REPLACED})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_REPLACED_ROLE
    assert result.requires_operator is True


def test_a_replaced_role_escalates_even_when_already_persisted_as_replaced():
    journal = record(
        role_states={OrderRole.PROTECTIVE_STOP.value: S.REPLACED.value}
    )
    result = run_recover(journal, {OrderRole.PROTECTIVE_STOP: S.REPLACED})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_REPLACED_ROLE


def test_a_replaced_role_beats_the_other_terminal_roles_being_clean():
    """One superseded role escalates the whole recovery."""
    journal = record(
        role_states={
            OrderRole.ENTRY.value: S.FILLED.value,
            OrderRole.PROTECTIVE_STOP.value: S.WORKING.value,
        }
    )
    result = run_recover(
        journal,
        {OrderRole.ENTRY: S.FILLED, OrderRole.PROTECTIVE_STOP: S.REPLACED},
    )
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_REPLACED_ROLE


def test_run_identity_is_required():
    with pytest.raises(RecoveryError):
        recover(record(), {}, expected_run_id="", current_monotonic=1.0)


def test_a_non_record_journal_is_refused():
    with pytest.raises(RecoveryError):
        recover({}, {}, expected_run_id=RUN, current_monotonic=1.0)


def test_check_order_is_a_safety_property():
    """A journal for the wrong run must not be compared role by role.

    The observation here would produce UNEXPECTED_ROLE if roles were checked
    first; the run mismatch must win, because a confident answer about the
    wrong run is worse than no answer.
    """
    result = run_recover(record(run_id="other"), {OrderRole.EXIT: S.WORKING})
    assert result.outcome is RecoveryOutcome.OPERATOR_REQUIRED_RUN_ID_MISMATCH


# --------------------------------------------------------------------------
# 5. Fail-closed invariants.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", list(RecoveryOutcome))
def test_no_outcome_permits_automated_action(outcome):
    assert RecoveryResult(outcome).permits_automated_action is False


def test_a_permitting_result_cannot_be_constructed():
    with pytest.raises(RecoveryError):
        RecoveryResult(
            RecoveryOutcome.NO_PENDING_WORK, permits_automated_action=True
        )


def test_exactly_two_outcomes_do_not_escalate():
    assert NON_ESCALATING_OUTCOMES == {
        RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE,
        RecoveryOutcome.NO_PENDING_WORK,
    }
    escalating = set(RecoveryOutcome) - NON_ESCALATING_OUTCOMES
    assert len(escalating) == 11
    for outcome in escalating:
        assert RecoveryResult(outcome).requires_operator is True


def test_the_explanation_states_where_authority_comes_from():
    assert "supervised-execution gate" in RESUME_AUTHORITY_EXPLANATION
    assert "does not exist yet" in RESUME_AUTHORITY_EXPLANATION


# --------------------------------------------------------------------------
# 6. Privacy.
# --------------------------------------------------------------------------

ALLOWED_FACT_VALUES = {
    "recovery_outcome": {outcome.value for outcome in RecoveryOutcome},
    "requires_operator": {"true", "false"},
    "pending_role_count": {"none", "one", "many"},
    "permits_automated_action": {"false"},
}


def test_recovery_facts_are_allowlisted():
    for outcome in RecoveryOutcome:
        facts = RecoveryResult(outcome, (OrderRole.ENTRY,)).sanitized_facts()
        assert set(facts) == set(ALLOWED_FACT_VALUES)
        for key, value in facts.items():
            assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_pending_roles_are_counted_not_listed():
    result = RecoveryResult(
        RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE,
        (OrderRole.ENTRY, OrderRole.PROTECTIVE_STOP),
    )
    facts = result.sanitized_facts()
    assert facts["pending_role_count"] == "many"
    assert "ENTRY" not in repr(facts)


def test_the_journal_persists_no_identifier_quantity_symbol_or_price(tmp_path):
    journal = RunJournal(tmp_path / "state")
    journal.write(record())
    text = journal.path.read_text(encoding="utf-8")
    for forbidden in ("order_id", "account", "symbol", "price", "quantity", "AAPL"):
        assert forbidden not in text


def test_the_journal_record_has_no_field_that_could_carry_one():
    import dataclasses

    names = {f.name for f in dataclasses.fields(JournalRecord)}
    for forbidden in ("identifier", "symbol", "price", "quantity", "account", "payload"):
        assert not any(forbidden in name for name in names), forbidden


def test_journal_content_survives_the_commissioning_privacy_sanitizer(tmp_path):
    from dataclasses import asdict

    from src.one_share_commissioning import validate_sanitized_evidence

    validate_sanitized_evidence(asdict(record()))
    validate_sanitized_evidence(
        RecoveryResult(RecoveryOutcome.NO_PENDING_WORK).sanitized_facts()
    )


# --------------------------------------------------------------------------
# 7. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_recovery_imports_no_pinned_checkpoint_file():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_recovery_imports_nothing_broker_shaped():
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator",
                 "commissioning", "manifest", "checkpoint", "strategy", "broker")
    for name in _imports(MODULE_PATH):
        assert not any(word in name.lower() for word in forbidden), name


def test_recovery_makes_no_network_call():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "subprocess"}
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "urlopen" not in source and "http://" not in source


def test_recovery_reads_no_wall_clock():
    """`current_monotonic` is injected; a module that reads the clock itself
    cannot be tested against a frozen one."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source
    assert "time.monotonic(" not in source


def test_recovery_defines_no_write_shaped_function():
    forbidden = {"place", "cancel", "submit", "activate", "execute", "send"}
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens = set(node.name.lower().strip("_").split("_"))
            assert not (tokens & forbidden), node.name


def test_the_only_filesystem_writes_are_the_journal():
    """Journal persistence is intended; nothing else may touch the disk."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    write_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name in {"open", "write_text", "write_bytes", "replace", "mkdir", "fsync"}:
                write_calls.append(name)
    assert set(write_calls) <= {"open", "replace", "mkdir", "fsync"}
