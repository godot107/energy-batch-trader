"""Dry-run broker: logs intended orders, places nothing. Phase 1 default.

When Alpaca keys are configured it *reads* the paper account's equity and
positions (read-only), so a dry-run previews exactly what tonight's paper run
would trade. It never places anything.
"""

from __future__ import annotations

import logging

from energy_trader.brokers.base import Broker, Order, OrderResult, Position
from energy_trader.config import Settings

logger = logging.getLogger(__name__)


class DryRunBroker(Broker):
    name = "dry_run"

    def __init__(self, settings: Settings | None = None):
        self._source: Broker | None = None
        if settings and settings.alpaca_api_key and settings.alpaca_secret_key:
            from energy_trader.brokers.alpaca_paper import AlpacaPaperBroker

            self._source = AlpacaPaperBroker(settings)

    def equity(self) -> float | None:
        return self._source.equity() if self._source else None

    def positions(self) -> dict[str, Position] | None:
        return self._source.positions() if self._source else None

    def place(self, order: Order) -> OrderResult:
        logger.info("[DRY-RUN] would place: %s — %s", order.describe(), order.reason)
        return OrderResult(
            order=order,
            status="dry_run",
            broker=self.name,
            detail={"note": "no order sent; dry-run mode"},
        )
