from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import parse_qs, urlparse


MCP_URL = "https://agent.robinhood.com/mcp/trading"
CALLBACK_PORT = 8765

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = PROJECT_ROOT / "data" / ".rh_tokens.json"


class RobinhoodClientError(RuntimeError):
    """Raised when Robinhood returns an unusable response."""


@dataclass(frozen=True)
class EquityQuote:
    symbol: str
    bid_price: Decimal
    ask_price: Decimal
    last_trade_price: Decimal | None = None


class _FileTokenStorage:
    """
    Stores OAuth tokens locally.

    The token file is a credential. Never upload, email, commit, or share it.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}

        return json.loads(
            self.path.read_text(encoding="utf-8")
        )

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )

        try:
            self.path.chmod(0o600)
        except OSError:
            # Windows does not use Unix chmod permissions in the same way.
            pass

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        self._write(data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")

        return (
            OAuthClientInformationFull.model_validate(raw)
            if raw
            else None
        )

    async def set_client_info(self, client_info) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)


class _CallbackServer:
    """Receives the OAuth redirect from Robinhood."""

    def __init__(self, port: int):
        self.port = port
        self.code: str | None = None
        self.state: str | None = None
        self._done = threading.Event()

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                query = parse_qs(
                    urlparse(self.path).query
                )

                outer.code = (
                    query.get("code") or [None]
                )[0]

                outer.state = (
                    query.get("state") or [None]
                )[0]

                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/html",
                )
                self.end_headers()

                self.wfile.write(
                    b"<h3>Authenticated. You may close this tab.</h3>"
                )

                outer._done.set()

            def log_message(self, *_):
                pass

        self._server = HTTPServer(
            ("127.0.0.1", self.port),
            Handler,
        )

    def start(self) -> None:
        thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        thread.start()

    def wait(
        self,
        timeout: float = 300,
    ) -> tuple[str, str | None]:
        if not self._done.wait(timeout):
            raise TimeoutError(
                "No OAuth callback was received."
            )

        self._server.shutdown()

        if not self.code:
            raise RobinhoodClientError(
                "OAuth callback did not include an authorization code."
            )

        return self.code, self.state


def _tool_error_text(result) -> str:
    return " ".join(
        getattr(item, "text", "")
        for item in result.content
    ) or "Unknown Robinhood MCP error."


def _unwrap_tool_result(
    result,
    tool_name: str,
) -> dict:
    if result.isError:
        raise RobinhoodClientError(
            f"{tool_name} failed: {_tool_error_text(result)}"
        )

    if not result.structuredContent:
        raise RobinhoodClientError(
            f"{tool_name} returned no structured content."
        )

    data = result.structuredContent.get("data")

    if not isinstance(data, dict):
        raise RobinhoodClientError(
            f"{tool_name} returned an unexpected response."
        )

    return data


class RobinhoodClient:
    """
    Read-only Robinhood MCP client.

    No trading methods are included in this version.
    """

    @asynccontextmanager
    async def session(self) -> AsyncIterator:
        from mcp import ClientSession
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import (
            streamablehttp_client,
        )
        from mcp.shared.auth import OAuthClientMetadata

        callback_server = _CallbackServer(
            CALLBACK_PORT
        )
        callback_server.start()

        async def redirect_handler(url: str) -> None:
            print(
                "\nOpening Robinhood authorization in your browser."
            )
            print(
                "If the browser does not open, copy this URL:"
            )
            print(url)

            webbrowser.open(url)

        async def callback_handler():
            return await asyncio.to_thread(
                callback_server.wait
            )

        oauth = OAuthClientProvider(
            server_url=MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name="ORB Live Read-Only Client",
                redirect_uris=[
                    f"http://127.0.0.1:"
                    f"{CALLBACK_PORT}/callback"
                ],
                grant_types=[
                    "authorization_code",
                    "refresh_token",
                ],
                response_types=["code"],
            ),
            storage=_FileTokenStorage(TOKEN_PATH),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

        try:
            async with streamablehttp_client(
                MCP_URL,
                auth=oauth,
                terminate_on_close=False,
            ) as (read, write, _):

                async with ClientSession(
                    read,
                    write,
                ) as session:

                    await session.initialize()
                    yield session

        finally:
            callback_server._server.shutdown()
            callback_server._server.server_close()

    async def get_equity_quote(
        self,
        symbol: str,
    ) -> EquityQuote:
        normalized_symbol = symbol.upper().strip()

        if not normalized_symbol:
            raise ValueError(
                "A stock symbol is required."
            )

        async with self.session() as session:
            result = await session.call_tool(
                "get_equity_quotes",
                {
                    "symbols": [normalized_symbol],
                },
            )

        data = _unwrap_tool_result(
            result,
            "get_equity_quotes",
        )

        results = data.get("results") or []

        matching_result = next(
            (
                item
                for item in results
                if item
                and item.get("quote")
                and item["quote"].get("symbol")
                == normalized_symbol
            ),
            None,
        )

        if not matching_result:
            raise RobinhoodClientError(
                f"No quote was returned for "
                f"{normalized_symbol}."
            )

        quote = matching_result.get("quote")

        if not quote:
            raise RobinhoodClientError(
                f"Quote data was missing for "
                f"{normalized_symbol}."
            )

        bid = Decimal(
            quote["bid_price"]
        )
        ask = Decimal(
            quote["ask_price"]
        )

        last_trade_raw = quote.get(
            "last_trade_price"
        )

        last_trade = (
            Decimal(last_trade_raw)
            if last_trade_raw is not None
            else None
        )

        if bid <= 0 or ask <= 0:
            raise RobinhoodClientError(
                "Robinhood returned a non-positive bid or ask."
            )

        if bid > ask:
            raise RobinhoodClientError(
                "Robinhood returned a bid above the ask."
            )

        return EquityQuote(
            symbol=normalized_symbol,
            bid_price=bid,
            ask_price=ask,
            last_trade_price=last_trade,
        )


async def main() -> None:
    client = RobinhoodClient()

    quote = await client.get_equity_quote(
        "AAPL"
    )

    print("\nRead-only Robinhood connection succeeded.")
    print(f"Symbol: {quote.symbol}")
    print(f"Bid: ${quote.bid_price}")
    print(f"Ask: ${quote.ask_price}")
    print(
        f"Last trade: "
        f"{quote.last_trade_price or 'not provided'}"
    )
    print("\nNo order tool was called.")


if __name__ == "__main__":
    asyncio.run(main())