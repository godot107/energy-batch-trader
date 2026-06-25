"""The orchestrator-agnostic pipeline: extract → anomaly gate → analyze → execute.

This single function is the integration point for every runtime (CLI, Airflow,
Azure Functions). It takes no orchestrator types and returns a plain result, so
nothing about *how* it's scheduled leaks into *what* it does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from energy_trader.anomaly import detect_anomalies
from energy_trader.brokers.base import Broker, Order, OrderResult, get_broker
from energy_trader.config import Settings, get_settings
from energy_trader.data import extract_market_data
from energy_trader.notify import send_telegram_alert
from energy_trader.strategy import analyze, current_signals

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    dry_run: bool
    halted: bool = False
    halt_reason: str = ""
    orders: list[Order] = field(default_factory=list)
    results: list[OrderResult] = field(default_factory=list)

    def summary(self) -> str:
        if self.halted:
            return f"HALTED — {self.halt_reason}"
        if not self.orders:
            return "No actionable signals (all hold)."
        lines = [
            f"{r.order.describe()} → {r.status} [{r.broker}]" for r in self.results
        ]
        return "\n".join(lines)


def run_pipeline(
    dry_run: bool = True, settings: Settings | None = None
) -> PipelineResult:
    """Run one EOD cycle. Defaults to ``dry_run=True`` — live trading is opt-in."""
    settings = settings or get_settings()
    mode = "DRY-RUN" if dry_run else settings.broker.upper()
    logger.info("Starting EOD pipeline (%s) for %s", mode, ", ".join(settings.assets))

    today = datetime.now().strftime("%Y-%m-%d")

    # 1) Risk gate first — cheap to halt, expensive to trade into a shock.
    anomaly = detect_anomalies(settings)
    if anomaly.detected:
        logger.warning("Anomaly detected — halting trading. %s", anomaly.reason)
        send_telegram_alert(
            f"📊 EOD run ({mode}) — {today}\n⚠️ HALTED — {anomaly.reason}"
        )
        return PipelineResult(dry_run=dry_run, halted=True, halt_reason=anomaly.reason)

    # 2) Extract market data.
    data = extract_market_data(settings)

    # 3) Size relative to account equity when the broker can report it (percent-of-
    #    equity, Kaufman ch.23); fall back to a fixed notional otherwise. The broker
    #    is created once here and reused for execution; if it can't be built (e.g.
    #    unconfigured on a hold day) we degrade to fixed sizing and only surface the
    #    error if there's actually an order to place.
    signals = current_signals(data, settings)
    broker: Broker | None = None
    equity: float | None = None
    try:
        broker = get_broker(dry_run=dry_run, settings=settings)
        equity = broker.equity()
    except Exception as exc:  # noqa: BLE001 - degrade; re-raised at execution if needed
        logger.warning("Broker unavailable for equity sizing (%s); fixed notional.", exc)

    orders = analyze(data, settings, signals, account_equity=equity)

    # 4) Execute via the selected broker (only when there are orders).
    results: list[OrderResult] = []
    if orders:
        if broker is None:
            broker = get_broker(dry_run=dry_run, settings=settings)  # surface config error
        results = [broker.place(order) for order in orders]
    else:
        logger.info("No crossover signals today; nothing to execute.")

    # 5) Always notify — a daily ping every run, hold or trade.
    send_telegram_alert(_daily_summary(mode, today, signals, results))

    return PipelineResult(dry_run=dry_run, orders=orders, results=results)


def _daily_summary(
    mode: str, today: str, signals: dict[str, str], results: list[OrderResult]
) -> str:
    """One Telegram message summarizing the run — sent even on all-hold days."""
    sig_line = " · ".join(f"{s} {a.upper()}" for s, a in signals.items()) or "(no data)"
    lines = [f"📊 EOD run ({mode}) — {today}", f"Signals: {sig_line}"]
    if results:
        lines += [
            f"{'🟢' if r.order.side == 'buy' else '🔴'} {r.order.describe()} "
            f"→ {r.status}"
            for r in results
        ]
    else:
        lines.append("No orders today (all hold).")
    return "\n".join(lines)
