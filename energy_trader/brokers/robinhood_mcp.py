"""Robinhood official Agentic Trading MCP broker (Phase 2).

This places equity orders through Robinhood's *sanctioned* MCP server at
``https://agent.robinhood.com/mcp/trading`` — not the unofficial ``robin_stocks``
private API. Trades land only in the isolated, separately-funded **Agentic
account**; every other account stays read-only, which contains the blast radius.

Design note — *deterministic, no LLM in the trade loop*:
    MCP is JSON-RPC tool calls, so we drive ``review_equity_order`` then
    ``place_equity_order`` directly with parameters our strategy computed. The
    LLM only ever does anomaly research (see :mod:`energy_trader.anomaly`); it
    never decides or places a trade here.

Two things must be confirmed against the live server before going live (both are
flagged with ``TODO(live)`` below):
  1. OAuth — Robinhood's connector flow approves access in the Robinhood app and
     issues a token; this client expects that token in ``ROBINHOOD_MCP_TOKEN``.
     Token refresh/rotation belongs in a secret store (Azure Key Vault) for
     Phase 3, not on disk.
  2. The exact argument schema for ``place_equity_order`` — call ``tools/list``
     against the live server and adjust ``_order_args`` to match.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from energy_trader.brokers.base import Broker, Order, OrderResult
from energy_trader.config import Settings

logger = logging.getLogger(__name__)


class RobinhoodMCPNotConfigured(RuntimeError):
    """Raised when live execution is requested without an MCP token."""


class RobinhoodMCPBroker(Broker):
    name = "robinhood_mcp"

    REVIEW_TOOL = "review_equity_order"
    PLACE_TOOL = "place_equity_order"

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.rh_mcp_token:
            raise RobinhoodMCPNotConfigured(
                "ROBINHOOD_MCP_TOKEN is not set. Connect the Robinhood Agentic "
                "Trading MCP via your agent's connector flow, then export the "
                "issued token. Until then, run with --dry-run."
            )

    # --- argument mapping --------------------------------------------------
    def _order_args(self, order: Order) -> dict[str, Any]:
        """Map our :class:`Order` to ``place_equity_order`` arguments.

        TODO(live): confirm key names against the live MCP ``tools/list`` schema.
        """
        args: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "time_in_force": order.time_in_force,
            "extended_hours": order.extended_hours,
        }
        if order.notional is not None:
            args["amount"] = order.notional  # dollar-based (fractional)
        if order.quantity is not None:
            args["quantity"] = order.quantity
        return args

    # --- async core --------------------------------------------------------
    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # Imported lazily so dry-run runs need not install the MCP SDK.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {self.settings.rh_mcp_token}"}
        async with streamablehttp_client(self.settings.rh_mcp_url, headers=headers) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments=args)
                return {
                    "is_error": result.isError,
                    "content": [getattr(c, "text", str(c)) for c in result.content],
                }

    # --- Broker API --------------------------------------------------------
    def review(self, order: Order) -> dict[str, Any]:
        return asyncio.run(self._call(self.REVIEW_TOOL, self._order_args(order)))

    def place(self, order: Order) -> OrderResult:
        try:
            review = self.review(order)
            if review.get("is_error"):
                logger.error("Order review rejected: %s", review.get("content"))
                return OrderResult(order, "rejected", self.name, detail=review)

            result = asyncio.run(self._call(self.PLACE_TOOL, self._order_args(order)))
            status = "error" if result.get("is_error") else "submitted"
            logger.info("[LIVE] %s — %s", order.describe(), status)
            return OrderResult(order, status, self.name, detail=result)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the batch
            logger.exception("MCP order failed for %s", order.symbol)
            return OrderResult(order, "error", self.name, detail={"error": str(exc)})
