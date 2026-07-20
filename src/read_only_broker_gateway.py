from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping


READ_ONLY_TOOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_historicals",
    }
)

FORBIDDEN_TOOL_MARKERS: tuple[str, ...] = (
    "place",
    "submit",
    "cancel",
    "replace",
    "modify",
    "review",
)


class ReadOnlyToolViolation(RuntimeError):
    """Raised when a non-read-only broker tool is requested."""


def assert_tool_allowed(tool_name: Any) -> str:
    """
    Validates a broker tool name against the read-only allowlist.

    Raises before any session is opened or invoked. This is the single
    chokepoint for every broker read in the live reconciliation path.
    """

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ReadOnlyToolViolation(
            "A broker tool name is required."
        )

    normalized = tool_name.strip()

    if normalized not in READ_ONLY_TOOL_ALLOWLIST:
        raise ReadOnlyToolViolation(
            f"Tool {normalized!r} is not on the read-only allowlist. "
            "Failing closed rather than invoking an unapproved "
            "broker tool."
        )

    return normalized


class ReadOnlySession:
    """
    Wraps an MCP session so only allowlisted read tools can be called.

    Every call is validated before delegation. The wrapper exposes no
    method that accepts an order payload and never constructs order
    arguments.
    """

    def __init__(self, session: Any):
        if session is None:
            raise ReadOnlyToolViolation(
                "A broker session is required."
            )
        self._session = session

    async def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ):
        allowed = assert_tool_allowed(tool_name)

        return await self._session.call_tool(
            allowed,
            dict(arguments or {}),
        )


class ReadOnlyBrokerGateway:
    """
    Read-only broker surface for live reconciliation.

    No review, place, cancel, replace, or modify capability is
    reachable through this gateway. Unknown tools are rejected before a
    session is opened.
    """

    allowlist = READ_ONLY_TOOL_ALLOWLIST

    def __init__(self, client: Any):
        if client is None:
            raise ReadOnlyToolViolation(
                "A broker client is required."
            )
        self._client = client

    @staticmethod
    def is_allowed(tool_name: Any) -> bool:
        try:
            assert_tool_allowed(tool_name)
        except ReadOnlyToolViolation:
            return False
        return True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ReadOnlySession]:
        async with self._client.session() as session:
            yield ReadOnlySession(session)

    async def read(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ):
        """
        Performs one allowlisted read.

        The tool name is validated before the session is opened, so a
        rejected tool never reaches the transport.
        """

        allowed = assert_tool_allowed(tool_name)
        payload = dict(arguments or {})

        async with self.session() as session:
            return await session.call_tool(allowed, payload)
