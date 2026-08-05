from __future__ import annotations

import json
from datetime import timedelta

import pytest

from src.bounded_shadow_session import SESSION_NOW, run_bounded_shadow_session
from src.offline_evidence_summary import (
    CountBucket, EvidenceSummaryError, OfflineHealthStatus, ReadinessStatus,
    SummaryReason, load_summary, summarize_sessions,
)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return run_bounded_shadow_session(tmp_path_factory.mktemp("summary") / "session")


def test_summary_is_projection_only_and_blocked(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", (report,), evaluated_at=SESSION_NOW)
    assert summary.offline_health_status is OfflineHealthStatus.DEGRADED
    assert summary.supervised_readiness_status is ReadinessStatus.SUPERVISED_BLOCKED
    assert SummaryReason.PROTECTION_NOT_GROUNDED in summary.blocker_reasons
    assert summary.live_ready is False and summary.unattended_ready is False
    assert summary.protection_grounded is False and summary.activation_approved is False
    assert summary.permits_automated_action is False


def test_allowlisted_deterministic_round_trip(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", (report,), evaluated_at=SESSION_NOW)
    payload = json.loads(summary.serialize())
    assert summary.serialize() == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert load_summary(summary.serialize()) == summary


def test_duplicate_mixed_stale_and_future_evidence_rejected(report) -> None:
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (report, report), evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (report,), evaluated_at=SESSION_NOW - timedelta(minutes=1))
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (report,), evaluated_at=SESSION_NOW + timedelta(days=2))


def test_unknown_keys_and_private_fields_rejected(report) -> None:
    summary = summarize_sessions("batch-shadow-20260803", (report,), evaluated_at=SESSION_NOW)
    raw = summary.to_dict()
    raw["symbol"] = "X"
    with pytest.raises(EvidenceSummaryError):
        load_summary(json.dumps(raw))
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-account-1", (report,), evaluated_at=SESSION_NOW)


def test_invalid_component_inputs_rejected() -> None:
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", [], evaluated_at=SESSION_NOW)
    with pytest.raises(EvidenceSummaryError):
        summarize_sessions("batch-shadow-20260803", (object(),), evaluated_at=SESSION_NOW)


def test_count_bucket_boundaries() -> None:
    assert [CountBucket.ZERO, CountBucket.ONE, CountBucket.TWO_TO_FIVE, CountBucket.MANY] == list(CountBucket)
