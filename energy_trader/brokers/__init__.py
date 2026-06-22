"""Pluggable execution backends.

``DryRunBroker`` logs intended orders (Phase 1). ``RobinhoodMCPBroker`` places
real equity orders through Robinhood's official Agentic Trading MCP (Phase 2).
Select one with :func:`energy_trader.brokers.base.get_broker`.
"""

from energy_trader.brokers.base import Broker, Order, get_broker

__all__ = ["Broker", "Order", "get_broker"]
