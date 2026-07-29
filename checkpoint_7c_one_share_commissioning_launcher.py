"""Attended Checkpoint 7C one-share commissioning entry point.

The default invocation remains inert. Real-session execution is reachable only
through the complete CLI authorization surface and the reviewed tracked session
binding. Importing this module performs no broker, MCP, configuration, or token
I/O.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hmac
import subprocess
import sys
import time as runtime_time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.canonical_source_hash import (
    CanonicalSourceHashError,
    canonical_source_sha256,
)
from src.config import load_config
from src.one_share_commissioning import (
    AtomicEvidenceStore, AuthorizationPhraseMismatchError,
    AuthorizationReaderError, CommissioningDiagnosticError, CommissioningError,
    EASTERN,
    ENTRY_AUTHORIZATION_PHRASE,
    FLATTEN_AUTHORIZATION_PHRASE,
    MAX_AUTHORIZATION_WINDOW_SECONDS, PLANNED_RISK_USD,
    QUANTITY,
    RUN_ID_PATTERN,
    RuntimeAuthorization,
    State,
    SYMBOL,
)
from src.one_share_commissioning_orchestrator import (
    CommissioningTimePolicy,
    OneShareCommissioningOrchestrator,
    OrchestrationResult,
    entry_arguments,
    exit_arguments,
    stop_arguments,
    validate_commissioning_entry_time,
)
from src.robinhood_commissioning_adapter import RobinhoodCommissioningAdapter
from src.robinhood_commissioning_session import (
    CommissionedAccountResolver,
    RobinhoodCommissioningSessionBinding,
    silent_robinhood_session_factory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent
EXPECTED_BRANCH = "main"
REVIEWED_HASHES: Mapping[str, str] = {
    "src/broker_identity.py":
        "d5ffab0c3761385bf1c744ef5c67385c970cdec52584c7fa9f059d0563622812",
    "src/broker_snapshot.py":
        "0dc401502b81d9df902e01cec9d7c57f221405316b24b848f5d3b69e69c083fd",
    "src/canonical_source_hash.py":
        "28b4c9d5154310fcc686f3cfaee6ef3ec2d38a872f5c0da2fea1570fd179168c",
    "src/config.py":
        "37669854c3e1dae3c282e787c0af22da9c89ce21fb790a482f4edb2e2db7f1c3",
    "src/one_share_commissioning.py":
        "563b5ad79db52187989866a0df7dc650b0fb3bba917812d58a16f86b62fcb487",
    "src/robinhood_commissioning_adapter.py":
        "1b47fce3faa003e8485f43186991904d539ab8f839f5d49781609bfe22be5b15",
    "src/one_share_commissioning_orchestrator.py":
        "b124a474aa283a12dc1e0c03b56b91b806685dfc999813131e68eea413f8f7fb",
    "src/robinhood_commissioning_session.py":
        "a7e0367de955eb26695f6e61c99b0c99cfdab869c042cd9fcddf209b7065c4e1",
    "src/robinhood_client.py":
        "8e00f808dda3e8741a2fccc6ae5edb42deb4a3761af5eae39304c601cfa1f39e",
}
REAL_SESSION_RUNTIME_FLAG = "ENABLE_ATTENDED_ONE_SHARE_COMMISSIONING"
LIVE_CHECKPOINT_AUTHORIZATION_FLAG = "AUTHORIZE_CHECKPOINT_7C_LIVE"
RETIRED_LIVE_RUN_IDS = frozenset({
    "cp7c-live-aapl-one-share-20260729-02",
    "cp7c-live-aapl-one-share-20260728-03",
    "cp7c-live-aapl-one-share-20260728-04",
    "cp7c-live-aapl-one-share-20260728-05",
})


def _git_output(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def verify_git_posture(root: Path) -> None:
    """Require the clean, synchronized main checkout containing this launcher."""

    try:
        if root.resolve() != REPOSITORY_ROOT.resolve():
            raise CommissioningError("commissioning repository root mismatch")
        branch = _git_output(root, "branch", "--show-current")
        if branch != EXPECTED_BRANCH:
            raise CommissioningError("commissioning requires the main branch")
        head = _git_output(root, "rev-parse", "HEAD")
        origin_main = _git_output(root, "rev-parse", "origin/main")
        if head != origin_main:
            raise CommissioningError("local main is not synchronized with origin/main")
        if _git_output(root, "status", "--porcelain", "--untracked-files=no"):
            raise CommissioningError("tracked commissioning tree is not clean")
        if _git_output(root, "diff", "--cached", "--name-only"):
            raise CommissioningError("commissioning staged area is not empty")
    except CommissioningError:
        raise
    except Exception:
        raise CommissioningError("commissioning Git posture is unavailable") from None


def verify_reviewed_hashes(root: Path, expected: Mapping[str, str]) -> None:
    if not expected:
        raise CommissioningError("reviewed hash manifest is not approved")
    for relative, digest in expected.items():
        try:
            actual = canonical_source_sha256(root / relative)
        except CanonicalSourceHashError as exc:
            raise CommissioningError("reviewed file hash could not be verified") from exc
        if actual != digest:
            raise CommissioningError("reviewed file hash mismatch")


def verify_strategy_disabled(root: Path) -> None:
    try:
        config = load_config(root / "config" / "strategy_v0.yaml")
    except Exception:
        raise CommissioningError("strategy posture is unavailable") from None
    if config.get("strategy", {}).get("enabled") is not False:
        raise CommissioningError("strategy.enabled must remain false")


def verify_runtime_posture(root: Path) -> None:
    verify_git_posture(root)
    verify_reviewed_hashes(root, REVIEWED_HASHES)
    verify_strategy_disabled(root)


def sanitized_review_summary(review: Mapping[str, Any]) -> dict[str, Any]:
    """Return only policy facts and types; never echo private or price values."""

    return {
        "symbol_match": review.get("symbol") == SYMBOL,
        "quantity_match": str(review.get("quantity")) == "1",
        "side": review.get("side")
        if review.get("side") in {"buy", "sell"}
        else "unknown",
        "order_type": review.get("type")
        if review.get("type") in {"market", "stop", "stop_market"}
        else "unknown",
        "planned_risk_usd": int(PLANNED_RISK_USD),
        "review_json_type": type(review).__name__,
    }


def sanitized_emergency_handoff(payload: Any) -> None:
    """Display only the reviewed emergency fields, never broker identity data."""

    print("emergency_handoff=OPERATOR_REQUIRED")
    print(f"current_state={getattr(payload, 'current_state', 'OPERATOR_REQUIRED')}")
    print(f"reason_code={getattr(payload, 'reason_code', 'FAIL_CLOSED')}")
    print("assume manual Robinhood control; do not retry any ambiguous write")


def is_regular_market_hours(now: datetime) -> bool:
    if now.tzinfo != EASTERN or now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time().replace(tzinfo=None) < time(16, 0)


@dataclass(frozen=True)
class LiveRunRequest:
    run_id: str
    evidence_root: Path
    execution_date: date
    attended_start: time
    entry_cutoff: time
    mandatory_risk_off: time
    verified_flat_deadline: time
    attended_end: time
    symbol: str
    quantity: Decimal
    planned_risk_usd: Decimal
    acknowledges_loss_may_exceed_plan: bool
    operator_present: bool
    robinhood_ui_open: bool
    no_second_trade: bool
    real_session_flag: str
    live_checkpoint_flag: str

    def time_policy(self) -> CommissioningTimePolicy:
        def combine(value: time) -> datetime:
            if value.tzinfo is not None:
                raise CommissioningError(
                    'commissioning time values must not include an offset'
                )
            return datetime.combine(self.execution_date, value, tzinfo=EASTERN)

        return CommissioningTimePolicy(
            execution_date=self.execution_date,
            entry_start=combine(self.attended_start),
            entry_cutoff=combine(self.entry_cutoff),
            mandatory_risk_off=combine(self.mandatory_risk_off),
            verified_flat_deadline=combine(self.verified_flat_deadline),
            attended_end=combine(self.attended_end),
        )


def validate_live_run_request(request: LiveRunRequest, *, now: datetime) -> None:
    if request.real_session_flag != REAL_SESSION_RUNTIME_FLAG:
        raise CommissioningError("real-session runtime flag is absent")
    if request.live_checkpoint_flag != LIVE_CHECKPOINT_AUTHORIZATION_FLAG:
        raise CommissioningError("live-checkpoint authorization flag is absent")
    if not RUN_ID_PATTERN.fullmatch(request.run_id):
        raise CommissioningError("run ID is invalid")
    if request.run_id in RETIRED_LIVE_RUN_IDS:
        raise CommissioningError("run ID is permanently retired")
    policy = request.time_policy()
    if (
        request.symbol != SYMBOL
        or request.quantity != QUANTITY
        or request.planned_risk_usd != PLANNED_RISK_USD
    ):
        raise CommissioningError("commissioning fixed policy mismatch")
    if not all(
        (
            request.acknowledges_loss_may_exceed_plan,
            request.operator_present,
            request.robinhood_ui_open,
            request.no_second_trade,
        )
    ):
        raise CommissioningError("commissioning acknowledgments are incomplete")
    validate_commissioning_entry_time(now, policy)
    store = AtomicEvidenceStore(request.evidence_root, request.run_id)
    if store.completed.exists() or store.partial.exists():
        raise CommissioningError("run ID has already been used")


@dataclass
class InteractiveAuthorizationProvider:
    request: LiveRunRequest
    now_eastern: Callable[[], datetime]
    secret_reader: Callable[[str], str] = getpass.getpass
    _used: set[str] = field(default_factory=set, init=False)

    def __call__(self, stage: str) -> RuntimeAuthorization:
        if stage not in {"entry", "flatten"} or stage in self._used:
            raise CommissioningError("runtime authorization is not reusable")
        current = self.now_eastern()
        if current.tzinfo != EASTERN or current.date() != self.request.execution_date:
            raise CommissioningError("runtime authorization date or timezone mismatch")
        if stage == "entry":
            validate_commissioning_entry_time(current, self.request.time_policy())
            expected = ENTRY_AUTHORIZATION_PHRASE
            prompt = "Enter the exact single-use entry authorization phrase: "
        else:
            expected = FLATTEN_AUTHORIZATION_PHRASE
            prompt = "Enter the exact separate flatten authorization phrase: "
        try:
            supplied = self.secret_reader(prompt)
            accepted = isinstance(supplied, str) and hmac.compare_digest(
                supplied, expected
            )
        except Exception:
            raise AuthorizationReaderError("AUTHORIZATION_INPUT_FAILED") from None
        finally:
            if "supplied" in locals():
                supplied = ""
        if not accepted:
            raise AuthorizationPhraseMismatchError(
                "AUTHORIZATION_INPUT_FAILED"
            )
        accepted_at = self.now_eastern()
        if accepted_at.tzinfo != EASTERN or accepted_at.date() != self.request.execution_date:
            raise AuthorizationReaderError("AUTHORIZATION_INPUT_FAILED")
        if stage == "entry":
            validate_commissioning_entry_time(accepted_at, self.request.time_policy())
            cutoff = datetime.combine(
                self.request.execution_date,
                self.request.entry_cutoff,
                tzinfo=EASTERN,
            )
            start = accepted_at
            end = min(
                accepted_at + timedelta(seconds=MAX_AUTHORIZATION_WINDOW_SECONDS),
                cutoff,
            )
        else:
            start = accepted_at
            end = accepted_at + timedelta(seconds=MAX_AUTHORIZATION_WINDOW_SECONDS)
        self._used.add(stage)
        return RuntimeAuthorization(
            run_id=self.request.run_id,
            eastern_date=self.request.execution_date,
            window_start=start,
            window_end=end,
            phrase=expected,
            symbol=self.request.symbol,
            quantity=self.request.quantity,
            planned_risk_usd=self.request.planned_risk_usd,
            acknowledges_loss_may_exceed_plan=(
                self.request.acknowledges_loss_may_exceed_plan
            ),
            operator_present=self.request.operator_present,
            robinhood_ui_available=self.request.robinhood_ui_open,
            no_second_trade=self.request.no_second_trade,
        )


async def execute_commissioning(
    *,
    root: Path,
    session: Any,
    commissioned_account_number: str,
    run_id: str,
    evidence_root: Path,
    monotonic: Any,
    sleep: Any,
    now_eastern: Any,
    authorization_provider: Any,
    emergency_handoff: Any,
    strategy_enabled: bool,
    time_policy: CommissioningTimePolicy,
    mode: str = "blocked",
    runtime_flag: str = "",
    real_session_authorized: bool = False,
) -> OrchestrationResult:
    """Execute only the existing explicitly injected offline fixture path."""

    if mode != "fake":
        raise CommissioningError(
            "real sessions require the tracked commissioning session binding"
        )
    if getattr(session, "offline_fixture", False) is not True:
        raise CommissioningError(
            "fake mode requires an explicitly marked offline fixture"
        )
    verify_runtime_posture(root)
    if strategy_enabled:
        raise CommissioningError("strategy.enabled must remain false")
    current = now_eastern()
    if current.tzinfo != EASTERN or not is_regular_market_hours(current):
        raise CommissioningError(
            "current Eastern time is outside regular market hours"
        )
    store = AtomicEvidenceStore(evidence_root, run_id)
    adapter = RobinhoodCommissioningAdapter(session, commissioned_account_number)
    orchestrator = OneShareCommissioningOrchestrator(
        run_id=run_id,
        adapter=adapter,
        monotonic=monotonic,
        sleep=sleep,
        now_eastern=now_eastern,
        authorization_provider=authorization_provider,
        evidence_store=store,
        emergency_handoff=emergency_handoff,
        strategy_enabled=strategy_enabled,
        regular_market_hours=True,
        time_policy=time_policy,
    )
    result = await orchestrator.run()
    print(f"commissioning_state={result.state.value}")
    print("planned_risk_usd=15; actual loss may exceed planned risk")
    return result


async def execute_real_session(
    request: LiveRunRequest,
    *,
    root: Path = REPOSITORY_ROOT,
    now_eastern: Callable[[], datetime],
    session_factory: Callable[[], Any] = silent_robinhood_session_factory,
    commissioned_account_resolver: Callable[[Any], Any] | None = None,
    authorization_provider: Callable[[str], Any] | None = None,
    evidence_store: Any | None = None,
    emergency_handoff: Callable[[Any], Any] | None = None,
    binding_factory: Callable[..., Any] = RobinhoodCommissioningSessionBinding,
    monotonic: Callable[[], float] = runtime_time.monotonic,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> OrchestrationResult:
    """Run one reviewed binding after every non-broker gate passes."""

    verify_runtime_posture(root)
    current = now_eastern()
    validate_live_run_request(request, now=current)
    provider = authorization_provider or InteractiveAuthorizationProvider(
        request, now_eastern
    )
    store = evidence_store or AtomicEvidenceStore(
        request.evidence_root, request.run_id
    )
    handoff = emergency_handoff or sanitized_emergency_handoff
    resolver = commissioned_account_resolver or CommissionedAccountResolver()
    binding = binding_factory(
        run_id=request.run_id,
        time_policy=request.time_policy(),
        session_factory=session_factory,
        commissioned_account_resolver=resolver,
        monotonic=monotonic,
        sleep=sleep,
        now_eastern=now_eastern,
        authorization_provider=provider,
        evidence_store=store,
        emergency_handoff=handoff,
    )
    result = await binding.run()
    print(f"commissioning_state={result.state.value}")
    print("planned_risk_usd=15; actual loss may exceed planned risk")
    return result


def run_fake_demo(**kwargs: Any) -> OrchestrationResult:
    """Run only with an explicitly injected offline fake session."""

    kwargs["mode"] = "fake"
    return asyncio.run(execute_commissioning(**kwargs))


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from None


def _parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("time must use HH:MM[:SS]") from None
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError(
            "time must use HH:MM[:SS] without an offset"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--real-session", action="store_true", required=True)
    parser.add_argument(
        "--authorize-live-checkpoint", action="store_true", required=True
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--execution-date", required=True, type=_parse_date)
    parser.add_argument("--attended-start", required=True, type=_parse_time)
    parser.add_argument("--entry-cutoff", required=True, type=_parse_time)
    parser.add_argument("--mandatory-risk-off", required=True, type=_parse_time)
    parser.add_argument("--verified-flat-deadline", required=True, type=_parse_time)
    parser.add_argument("--attended-end", required=True, type=_parse_time)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--quantity", required=True, type=Decimal)
    parser.add_argument("--planned-risk-usd", required=True, type=Decimal)
    parser.add_argument(
        "--acknowledge-loss-may-exceed-plan", action="store_true", required=True
    )
    parser.add_argument("--operator-present", action="store_true", required=True)
    parser.add_argument("--robinhood-ui-open", action="store_true", required=True)
    parser.add_argument("--no-second-trade", action="store_true", required=True)
    return parser


def request_from_namespace(value: argparse.Namespace) -> LiveRunRequest:
    return LiveRunRequest(
        run_id=value.run_id,
        evidence_root=value.evidence_root,
        execution_date=value.execution_date,
        attended_start=value.attended_start,
        entry_cutoff=value.entry_cutoff,
        mandatory_risk_off=value.mandatory_risk_off,
        verified_flat_deadline=value.verified_flat_deadline,
        attended_end=value.attended_end,
        symbol=value.symbol,
        quantity=value.quantity,
        planned_risk_usd=value.planned_risk_usd,
        acknowledges_loss_may_exceed_plan=(
            value.acknowledge_loss_may_exceed_plan
        ),
        operator_present=value.operator_present,
        robinhood_ui_open=value.robinhood_ui_open,
        no_second_trade=value.no_second_trade,
        real_session_flag=(
            REAL_SESSION_RUNTIME_FLAG if value.real_session else ""
        ),
        live_checkpoint_flag=(
            LIVE_CHECKPOINT_AUTHORIZATION_FLAG
            if value.authorize_live_checkpoint
            else ""
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("BLOCKED: complete attended real-session authorization required")
        print("planned_risk_usd=15; actual loss may exceed planned risk")
        return 2
    try:
        request = request_from_namespace(build_parser().parse_args(arguments))
        result = asyncio.run(
            execute_real_session(
                request,
                now_eastern=lambda: datetime.now(EASTERN),
            )
        )
    except SystemExit:
        print("commissioning_result=FAILED_CLOSED")
        print("assume manual Robinhood control if any position may exist")
        return 2
    except CommissioningDiagnosticError as error:
        print("commissioning_result=FAILED_CLOSED")
        print(f"failure_category={error.category}")
        print(f"failure_stage={error.stage}")
        print(f"sanitized_exception_type={error.exception_type}")
        print("assume manual Robinhood control if any position may exist")
        return 2
    except Exception:
        print("commissioning_result=FAILED_CLOSED")
        print("assume manual Robinhood control if any position may exist")
        return 2
    if result.state is not State.COMPLETE:
        print("assume manual Robinhood control; do not rerun this Run ID")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
