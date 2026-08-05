from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from src.bounded_shadow_session import SESSION_NOW, run_bounded_shadow_session
from src.offline_evidence_summary import (
    CountBucket, EvidenceSummaryError, OfflineHealthStatus, ReadinessStatus,
    SessionEvidence, SummaryReason, load_summary, summarize_sessions,
)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return run_bounded_shadow_session(tmp_path_factory.mktemp("summary") / "session")


def test_summary_is_projection_only_and_blocked(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW)
    assert summary.offline_health_status is OfflineHealthStatus.DEGRADED
    assert summary.supervised_readiness_status is ReadinessStatus.SUPERVISED_BLOCKED
    assert SummaryReason.PROTECTION_NOT_GROUNDED in summary.blocker_reasons
    assert summary.live_ready is False and summary.unattended_ready is False
    assert summary.protection_grounded is False and summary.activation_approved is False
    assert summary.permits_automated_action is False


def test_allowlisted_deterministic_round_trip(report) -> None:
    evidence = (SessionEvidence("occurrence-shadow-20260803-01", report),)
    summary = summarize_sessions("batch-shadow-20260803", evidence, evaluated_at=SESSION_NOW)
    payload = json.loads(summary.serialize())
    assert summary.serialize() == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert load_summary(summary.serialize(), evidence, evaluated_at=SESSION_NOW) == summary


def test_duplicate_mixed_stale_and_future_evidence_rejected(report) -> None:
    evidence = SessionEvidence("occurrence-shadow-20260803-01", report)
    assert summarize_sessions("batch-shadow-20260803", (evidence, SessionEvidence("occurrence-shadow-20260803-02", report)), evaluated_at=SESSION_NOW).session_count is CountBucket.TWO_TO_FIVE
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (evidence, evidence), evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW - timedelta(minutes=1))
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW + timedelta(days=2))


def test_unknown_keys_and_private_fields_rejected(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW)
    raw = summary.to_dict()
    raw["symbol"] = "X"
    with pytest.raises(EvidenceSummaryError):
        load_summary(json.dumps(raw), (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-account-1", (SessionEvidence("occurrence-shadow-20260803-01", report),), evaluated_at=SESSION_NOW)


def test_invalid_component_inputs_rejected() -> None:
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", [], evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (object(),), evaluated_at=SESSION_NOW)


def test_count_bucket_boundaries() -> None:
    assert [CountBucket.ZERO, CountBucket.ONE, CountBucket.TWO_TO_FIVE, CountBucket.MANY] == list(CountBucket)


def _evidence(report):
    return (SessionEvidence("occurrence-shadow-20260803-01", report),)


def _tampered_payload(summary, **changes):
    raw = summary.to_dict()
    raw.update(changes)
    return json.dumps(raw)


@pytest.mark.parametrize("state", [
    "STOPPED_ON_SUSPENSION", "AMBIGUOUS_RESTART", "DELIVERY_UNCONFIRMED",
    "STALE", "STOPPED_ON_REVIEW",
])
def test_favorable_headline_plus_each_adverse_state_is_rejected(report, state) -> None:
    adverse = replace(report, observations=(replace(report.observations[0], observed_state=state),) + report.observations[1:])
    evidence = _evidence(adverse)
    summary = summarize_sessions("batch-shadow-20260803", evidence, evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        load_summary(_tampered_payload(summary, offline_health_status=OfflineHealthStatus.HEALTHY.value), evidence, evaluated_at=SESSION_NOW)


@pytest.mark.parametrize("field,value", [
    ("offline_health_status", OfflineHealthStatus.HEALTHY.value),
    ("supervised_readiness_status", ReadinessStatus.OFFLINE_HEALTHY.value),
    ("live_ready", True), ("unattended_ready", True),
    ("protection_grounded", True), ("activation_approved", True),
    ("permits_automated_action", True),
])
def test_favorable_headline_or_permission_is_rejected(report, field, value) -> None:
    evidence = _evidence(report)
    summary = summarize_sessions("batch-shadow-20260803", evidence, evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        load_summary(_tampered_payload(summary, **{field: value}), evidence, evaluated_at=SESSION_NOW)


def test_direct_contradictory_construction_is_rejected(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", _evidence(report), evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        replace(summary, offline_health_status=OfflineHealthStatus.HEALTHY)


def test_rehashed_semantically_contradictory_payload_is_rejected(report) -> None:
    evidence = _evidence(report)
    summary = summarize_sessions("batch-shadow-20260803", evidence, evaluated_at=SESSION_NOW)
    raw = summary.to_dict()
    raw["offline_health_status"] = OfflineHealthStatus.HEALTHY.value
    raw["evidence_digest"] = "0" * 64
    with pytest.raises(EvidenceSummaryError):
        load_summary(json.dumps(raw), evidence, evaluated_at=SESSION_NOW)


def test_missing_or_extra_blocker_reason_is_rejected(report) -> None:
    evidence = _evidence(report)
    summary = summarize_sessions("batch-shadow-20260803", evidence, evaluated_at=SESSION_NOW)
    reasons = summary.to_dict()["blocker_reasons"]
    with pytest.raises(EvidenceSummaryError):
        load_summary(_tampered_payload(summary, blocker_reasons=reasons[:-1]), evidence, evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        load_summary(_tampered_payload(summary, blocker_reasons=reasons + [SummaryReason.ACTIVE_SUSPENSION.value]), evidence, evaluated_at=SESSION_NOW)


def test_occurrence_identity_is_bound_and_reload_preserves_it(report) -> None:
    first = SessionEvidence("occurrence-shadow-20260803-01", report)
    second = SessionEvidence("occurrence-shadow-20260803-02", report)
    summary = summarize_sessions("batch-shadow-20260803", (second, first), evaluated_at=SESSION_NOW)
    assert summary.occurrence_ids == (first.occurrence_id, second.occurrence_id)
    assert load_summary(summary.serialize(), (first, second), evaluated_at=SESSION_NOW).occurrence_ids == summary.occurrence_ids
    with pytest.raises(EvidenceSummaryError):
        load_summary(summary.serialize(), (first, first), evaluated_at=SESSION_NOW)
    raw = summary.to_dict()
    raw["occurrence_ids"] = [second.occurrence_id, first.occurrence_id]
    with pytest.raises(EvidenceSummaryError):
        load_summary(json.dumps(raw), (first, second), evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        load_summary(summary.serialize(), (SessionEvidence("occurrence-other-20260803-01", report),), evaluated_at=SESSION_NOW)


def test_occurrence_identity_changes_digest_and_batch_binding(report) -> None:
    first = summarize_sessions("batch-shadow-20260803", _evidence(report), evaluated_at=SESSION_NOW)
    second = summarize_sessions("batch-shadow-20260803", (SessionEvidence("occurrence-shadow-20260803-02", report),), evaluated_at=SESSION_NOW)
    assert first.evidence_digest != second.evidence_digest
    with pytest.raises(EvidenceSummaryError):
        load_summary(first.serialize(), (SessionEvidence("occurrence-shadow-20260803-02", report),), evaluated_at=SESSION_NOW)
