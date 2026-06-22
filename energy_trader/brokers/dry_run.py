"""Dry-run broker: logs intended orders, places nothing. Phase 1 default."""

from __future__ import annotations

import logging

from energy_trader.brokers.base import Broker, Order, OrderResult

logger = logging.getLogger(__name__)


class DryRunBroker(Broker):
    name = "dry_run"

    def place(self, order: Order) -> OrderResult:
        logger.info("[DRY-RUN] would place: %s — %s", order.describe(), order.reason)
        return OrderResult(
            order=order,
            status="dry_run",
            broker=self.name,
            detail={"note": "no order sent; dry-run mode"},
        )
