"""Durable, append-only cross-run protection-observation ledger (M13 Issue 2).

Every test uses `tmp_path` per `CLAUDE.md`. Nothing here touches repository
data, a broker, the network, or a real clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.protection_observation_ledger import (
    LedgerAppendState,
    ProtectionLedgerError,
    ProtectionObservationLedger,
    STATES_WITH_A_STORED_ROW,
)
from src.protection_observation_model import EvidenceSourceClass
from src.read_only_broker_state import SnapshotFreshness
from tests._protection_observation_fixtures import (
    active_protective_order,
    conflicting_states,
    digest,
    duplicate_protection,
    imported_evidence,
    make_observation,
    mismatched_account_fingerprint,
    mismatched_implementation_digest,
    no_protective_order,
    over_coverage,
    partial_quantity_coverage,
    simulated_evidence,
    stale_protective_order,
    terminal_protective_order,
)


def _ledger(tmp_path: Path, run_id: str = "run-0001") -> ProtectionObservationLedger:
    return ProtectionObservationLedger(tmp_path / "ledger.sqlite3", run_id)


# --------------------------------------------------------------------------
# Repository-data refusal.
# --------------------------------------------------------------------------


def test_refuses_a_repository_data_path():
    repo_root = Path(__file__).resolve().parent.parent
    with pytest.raises(ProtectionLedgerError):
        ProtectionObservationLedger(repo_root / "data" / "ledger.sqlite3", "run-1")


# --------------------------------------------------------------------------
# Append: the scenarios named in Work Item 2.
# --------------------------------------------------------------------------


def test_no_protective_order_scenario_accepted(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(no_protective_order("run-0001"))
        assert result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_active_protective_order_scenario_accepted(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(active_protective_order("run-0001"))
        assert result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_stale_protective_order_scenario_flagged(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(stale_protective_order("run-0001"))
        assert result.state is LedgerAppendState.STALE_OBSERVATION
        assert result.observation_id in {
            summary.observation_id for summary in ledger.stale_observations()
        }


def test_terminal_protective_order_scenario_accepted(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(terminal_protective_order("run-0001"))
        assert result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_partial_quantity_coverage_scenario_accepted(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(partial_quantity_coverage("run-0001"))
        assert result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_over_coverage_scenario_accepted(tmp_path):
    with _ledger(tmp_path) as ledger:
        result = ledger.append(over_coverage("run-0001"))
        assert result.state is LedgerAppendState.ACCEPTED_OBSERVATION


def test_duplicate_protection_scenario_both_recorded(tmp_path):
    first, second = duplicate_protection("run-0001")
    with _ledger(tmp_path) as ledger:
        assert ledger.append(first).state is LedgerAppendState.ACCEPTED_OBSERVATION
        assert ledger.append(second).state is LedgerAppendState.ACCEPTED_OBSERVATION
        assert len(ledger.observations_for_run("run-0001")) == 2


def test_conflicting_states_scenario_both_recorded_for_rubric_to_judge(tmp_path):
    active, flat = conflicting_states("run-0001")
    with _ledger(tmp_path) as ledger:
        ledger.append(active)
        ledger.append(flat)
        assert len(ledger.observations_for_run("run-0001")) == 2


def test_mismatched_account_fingerprint_scenario_accepted_and_queryable(tmp_path):
    obs = mismatched_account_fingerprint("run-0001")
    with _ledger(tmp_path) as ledger:
        ledger.append(obs)
        assert ledger.observations_by_account(obs.account_fingerprint_reference)


def test_mismatched_implementation_digest_scenario_accepted_and_queryable(tmp_path):
    obs = mismatched_implementation_digest("run-0001")
    with _ledger(tmp_path) as ledger:
        ledger.append(obs)
        assert ledger.observations_by_implementation(obs.implementation_digest)


def test_simulated_evidence_scenario_accepted_but_not_broker_authoritative(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(simulated_evidence("run-0001"))
        assert ledger.broker_authoritative_observations() == []
        assert len(
            ledger.observations_by_source(EvidenceSourceClass.SIMULATED_BROKER)
        ) == 1


def test_imported_evidence_scenario_accepted_but_not_broker_authoritative(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(imported_evidence("run-0001"))
        assert ledger.broker_authoritative_observations() == []


def test_duplicate_equivalent_record_is_idempotent(tmp_path):
    with _ledger(tmp_path) as ledger:
        obs = no_protective_order("run-0001", observation_id="obs-x")
        first = ledger.append(obs)
        second = ledger.append(obs)
        assert first.state is LedgerAppendState.ACCEPTED_OBSERVATION
        assert second.state is LedgerAppendState.DUPLICATE_EQUIVALENT
        assert second.recorded_sequence == first.recorded_sequence
        assert len(ledger.all_observations()) == 1


def test_duplicate_conflicting_record_is_refused_without_overwriting(tmp_path):
    with _ledger(tmp_path) as ledger:
        original = no_protective_order("run-0001", observation_id="obs-y")
        ledger.append(original)
        conflicting = active_protective_order("run-0001", observation_id="obs-y")
        result = ledger.append(conflicting)
        assert result.state is LedgerAppendState.DUPLICATE_CONFLICT
        stored = ledger.observations_for_run("run-0001")
        assert len(stored) == 1
        assert (
            stored[0].normalized_protection_state
            == original.normalized_protection_state
        )
        conflicts = ledger.conflicting_observations()
        assert len(conflicts) == 1
        assert conflicts[0].observation_id == "obs-y"
        assert conflicts[0].existing_evidence_digest == original.evidence_digest
        assert conflicts[0].attempted_evidence_digest == conflicting.evidence_digest


# --------------------------------------------------------------------------
# Append behaviour: ordering, run isolation, closed runs.
# --------------------------------------------------------------------------


def test_append_orders_deterministically(tmp_path):
    with _ledger(tmp_path) as ledger:
        first = ledger.append(no_protective_order("run-0001", observation_id="a"))
        second = ledger.append(no_protective_order("run-0001", observation_id="b"))
        assert second.recorded_sequence == first.recorded_sequence + 1
        ordered = [o.observation_id for o in ledger.all_observations()]
        assert ordered == ["a", "b"]


def test_append_rejects_run_id_mismatch(tmp_path):
    with _ledger(tmp_path, run_id="run-0001") as ledger:
        wrong_run = no_protective_order("run-0002", observation_id="obs-wrong")
        result = ledger.append(wrong_run)
        assert result.state is LedgerAppendState.INVALID_OBSERVATION
        assert ledger.all_observations() == []


def test_append_after_close_raises(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.close_run()
        with pytest.raises(ProtectionLedgerError):
            ledger.append(no_protective_order("run-0001"))


def test_closed_run_cannot_reopen(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with ProtectionObservationLedger(path, "run-close-me") as ledger:
        ledger.close_run()
    with pytest.raises(ProtectionLedgerError):
        ProtectionObservationLedger(path, "run-close-me")


def test_append_rejects_non_record_input(tmp_path):
    with _ledger(tmp_path) as ledger:
        with pytest.raises(ProtectionLedgerError):
            ledger.append({"not": "a record"})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Cross-run querying.
# --------------------------------------------------------------------------


def test_cross_run_query_spans_multiple_runs(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with ProtectionObservationLedger(path, "run-a") as ledger:
        ledger.append(no_protective_order("run-a", observation_id="a1"))
    with ProtectionObservationLedger(path, "run-b") as ledger:
        ledger.append(no_protective_order("run-b", observation_id="b1"))
        all_obs = ledger.all_observations()
        assert {o.observation_id for o in all_obs} == {"a1", "b1"}
        assert {o.run_id for o in all_obs} == {"run-a", "run-b"}


def test_observations_for_run_is_scoped(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with ProtectionObservationLedger(path, "run-a") as ledger:
        ledger.append(no_protective_order("run-a", observation_id="a1"))
    with ProtectionObservationLedger(path, "run-b") as ledger:
        ledger.append(no_protective_order("run-b", observation_id="b1"))
        assert len(ledger.observations_for_run("run-a")) == 1
        assert len(ledger.observations_for_run("run-b")) == 1


def test_stale_query_excludes_fresh_records(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="fresh"))
        ledger.append(stale_protective_order("run-0001", observation_id="stale"))
        stale_ids = {o.observation_id for o in ledger.stale_observations()}
        assert stale_ids == {"stale"}


def test_source_filtering(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001", observation_id="live"))
        ledger.append(simulated_evidence("run-0001", observation_id="sim"))
        live = ledger.observations_by_source(
            EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
        )
        assert {o.observation_id for o in live} == {"live"}


def test_account_filtering(tmp_path):
    with _ledger(tmp_path) as ledger:
        a = no_protective_order("run-0001", observation_id="acct-a")
        b = mismatched_account_fingerprint("run-0001", observation_id="acct-b")
        ledger.append(a)
        ledger.append(b)
        assert len(ledger.observations_by_account(a.account_fingerprint_reference)) == 1
        assert len(ledger.observations_by_account(b.account_fingerprint_reference)) == 1


def test_implementation_filtering(tmp_path):
    with _ledger(tmp_path) as ledger:
        a = no_protective_order("run-0001", observation_id="impl-a")
        b = mismatched_implementation_digest("run-0001", observation_id="impl-b")
        ledger.append(a)
        ledger.append(b)
        assert len(ledger.observations_by_implementation(a.implementation_digest)) == 1
        assert len(ledger.observations_by_implementation(b.implementation_digest)) == 1


# --------------------------------------------------------------------------
# Rebuild behaviour: disposable, non-authoritative.
# --------------------------------------------------------------------------


def test_rebuild_replays_records_idempotently(tmp_path):
    records = [
        no_protective_order("run-0001", observation_id="r1"),
        active_protective_order("run-0001", observation_id="r2"),
    ]
    path = tmp_path / "ledger.sqlite3"
    with ProtectionObservationLedger(path, "run-0001") as ledger:
        first_results = ledger.rebuild(records)
        assert all(
            r.state is LedgerAppendState.ACCEPTED_OBSERVATION for r in first_results
        )

    # Simulate the disposable database being lost and rebuilt from the same
    # authoritative record stream.
    (path).unlink()
    with ProtectionObservationLedger(path, "run-0001") as ledger:
        second_results = ledger.rebuild(records)
        assert len(ledger.all_observations()) == 2
        assert all(
            r.state is LedgerAppendState.ACCEPTED_OBSERVATION for r in second_results
        )


def test_rebuild_after_prior_writes_is_idempotent(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    records = [no_protective_order("run-0001", observation_id="r1")]
    with ProtectionObservationLedger(path, "run-0001") as ledger:
        ledger.append(records[0])
        replay_results = ledger.rebuild(records)
        assert replay_results[0].state is LedgerAppendState.DUPLICATE_EQUIVALENT
        assert len(ledger.all_observations()) == 1


# --------------------------------------------------------------------------
# Export and sanitized facts.
# --------------------------------------------------------------------------


def test_export_summaries_are_allowlisted_and_privacy_safe(tmp_path):
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        exported = ledger.export_summaries()
        assert len(exported) == 1
        row = exported[0]
        allowed_keys = {
            "observation_id",
            "run_id",
            "observation_sequence",
            "recorded_sequence",
            "release_identity",
            "implementation_digest",
            "account_fingerprint_reference",
            "broker_adapter_identity",
            "evidence_source_class",
            "normalized_protection_state",
            "data_freshness",
            "consistency_status",
            "contamination_status",
            "privacy_redaction_status",
            "evidence_digest",
            "append_state",
        }
        assert set(row) == allowed_keys


def test_sanitized_facts_are_bucketed_not_exact(tmp_path):
    with _ledger(tmp_path) as ledger:
        facts = ledger.sanitized_facts()
        assert facts["total_observation_count_bucket"] == "none"
        ledger.append(no_protective_order("run-0001"))
        facts = ledger.sanitized_facts()
        assert facts["total_observation_count_bucket"] == "one"


# --------------------------------------------------------------------------
# Never authoritative: recording never grants authority.
# --------------------------------------------------------------------------


def test_appending_does_not_touch_any_authority_module(tmp_path):
    import src.execution_gates as execution_gates
    import src.live_order_state_model as live_order_state_model

    before_gate = execution_gates.ACTIVATION_APPROVALS
    before_states = live_order_state_model.ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
    with _ledger(tmp_path) as ledger:
        ledger.append(active_protective_order("run-0001"))
        ledger.append(active_protective_order("run-0001", observation_id="x2"))
    assert execution_gates.ACTIVATION_APPROVALS == before_gate == frozenset()
    assert (
        live_order_state_model.ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES
        == before_states
        == frozenset()
    )


def test_ledger_module_has_no_authority_setters():
    import src.protection_observation_ledger as ledger_module

    forbidden = (
        "protection_grounded",
        "activation_approved",
        "permits_automated_action",
        "live_ready",
        "unattended_ready",
    )
    for name in forbidden:
        assert not hasattr(ledger_module.ProtectionObservationLedger, name)
