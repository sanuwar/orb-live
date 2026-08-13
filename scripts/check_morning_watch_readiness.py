"""Report local-only readiness for the canonical morning LIVE_SHADOW watch.

This command does not contact Alpaca or Robinhood, read an account, or create
an authorization. It checks only source/configuration facts needed before the
09:40–11:00 ET watcher window.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.broker_identity import (
    CommissionedAccount,
    CommissionedIdentityError,
    DEFAULT_LOCAL_CONFIG_PATH,
    load_local_override,
)
from src.config import ConfigError, load_config
from src.exchange_calendar import EASTERN, classify
from src.execution_safety_governance import AUTHORITY_CEILING
from src.morning_live_shadow_watcher import DEFAULT_WATCHLIST
from src.read_only_authorization_manifest import (
    PINNED_IMPLEMENTATION_CLOSURE,
    checkpoint_implementation_digest,
    resolve_head_sha,
)


@dataclass(frozen=True)
class MorningReadiness:
    source_sha: str
    implementation_digest: str
    pinned_closure_count: int
    strategy_name: str
    strategy_version: str
    watchlist: tuple[str, ...]
    entry_start: str
    entry_end: str
    market_session: str
    market_reason: str
    alpaca_key_present: bool
    alpaca_secret_present: bool
    local_config_present: bool
    account_fingerprint_resolvable: bool
    suspension_db_status: str
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


def _present(environment: Mapping[str, str], name: str) -> bool:
    return bool(environment.get(name, "").strip())


def _suspension_db_status(path: Path | None, *, root: Path) -> tuple[str, str | None]:
    if path is None:
        return "NOT CHECKED", None
    if not path.is_absolute():
        return "INVALID (must be absolute and outside repository)", "SUSPENSION_DB_PATH_INVALID"
    resolved = path.resolve()
    if resolved == root.resolve() or root.resolve() in resolved.parents:
        return "INVALID (must be outside repository)", "SUSPENSION_DB_PATH_INVALID"
    return "ACCEPTABLE (absolute, outside repository)", None


def assess_morning_readiness(
    *,
    environment: Mapping[str, str] | None = None,
    local_config: Mapping[str, object] | None = None,
    local_config_present: bool | None = None,
    suspension_db: Path | None = None,
    now: datetime | None = None,
    root: Path = ROOT,
) -> MorningReadiness:
    """Collect local readiness facts without any provider or broker boundary."""
    env = os.environ if environment is None else environment
    blockers: list[str] = []
    key_present = _present(env, "ALPACA_API_KEY_ID")
    secret_present = _present(env, "ALPACA_API_SECRET_KEY")
    if not key_present:
        blockers.append("ALPACA_API_KEY_ID_MISSING")
    if not secret_present:
        blockers.append("ALPACA_API_SECRET_KEY_MISSING")

    supplied_local = local_config if local_config is not None else load_local_override()
    local_present = DEFAULT_LOCAL_CONFIG_PATH.exists() if local_config_present is None else local_config_present
    account_resolvable = False
    try:
        config = load_config()
        account = CommissionedAccount.from_config(config, local_config=supplied_local, environment=env)
        account_resolvable = bool(account.expected_fingerprint)
        strategy = config["strategy"]
        signal = config["signal"]
        if strategy.get("enabled") is not False:
            blockers.append("STRATEGY_MUST_REMAIN_DISABLED")
    except (ConfigError, CommissionedIdentityError, KeyError, TypeError):
        config = {}
        strategy = {}
        signal = {}
    if not account_resolvable:
        blockers.append("COMMISSIONED_ACCOUNT_FINGERPRINT_NOT_RESOLVABLE")

    path_status, path_blocker = _suspension_db_status(suspension_db, root=root)
    if path_blocker:
        blockers.append(path_blocker)
    moment = now or datetime.now(timezone.utc)
    classification = classify(moment)
    try:
        source_sha = resolve_head_sha(root)
        digest = checkpoint_implementation_digest(root)
    except Exception:
        source_sha, digest = "UNAVAILABLE", "UNAVAILABLE"
        blockers.append("SOURCE_IDENTITY_UNAVAILABLE")

    return MorningReadiness(
        source_sha=source_sha,
        implementation_digest=digest,
        pinned_closure_count=len(PINNED_IMPLEMENTATION_CLOSURE),
        strategy_name=str(strategy.get("name", "UNAVAILABLE")),
        strategy_version=str(strategy.get("version", "UNAVAILABLE")),
        watchlist=DEFAULT_WATCHLIST,
        entry_start=str(signal.get("entry_start", "UNAVAILABLE")),
        entry_end=str(signal.get("entry_end", "UNAVAILABLE")),
        market_session=classification.session.value,
        market_reason=classification.reason.value,
        alpaca_key_present=key_present,
        alpaca_secret_present=secret_present,
        local_config_present=local_present,
        account_fingerprint_resolvable=account_resolvable,
        suspension_db_status=path_status,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def render_readiness(value: MorningReadiness) -> str:
    lines = [
        "ORB-LIVE MORNING READINESS",
        "",
        f"Source SHA: {value.source_sha}",
        f"Implementation digest: {value.implementation_digest}",
        f"Pinned closure: {value.pinned_closure_count} files",
        f"Strategy: {value.strategy_name} v{value.strategy_version}",
        f"Universe: {len(value.watchlist)} symbols ({', '.join(value.watchlist)})",
        f"Entry window: {value.entry_start}–{value.entry_end} ET",
        f"Market/session: {value.market_session} ({value.market_reason})",
        f"Alpaca key ID: {'PRESENT' if value.alpaca_key_present else 'MISSING'}",
        f"Alpaca secret: {'PRESENT' if value.alpaca_secret_present else 'MISSING'}",
        f"Local config: {'PRESENT' if value.local_config_present else 'NOT PRESENT'}",
        f"Account fingerprint: {'RESOLVABLE' if value.account_fingerprint_resolvable else 'NOT RESOLVABLE'}",
        f"Suspension DB: {value.suspension_db_status}",
        "",
        f"Authority: {AUTHORITY_CEILING}",
        "Activation approved: FALSE",
        "Automated action: DISABLED",
        "Live ready: FALSE",
        "Unattended ready: FALSE",
        "Broker writes: DISABLED",
        "",
    ]
    if value.ready:
        lines.append("READY_FOR_MORNING_WATCH")
    else:
        lines.append("BLOCKED_BY_LOCAL_CONFIGURATION")
        lines.append("Blocking checks: " + ", ".join(value.blockers))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suspension-db", type=Path, help="optional absolute SQLite path outside the repository")
    args = parser.parse_args(argv)
    readiness = assess_morning_readiness(suspension_db=args.suspension_db)
    print(render_readiness(readiness), end="")
    return 0 if readiness.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
