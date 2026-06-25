"""Broker interface + order model + factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from energy_trader.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class Order:
    """A single intended equity order.

    Exactly one of ``quantity`` / ``notional`` should be set. ``notional``
    (dollar amount) is preferred for fractional ETF orders like USO/XLE.
    """

    symbol: str
    side: str  # "buy" | "sell"
    quantity: float | None = None
    notional: float | None = None
    order_type: str = "market"
    time_in_force: str = "gtc"
    extended_hours: bool = True
    reason: str = ""

    def describe(self) -> str:
        size = (
            f"${self.notional:.2f}" if self.notional is not None
            else f"{self.quantity} sh"
        )
        return f"{self.side.upper()} {size} {self.symbol} ({self.order_type})"


@dataclass
class OrderResult:
    order: Order
    status: str  # "submitted" | "dry_run" | "rejected" | "error"
    broker: str
    detail: dict[str, Any] = field(default_factory=dict)


class Broker(ABC):
    """Execution backend contract."""

    name: str = "base"

    def review(self, order: Order) -> dict[str, Any]:
        """Optional pre-trade check; backends may override. Default: no-op."""
        return {"status": "skipped"}

    def equity(self) -> float | None:
        """Account equity (total value) for percent-of-equity position sizing.

        Returns ``None`` when there's no real account or it can't be read (e.g.
        dry-run), in which case the strategy falls back to a fixed notional.
        """
        return None

    @abstractmethod
    def place(self, order: Order) -> OrderResult:
        """Execute (or simulate) the order."""


def get_broker(dry_run: bool, settings: Settings) -> Broker:
    """Return the broker for this run.

    ``dry_run=True`` always returns the dry-run broker. Otherwise the execution
    target is ``settings.broker``: ``"alpaca_paper"`` (Phase 2, no real money) or
    ``"robinhood"`` (Phase 3, real money).
    """
    target = "dry_run" if dry_run else settings.broker

    if target == "dry_run":
        from energy_trader.brokers.dry_run import DryRunBroker

        return DryRunBroker()
    if target in ("alpaca_paper", "alpaca"):
        from energy_trader.brokers.alpaca_paper import AlpacaPaperBroker

        return AlpacaPaperBroker(settings)
    if target in ("robinhood", "robinhood_mcp"):
        from energy_trader.brokers.robinhood_mcp import RobinhoodMCPBroker

        return RobinhoodMCPBroker(settings)

    raise ValueError(f"Unknown broker target: {target!r}")
