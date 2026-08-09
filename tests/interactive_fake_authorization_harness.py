"""Strictly fake interactive authorization acceptance harness.

This module deliberately does not import the real commissioning session factory
or Robinhood client. Its only session, account, adapter, orders, and positions
are in-process test fixtures.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import checkpoint_7c_one_share_commissioning_launcher as launcher
from src.one_share_commissioning import (
    AtomicEvidenceStore, EASTERN, ENTRY_AUTHORIZATION_STAGE,
    FLATTEN_AUTHORIZATION_STAGE, State,
)
from src.one_share_commissioning_orchestrator import OneShareCommissioningOrchestrator
from tests.test_one_share_commissioning_orchestrator import (
    Clock, DEFAULT_GOVERNANCE_EVIDENCE, FakeAdapter, TIME_POLICY,
)

PRIVATE_FAKE_INPUT = "PRIVATE_FAKE_INPUT_SENTINEL"
NOW = datetime(2026, 7, 29, 10, 40, tzinfo=EASTERN)
FAKE_ACCOUNT = "OFFLINE-FAKE-COMMISSIONED-ACCOUNT"


class BoundFakeAdapter(FakeAdapter):
    commissioned_account_number = FAKE_ACCOUNT


class FakeSessionFactory:
    def __init__(self) -> None:
        self.created = 0
        self.closed = 0

    @asynccontextmanager
    async def __call__(self):
        self.created += 1
        try:
            yield SimpleNamespace(fake_session=True)
        finally:
            self.closed += 1


def fake_request(root: Path, run_id: str) -> launcher.LiveRunRequest:
    return launcher.LiveRunRequest(
        run_id=run_id, evidence_root=root,
        execution_date=date(2026, 7, 29),
        attended_start=time(10, 35), entry_cutoff=time(11, 0),
        mandatory_risk_off=time(11, 10),
        verified_flat_deadline=time(11, 15), attended_end=time(11, 20), symbol="AAPL", quantity=Decimal("1"),
        planned_risk_usd=Decimal("15"), acknowledges_loss_may_exceed_plan=True,
        operator_present=True, robinhood_ui_open=True, no_second_trade=True,
        real_session_flag="FAKE_ONLY_NO_REAL_SESSION",
        live_checkpoint_flag="FAKE_ONLY_NO_LIVE_CHECKPOINT",
    )


def read_fake_terminal(_prompt: str) -> str:
    supplied = sys.stdin.readline()
    if supplied == "":
        raise EOFError
    return supplied.rstrip("\r\n")


async def run_case(case: str, root: Path, run_id: str) -> int:
    store = AtomicEvidenceStore(root, run_id)
    if store.completed.exists() or store.partial.exists():
        print("fake_result=RUN_ID_REUSE_REJECTED")
        print("session_created=0")
        print("entry_place_attempts=0")
        print("stop_place_attempts=0")
        print("stop_cancel_attempts=0")
        print("exit_place_attempts=0")
        return 0

    request = fake_request(root, run_id)
    entry_provider = launcher.InteractiveAuthorizationProvider(
        request=request, now_eastern=lambda: NOW,
        secret_reader=read_fake_terminal,
    )
    flatten_provider = launcher.InteractiveAuthorizationProvider(
        request=request, now_eastern=lambda: NOW,
        secret_reader=lambda _prompt: "y",
    )
    entry_consumptions = 0

    def authorization_provider(stage: str):
        nonlocal entry_consumptions
        if stage == "entry":
            entry_consumptions += 1
            return entry_provider(stage)
        return flatten_provider(stage)

    session_factory = FakeSessionFactory()
    clock = Clock()
    emergency_handoffs: list[object] = []
    adapter = BoundFakeAdapter()
    counts = {"resolver": 0, "adapter": 0, "orchestrator": 0}

    async with session_factory() as fake_session:
        if not getattr(fake_session, "fake_session", False):
            raise RuntimeError("fake session invariant failed")
        counts["resolver"] += 1
        fake_account = FAKE_ACCOUNT
        counts["adapter"] += 1
        if adapter.commissioned_account_number != fake_account:
            raise RuntimeError("fake account binding invariant failed")
        counts["orchestrator"] += 1
        orchestrator = OneShareCommissioningOrchestrator(
            run_id=run_id, adapter=adapter,
            monotonic=clock.monotonic, sleep=clock.sleep,
            now_eastern=lambda: NOW,
            authorization_provider=authorization_provider,
            evidence_store=store, emergency_handoff=emergency_handoffs.append,
            strategy_enabled=False, regular_market_hours=True,
            time_policy=TIME_POLICY,
            governance_evidence=DEFAULT_GOVERNANCE_EVIDENCE,
        )
        result = await orchestrator.run()
    evidence_path = (
        store.completed / "commissioning_evidence.json"
        if store.completed.exists()
        else store.partial / "commissioning_evidence.json"
    )
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes.decode("utf-8"))
    rendered = evidence_bytes.decode("utf-8")
    validations = evidence.get("boolean_validations", {})
    counters = {
        "entry_place": int(validations.get("entry_write_before", False)),
        "stop_place": int(validations.get("stop_write_before", False)),
        "stop_cancel": int(validations.get("cancel_write_before", False)),
        "exit_place": int(validations.get("exit_write_before", False)),
    }

    print(f"fake_case={case}")
    print(f"fake_state={result.state.value}")
    print(f"failure_category={evidence.get('failure_category', 'NONE')}")
    print(f"sanitized_exception_type={evidence.get('sanitized_exception_type', 'NONE')}")
    print(f"write_boundary_reached={str(any(counters.values())).lower()}")
    print(f"entry_place_attempts={counters.get('entry_place', 0)}")
    print(f"stop_place_attempts={counters.get('stop_place', 0)}")
    print(f"stop_cancel_attempts={counters.get('stop_cancel', 0)}")
    print(f"exit_place_attempts={counters.get('exit_place', 0)}")
    print(f"session_created={session_factory.created}")
    print(f"session_closed={session_factory.closed}")
    print(f"adapter_created={counts['adapter']}")
    print(f"orchestrator_created={counts['orchestrator']}")
    print(f"authorization_consumed={entry_consumptions}")
    print(f"emergency_handoffs={len(emergency_handoffs)}")
    print(f"verified_flat={str(evidence.get('boolean_validations', {}).get('verified_flat', False)).lower()}")
    print(f"unresolved_orders={evidence.get('record_counts', {}).get('final_related_orders', 0)}")
    leakage = sum(value in rendered for value in (
        PRIVATE_FAKE_INPUT, FAKE_ACCOUNT,
    ))
    print(f"authorization_input_leakage={leakage}")
    print(f"evidence_sha256={hashlib.sha256(evidence_bytes).hexdigest()}")
    print(f"evidence_bytes={len(evidence_bytes)}")
    print(f"evidence_lines={len(evidence_bytes.splitlines())}")
    accepted_state = (
        case == "valid" and result.state is State.COMPLETE
    ) or (
        case in {"invalid", "eof"} and result.state is State.FAILED_CLOSED
    )
    return 0 if accepted_state else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("invalid", "eof", "valid", "reuse"), required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    return asyncio.run(run_case(arguments.case, arguments.evidence_root, arguments.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
