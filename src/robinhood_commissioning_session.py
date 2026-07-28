"""Reviewed one-session binding for attended one-share commissioning.

Nothing in this module opens a session at import time. The default live
factories are reached only through the launcher's fully authorized real-session
path; tests inject offline context managers and resolvers.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from src.broker_identity import CommissionedAccount, load_local_override
from src.broker_snapshot import account_fingerprint
from src.config import load_config
from src.one_share_commissioning import CommissioningError
from src.one_share_commissioning_orchestrator import (
    OneShareCommissioningOrchestrator,
    OrchestrationResult,
)
from src.robinhood_client import (
    CALLBACK_PORT,
    MCP_URL,
    TOKEN_PATH,
    _FileTokenStorage,
)
from src.robinhood_commissioning_adapter import (
    RobinhoodCommissioningAdapter,
    assert_tool_allowed,
)


class CommissioningSessionBindingError(CommissioningError):
    """The real-session dependency boundary could not fail closed."""


class BrowserAuthenticationRequestError(CommissioningSessionBindingError):
    """The silent session attempted to request interactive authentication."""


@dataclass(frozen=True, repr=False)
class CommissionedAccountResolution:
    """Transient identity result; neither value may enter logs or evidence."""

    account_number: str
    initial_accounts_result: Any

    def __repr__(self) -> str:
        return "CommissionedAccountResolution(<redacted>)"


def _response_data(result: Any) -> Mapping[str, Any]:
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise CommissioningSessionBindingError(
            "Commissioned account resolution failed closed."
        )
    content = getattr(result, "structuredContent", None)
    value = content if content is not None else getattr(result, "data", result)
    if isinstance(value, Mapping) and set(value) in ({"data"}, {"data", "guide"}):
        value = value.get("data")
    if not isinstance(value, Mapping):
        raise CommissioningSessionBindingError(
            "Commissioned account resolution returned an invalid shape."
        )
    return value


@dataclass
class CommissionedAccountResolver:
    """Resolve exactly one configured account with one cached broker read."""

    config_loader: Callable[[], Mapping[str, Any]] = load_config
    local_config_loader: Callable[[], Mapping[str, Any]] = load_local_override

    async def __call__(self, session: Any) -> CommissionedAccountResolution:
        try:
            commissioned = CommissionedAccount.from_config(
                self.config_loader(),
                local_config=self.local_config_loader(),
            )
            result = await session.call_tool("get_accounts", {})
            rows = _response_data(result).get("accounts")
            if not isinstance(rows, list) or any(
                not isinstance(row, Mapping) for row in rows
            ):
                raise CommissioningSessionBindingError(
                    "Commissioned account resolution returned an invalid shape."
                )
            eligible = [
                dict(row)
                for row in rows
                if row.get("agentic_allowed") is True
                and row.get("state") == "active"
                and row.get("deactivated") is not True
                and row.get("permanently_deactivated") is not True
            ]
            if len(eligible) != 1:
                raise CommissioningSessionBindingError(
                    "Exactly one commissioned account must match."
                )
            number = eligible[0].get("account_number")
            if not isinstance(number, str) or not number.strip():
                raise CommissioningSessionBindingError(
                    "Commissioned account identity is unavailable."
                )
            commissioned.verify(account_fingerprint(number))
            return CommissionedAccountResolution(number, result)
        except CommissioningSessionBindingError:
            raise
        except Exception:
            raise CommissioningSessionBindingError(
                "Commissioned account identity is unavailable or ambiguous."
            ) from None


@dataclass
class _ReplayInitialAccountsSession:
    """Replay the resolver's one account read, then delegate allowed calls."""

    _session: Any
    _initial_result: Any
    _replayed: bool = False

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        allowed = assert_tool_allowed(tool_name)
        if allowed == "get_accounts":
            if self._replayed or dict(arguments) != {}:
                raise CommissioningSessionBindingError(
                    "The commissioned account read may be replayed exactly once."
                )
            self._replayed = True
            result = self._initial_result
            self._initial_result = None
            return result
        return await self._session.call_tool(allowed, dict(arguments))


class SilentRobinhoodCommissioningClient:
    """One-purpose MCP client that never permits interactive authentication."""

    @asynccontextmanager
    async def session(self):
        from mcp import ClientSession
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.shared.auth import OAuthClientMetadata

        async def reject_redirect(_url: str) -> None:
            raise BrowserAuthenticationRequestError(
                "Silent Robinhood authentication is required."
            )

        async def reject_callback():
            raise BrowserAuthenticationRequestError(
                "Silent Robinhood authentication is required."
            )

        oauth = OAuthClientProvider(
            server_url=MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name="ORB Live One-Share Commissioning",
                redirect_uris=[f"http://127.0.0.1:{CALLBACK_PORT}/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=_FileTokenStorage(TOKEN_PATH),
            redirect_handler=reject_redirect,
            callback_handler=reject_callback,
        )
        try:
            async with streamablehttp_client(
                MCP_URL,
                auth=oauth,
                terminate_on_close=False,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except BrowserAuthenticationRequestError:
            raise


@asynccontextmanager
async def silent_robinhood_session_factory():
    """Open exactly one MCP session without permitting browser authentication."""

    client = SilentRobinhoodCommissioningClient()
    try:
        async with client.session() as session:
            yield session
    except BrowserAuthenticationRequestError:
        raise CommissioningSessionBindingError(
            "Silent Robinhood authentication is required."
        ) from None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass
class RobinhoodCommissioningSessionBinding:
    """Own exactly one session, adapter, orchestrator, and commissioning run."""

    run_id: str
    session_factory: Callable[[], Any]
    commissioned_account_resolver: Callable[[Any], Any]
    monotonic: Callable[[], float]
    sleep: Callable[[float], Any]
    now_eastern: Callable[[], Any]
    authorization_provider: Callable[[str], Any]
    evidence_store: Any
    emergency_handoff: Callable[[Any], Any]
    adapter_factory: Callable[..., Any] = RobinhoodCommissioningAdapter
    orchestrator_factory: Callable[..., Any] = OneShareCommissioningOrchestrator
    _used: bool = field(default=False, init=False)

    async def run(self) -> OrchestrationResult:
        if self._used:
            raise CommissioningSessionBindingError(
                "The real-session binding is single-use."
            )
        self._used = True
        try:
            async with self.session_factory() as session:
                resolution = await _maybe_await(
                    self.commissioned_account_resolver(session)
                )
                if not isinstance(resolution, CommissionedAccountResolution):
                    raise CommissioningSessionBindingError(
                        "Commissioned account resolution failed closed."
                    )
                replay_session = _ReplayInitialAccountsSession(
                    session, resolution.initial_accounts_result
                )
                adapter = self.adapter_factory(
                    replay_session, resolution.account_number
                )
                orchestrator = self.orchestrator_factory(
                    run_id=self.run_id,
                    adapter=adapter,
                    monotonic=self.monotonic,
                    sleep=self.sleep,
                    now_eastern=self.now_eastern,
                    authorization_provider=self.authorization_provider,
                    evidence_store=self.evidence_store,
                    emergency_handoff=self.emergency_handoff,
                    strategy_enabled=False,
                    regular_market_hours=True,
                )
                return await orchestrator.run()
        except BrowserAuthenticationRequestError:
            raise CommissioningSessionBindingError(
                "Silent Robinhood authentication is required."
            ) from None
        except CommissioningSessionBindingError:
            raise
