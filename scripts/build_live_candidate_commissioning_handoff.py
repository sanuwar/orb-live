"""Build a saved attended-commissioning handoff from one live-shadow candidate.

This is a read-only, non-authorizing bridge from authenticated LIVE_SHADOW
evidence to the existing production read-only rehearsal.  It deliberately
stops before any adapter or broker mutation boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.account_state import AccountStateReader
from src.attended_commissioning_handoff import (
    AUTHORITY_CEILING,
    AttendedCommissioningHandoff,
    AttendedCommissioningHandoffState,
    build_attended_commissioning_handoff,
)
from src.attended_safety_evaluation_bridge import (
    AttendedSafetyEvaluationResult,
    AttendedSafetyEvaluationState,
    AttendedSafetyInputs,
    build_attended_safety_evaluation_result,
)
from src.broker_identity import CommissionedAccount, load_local_override
from src.commissioning_suspension_identity import (
    CommissioningSuspensionIdentity,
    CommissioningSuspensionStateProvider,
)
from src.config import load_config
from src.execution_safety_governance import (
    CommissioningGovernanceEvidence,
    CommissioningMode,
    ExecutionAction,
    ExecutionSafetyContext,
    OperatorAcknowledgements,
    evaluate_execution_safety,
)
from src.live_attended_preflight_inputs import (
    SuspensionObservation,
    build_live_attended_preflight_result,
    observe_suspension,
)
from src.market_data_canonical import MarketDataQuality
from src.read_only_authorization_manifest import (
    checkpoint_implementation_digest,
    resolve_head_sha,
)
from src.shadow_candidate_commissioning_review import (
    ReviewReadinessState,
    ShadowCandidateCommissioningReview,
    build_shadow_candidate_commissioning_review,
)
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
)
from src.supervised_micro_live_dry_run import (
    DryRunState,
    SupervisedMicroLiveDryRunPlan,
    build_supervised_micro_live_dry_run_plan,
)
from src.supervised_micro_live_preflight import (
    PreflightInputs,
    PreflightState,
    SupervisedMicroLivePreflight,
    build_supervised_micro_live_preflight,
)

_ACKS = (
    "acknowledges_loss_may_exceed_plan",
    "operator_present",
    "robinhood_ui_open",
    "no_second_trade",
)
_EXPECTED_STRATEGY = "orb_long_v0-0.1.0"
_WATCHLIST = ("AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "NFLX")


class CandidateHandoffError(ValueError):
    """A candidate-to-handoff prerequisite failed closed."""


@dataclass(frozen=True)
class ReadOnlyInputs:
    snapshot: Any
    account_fingerprint_reference: str
    suspension_observation: SuspensionObservation
    observed_at: datetime


@dataclass(frozen=True)
class CandidateHandoffArtifacts:
    candidate: ShadowTradingDecision
    review: ShadowCandidateCommissioningReview
    dry_run: SupervisedMicroLiveDryRunPlan
    preflight: SupervisedMicroLivePreflight
    live_preflight: Any
    safety_evaluation: AttendedSafetyEvaluationResult
    handoff: AttendedCommissioningHandoff


def _as_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise CandidateHandoffError(f"candidate {field} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CandidateHandoffError(f"candidate {field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateHandoffError(f"candidate {field} must be timezone-aware")
    return parsed


def _as_decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise CandidateHandoffError(f"candidate {field} is required")
    try:
        return Decimal(value)
    except Exception as exc:
        raise CandidateHandoffError(f"candidate {field} is invalid") from exc


def _candidate_from_evidence(raw: Mapping[str, Any]) -> ShadowTradingDecision:
    if raw.get("run_mode") != "LIVE_SHADOW" or raw.get("provider") != "alpaca":
        raise CandidateHandoffError("requires authentic LIVE_SHADOW Alpaca evidence")
    summary = raw.get("candidate_summary")
    if not isinstance(summary, Mapping) or summary.get("candidate_count") != 1:
        raise CandidateHandoffError("candidate evidence must contain exactly one candidate")
    candidates = summary.get("candidate_decisions")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], Mapping):
        raise CandidateHandoffError("candidate evidence is ambiguous")
    decision = raw.get("shadow_trading_decision")
    if not isinstance(decision, Mapping) or decision.get("decision_type") != "LONG_CANDIDATE":
        raise CandidateHandoffError("candidate decision must be LONG_CANDIDATE")
    if candidates[0].get("decision_id") != decision.get("decision_id"):
        raise CandidateHandoffError("candidate summary and decision identity disagree")
    symbol = decision.get("symbol")
    if not isinstance(symbol, str) or symbol not in _WATCHLIST:
        raise CandidateHandoffError("candidate symbol is outside the bounded watchlist")
    if decision.get("strategy_version") != _EXPECTED_STRATEGY:
        raise CandidateHandoffError("candidate strategy version is not orb_long_v0")
    if raw.get("strategy_version") != _EXPECTED_STRATEGY:
        raise CandidateHandoffError("live-shadow strategy version is not orb_long_v0")
    if raw.get("implementation_digest") != decision.get("implementation_digest"):
        raise CandidateHandoffError("live-shadow and candidate implementation digests disagree")
    if not isinstance(raw.get("reviewed_sha"), str) or not raw["reviewed_sha"]:
        raise CandidateHandoffError("live-shadow reviewed source SHA is required")
    try:
        return ShadowTradingDecision(
            shadow_run_id=str(decision["shadow_run_id"]),
            decision_id=str(decision["decision_id"]),
            symbol=symbol,
            trading_date=date.fromisoformat(str(decision["trading_date"])),
            strategy_version=str(decision["strategy_version"]),
            implementation_digest=str(decision["implementation_digest"]),
            market_data_digest=str(decision["market_data_digest"]),
            opening_range_identity=str(decision["opening_range_identity"]),
            signal_timestamp=_as_datetime(decision.get("signal_timestamp"), "signal_timestamp"),
            decision_type=ShadowDecisionType(str(decision["decision_type"])),
            proposed_entry=_as_decimal(decision.get("proposed_entry"), "proposed_entry"),
            proposed_stop=_as_decimal(decision.get("proposed_stop"), "proposed_stop"),
            proposed_target=_as_decimal(decision.get("proposed_target"), "proposed_target", optional=True),
            reason=str(decision["reason"]),
            market_data_quality=MarketDataQuality(str(decision["market_data_quality"])),
            exchange_session=str(decision["exchange_session"]),
            created_at=_as_datetime(decision.get("created_at"), "created_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CandidateHandoffError("candidate evidence is incomplete or invalid") from exc


async def _observe_read_only_inputs(*, release_sha: str, suspension_db: Path) -> ReadOnlyInputs:
    """Reuse the established production read-only snapshot/suspension path."""
    commissioned = CommissionedAccount.from_config(load_config(), local_config=load_local_override())
    snapshot = await AccountStateReader().read_snapshot()
    commissioned.verify(snapshot.metadata.account_fingerprint)
    observed_at = datetime.now(timezone.utc)
    identity = CommissioningSuspensionIdentity.derive(
        release_sha=release_sha,
        account_fingerprint_reference=commissioned.expected_fingerprint,
    )
    with CommissioningSuspensionStateProvider(database_path=suspension_db, identity=identity) as provider:
        suspension = observe_suspension(provider, observed_at=observed_at)
    return ReadOnlyInputs(snapshot, commissioned.expected_fingerprint, suspension, observed_at)


def _acknowledgements(values: list[str]) -> OperatorAcknowledgements:
    if tuple(sorted(set(values))) != tuple(sorted(_ACKS)) or len(values) != len(_ACKS):
        raise CandidateHandoffError("all four exact operator acknowledgements must be explicitly supplied")
    return OperatorAcknowledgements(True, True, True, True)


def _validate_runtime(*, release_sha: str, candidate: ShadowTradingDecision, root: Path) -> str:
    if release_sha != resolve_head_sha(root):
        raise CandidateHandoffError("release SHA does not match the current reviewed source")
    trusted = checkpoint_implementation_digest(root)
    if candidate.implementation_digest != trusted:
        raise CandidateHandoffError("candidate implementation digest does not match the current runtime")
    return trusted


def build_candidate_handoff(
    raw_evidence: Mapping[str, Any], *, release_sha: str, acknowledgements: OperatorAcknowledgements,
    observer: Callable[..., Any] = _observe_read_only_inputs, root: Path = ROOT,
    suspension_db: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CandidateHandoffArtifacts:
    """Compose existing evidence builders; the observer is the sole I/O seam."""
    candidate = _candidate_from_evidence(raw_evidence)
    if raw_evidence.get("reviewed_sha") != release_sha:
        raise CandidateHandoffError("live-shadow reviewed source SHA does not match the reviewed release")
    trusted = _validate_runtime(release_sha=release_sha, candidate=candidate, root=root)
    if acknowledgements != OperatorAcknowledgements(True, True, True, True):
        raise CandidateHandoffError("all four explicit operator acknowledgements are required")

    # Review and dry run are pure and intentionally precede account observation.
    reviewed_at = clock()
    review = build_shadow_candidate_commissioning_review(
        candidate, reviewed_at=reviewed_at, trusted_implementation_digest=trusted, root=root,
    )
    if review.readiness_state is not ReviewReadinessState.REVIEW_READY:
        raise CandidateHandoffError("candidate review is not ready")
    dry_run = build_supervised_micro_live_dry_run_plan(
        review, trusted_implementation_digest=trusted, root=root,
    )
    if dry_run.dry_run_state is not DryRunState.DRY_RUN_READY:
        raise CandidateHandoffError("dry-run plan is not ready")

    observed = asyncio.run(observer(release_sha=release_sha, suspension_db=suspension_db))
    if not isinstance(observed, ReadOnlyInputs):
        raise CandidateHandoffError("read-only observation returned invalid evidence")
    if observed.suspension_observation.decision.state.value != "CLEAR":
        raise CandidateHandoffError("fresh suspension assessment is not CLEAR")
    observed_at = observed.observed_at
    dry_run = build_supervised_micro_live_dry_run_plan(
        review,
        account_fingerprint_reference=observed.account_fingerprint_reference,
        trusted_implementation_digest=trusted,
        root=root,
    )
    if dry_run.dry_run_state is not DryRunState.DRY_RUN_READY:
        raise CandidateHandoffError("account-bound dry-run plan is not ready")
    preflight = build_supervised_micro_live_preflight(
        review,
        dry_run,
        inputs=PreflightInputs(
            evaluated_at=observed_at,
            acknowledgements=(True, True, True, True),
            source_hash_verified=True,
            release_sha=release_sha,
            account_fingerprint_matched=True,
            account_fingerprint_reference=observed.account_fingerprint_reference,
            suspension_state="CLEAR",
        ),
        trusted_implementation_digest=trusted,
        root=root,
    )
    if preflight.preflight_state is not PreflightState.PREFLIGHT_READY:
        raise CandidateHandoffError("preflight is not ready")
    live_preflight = build_live_attended_preflight_result(
        preflight,
        snapshot=observed.snapshot,
        expected_account_fingerprint=observed.account_fingerprint_reference,
        suspension_observation=observed.suspension_observation,
        evaluated_at=observed_at,
        trusted_implementation_digest=trusted,
        root=root,
    )
    if live_preflight.result_state.value != "LIVE_PREFLIGHT_READY":
        raise CandidateHandoffError("live preflight is blocked")
    safety_inputs = AttendedSafetyInputs(
        decision_time=observed_at,
        operator_acknowledgements=acknowledgements,
        operator_attended=True,
        source_hash_verified=True,
        release_sha=release_sha,
    )
    safety = build_attended_safety_evaluation_result(
        live_preflight, inputs=safety_inputs, trusted_implementation_digest=trusted, root=root,
    )
    if safety.evaluation_state is not AttendedSafetyEvaluationState.SAFETY_EVALUATED_ALLOW:
        raise CandidateHandoffError("attended safety evaluation is not ALLOW")
    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=True,
        operator_acknowledgements=acknowledgements,
        source_hash_verified=True,
        release_sha=release_sha,
        implementation_digest=trusted,
        account_fingerprint_matched=True,
        account_fingerprint_reference=observed.account_fingerprint_reference,
        suspension_state="CLEAR",
    )
    decision = evaluate_execution_safety(ExecutionSafetyContext(
        context_version="1", decision_time=observed_at, run_id=live_preflight.live_preflight_id,
        action=ExecutionAction.ENTRY_PLACE, symbol=candidate.symbol, side="buy", order_type="market",
        quantity="1", stop_price=None, order_id=None, governance_evidence=evidence,
    ))
    handoff = build_attended_commissioning_handoff(
        safety, dry_run=dry_run, decision=decision, evaluated_at=observed_at, release_sha=release_sha,
        trusted_implementation_digest=trusted, root=root,
    )
    if handoff.handoff_state is not AttendedCommissioningHandoffState.HANDOFF_READY:
        raise CandidateHandoffError("attended commissioning handoff is blocked")
    return CandidateHandoffArtifacts(candidate, review, dry_run, preflight, live_preflight, safety, handoff)


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if not isinstance(value, Decimal) else format(value, "f")
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def persist_artifacts(artifacts: CandidateHandoffArtifacts, output_root: Path) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    values = {
        "review": artifacts.review,
        "dry_run": artifacts.dry_run,
        "preflight": artifacts.preflight,
        "live_preflight": artifacts.live_preflight,
        "safety_evaluation": artifacts.safety_evaluation,
        "handoff": artifacts.handoff,
    }
    paths: dict[str, Path] = {}
    for name, value in values.items():
        path = output_root / f"{name}.json"
        path.write_text(json.dumps(asdict(value), default=_json_default, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        paths[name] = path
    return paths


def _next_command(*, live_shadow: Path, handoff: Path, release_sha: str, suspension_db: Path) -> str:
    acknowledgements = " ".join(f"--ack {ack}" for ack in _ACKS)
    return (
        "python scripts/run_production_read_only_rehearsal.py --production-read-only "
        f"--live-shadow {live_shadow} --handoff {handoff} --release-sha {release_sha} "
        f"--suspension-db {suspension_db} {acknowledgements}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-shadow", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--suspension-db", type=Path, required=True)
    parser.add_argument("--ack", action="append", default=[])
    args = parser.parse_args(argv)
    if not args.suspension_db.is_absolute():
        raise CandidateHandoffError("suspension database must be an absolute path outside the repository")
    suspension_db = args.suspension_db.resolve()
    if ROOT.resolve() in suspension_db.parents:
        raise CandidateHandoffError("suspension database must be an absolute path outside the repository")
    acks = _acknowledgements(args.ack)
    raw = json.loads(args.live_shadow.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise CandidateHandoffError("live-shadow evidence must be a JSON object")

    async def observer(**kwargs: Any) -> ReadOnlyInputs:
        return await _observe_read_only_inputs(**{**kwargs, "suspension_db": suspension_db})

    artifacts = build_candidate_handoff(raw, release_sha=args.release_sha, acknowledgements=acks, observer=observer, suspension_db=suspension_db)
    output_root = Path("artifacts") / "commissioning-handoff" / artifacts.handoff.handoff_id
    paths = persist_artifacts(artifacts, output_root)
    print(json.dumps({
        "result": "HANDOFF_READY_FOR_PRODUCTION_READ_ONLY_REHEARSAL",
        "candidate_symbol": artifacts.candidate.symbol,
        "candidate_decision_id": artifacts.candidate.decision_id,
        "review_id": artifacts.review.review_id,
        "preflight_id": artifacts.preflight.preflight_id,
        "live_preflight_id": artifacts.live_preflight.live_preflight_id,
        "safety_evaluation_id": artifacts.safety_evaluation.evaluation_id,
        "safety_result": artifacts.safety_evaluation.safety_result,
        "handoff_id": artifacts.handoff.handoff_id,
        "handoff_artifact": str(paths["handoff"]),
        "broker_write_count": 0,
        "execution_authorized": False,
        "activation_approved": False,
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
        "authority_ceiling": AUTHORITY_CEILING,
        "next_command": _next_command(live_shadow=args.live_shadow, handoff=paths["handoff"], release_sha=args.release_sha, suspension_db=suspension_db),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
