"""Alpaca paper-trading broker (Phase 2).

Places equity orders against Alpaca's **paper** account (no real money), so the
strategy can be validated against real fills and account state before any live
broker. Uses ``alpaca-py``'s ``TradingClient(paper=True)``, imported lazily so a
dry-run never needs the trading deps (mirrors :class:`RobinhoodMCPBroker`).

Notional (dollar) orders on Alpaca must be market / time-in-force=day / regular
hours — which is exactly what the strategy emits for fractional ETF orders.
"""

from __future__ import annotations

import logging
from typing import Any

from energy_trader.brokers.base import Broker, Order, OrderResult
from energy_trader.config import Settings

logger = logging.getLogger(__name__)


class AlpacaNotConfigured(RuntimeError):
    """Raised when paper execution is requested without Alpaca keys."""


class AlpacaPaperBroker(Broker):
    name = "alpaca_paper"

    def __init__(self, settings: Settings):
        self.settings = settings
        if not (settings.alpaca_api_key and settings.alpaca_secret_key):
            raise AlpacaNotConfigured(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Add your Alpaca "
                "paper-trading keys to .env before paper trading."
            )
        self._client = None  # built lazily on first use

    @property
    def client(self):
        if self._client is None:
            from alpaca.trading.client import TradingClient

            self._client = TradingClient(
                self.settings.alpaca_api_key,
                self.settings.alpaca_secret_key,
                paper=True,
            )
        return self._client

    def _request(self, order: Order):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        kwargs: dict[str, Any] = {
            "symbol": order.symbol,
            "side": OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
            "time_in_force": TimeInForce.DAY,  # required for notional orders
        }
        if order.notional is not None:
            kwargs["notional"] = round(order.notional, 2)
        elif order.quantity is not None:
            kwargs["qty"] = order.quantity
        return MarketOrderRequest(**kwargs)

    def review(self, order: Order) -> dict[str, Any]:
        """Read-only pre-trade check: confirm the paper account is reachable/active."""
        try:
            acct = self.client.get_account()
            return {
                "account_status": getattr(acct.status, "value", str(acct.status)),
                "buying_power": str(acct.buying_power),
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def equity(self) -> float | None:
        """Paper-account equity (total value) for percent-of-equity sizing."""
        try:
            return float(self.client.get_account().equity)
        except Exception as exc:  # noqa: BLE001 - degrade to fixed notional
            logger.warning("Could not fetch Alpaca account equity (%s).", exc)
            return None

    def place(self, order: Order) -> OrderResult:
        try:
            o = self.client.submit_order(order_data=self._request(order))
            status = getattr(o.status, "value", str(o.status))
            logger.info("[PAPER] %s — %s (id %s)", order.describe(), status, o.id)
            return OrderResult(
                order,
                status,
                self.name,
                detail={"id": str(o.id), "status": status, "symbol": o.symbol},
            )
        except Exception as exc:  # noqa: BLE001 - surface, never crash the batch
            logger.exception("Alpaca paper order failed for %s", order.symbol)
            return OrderResult(order, "error", self.name, detail={"error": str(exc)})
