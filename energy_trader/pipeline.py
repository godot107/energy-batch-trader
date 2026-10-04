"""The orchestrator-agnostic pipeline: extract → anomaly gate → analyze → execute.

This single function is the integration point for every runtime (CLI, Airflow,
Azure Functions). It takes no orchestrator types and returns a plain result, so
nothing about *how* it's scheduled leaks into *what* it does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from energy_trader.anomaly import detect_anomalies
from energy_trader.brokers.base import Broker, Order, OrderResult, Position, get_broker
from energy_trader.config import Settings, get_settings
from energy_trader.data import extract_market_data
from energy_trader.allocation import plan_allocation
from energy_trader.notify import send_telegram_alert
from energy_trader.rebalance import Allocation, plan_rebalance
from energy_trader.strategy import analyze, current_signals

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    dry_run: bool
    halted: bool = False
    halt_reason: str = ""
    orders: list[Order] = field(default_factory=list)
    results: list[OrderResult] = field(default_factory=list)
    plan: list[Allocation] = field(default_factory=list)  # empty ⇒ crossover mode
    headline: str = ""

    def summary(self) -> str:
        if self.halted:
            return f"HALTED — {self.halt_reason}"
        lines = [self.headline] if self.headline else []
        lines += [a.describe() for a in self.plan]
        if not self.orders:
            return "\n".join(lines + ["No orders today."])
        lines += [
            f"{r.order.describe()} → {r.status} [{r.broker}]" for r in self.results
        ]
        return "\n".join(lines)


def run_pipeline(
    dry_run: bool = True, settings: Settings | None = None
) -> PipelineResult:
    """Run one EOD cycle. Defaults to ``dry_run=True`` — live trading is opt-in."""
    settings = settings or get_settings()
    mode = "DRY-RUN" if dry_run else settings.broker.upper()
    mode += f" · {settings.strategy}"
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

    # 2) Extract market data (the trend strategy only; allocation trades off the
    #    broker's own market values, so it needs no bars).
    trend = settings.strategy == "trend"
    data = extract_market_data(settings) if trend else {}

    # 3) Size relative to account equity when the broker can report it (percent-of-
    #    equity, Kaufman ch.23); fall back to a fixed notional otherwise. The broker
    #    is created once here and reused for execution; if it can't be built (e.g.
    #    unconfigured on a hold day) we degrade to fixed sizing and only surface the
    #    error if there's actually an order to place.
    signals = current_signals(data, settings) if trend else {}
    broker: Broker | None = None
    equity: float | None = None
    positions: dict[str, Position] | None = None
    try:
        broker = get_broker(dry_run=dry_run, settings=settings)
        equity = broker.equity()
        positions = broker.positions()
    except Exception as exc:  # noqa: BLE001 - degrade; re-raised at execution if needed
        logger.warning("Broker unavailable for equity sizing (%s); fixed notional.", exc)

    # Position-aware broker ⇒ trade toward today's target holdings. Otherwise
    # (no Alpaca keys / live MCP until its positions schema is verified) the trend
    # strategy falls back to crossover-day orders; allocation can't act blind.
    plan: list[Allocation] = []
    headline = ""
    orders: list[Order] = []
    if not trend:
        if positions is None or not equity:
            headline = "Allocation needs a broker that reports equity + positions; no trades."
            logger.warning(headline)
        else:
            plan, headline = plan_allocation(settings, equity, positions, date.today())
    elif positions is not None:
        plan = plan_rebalance(data, settings, equity, positions)
    else:
        orders = analyze(data, settings, signals, account_equity=equity)
    orders = orders or [a.order for a in plan if a.order is not None]

    # 4) Execute via the selected broker (only when there are orders).
    results: list[OrderResult] = []
    if orders:
        if broker is None:
            broker = get_broker(dry_run=dry_run, settings=settings)  # surface config error
        results = [broker.place(order) for order in orders]
    else:
        logger.info("Nothing to trade today (no crossovers / holdings within band).")

    # 5) Always notify — a daily ping every run, hold or trade.
    send_telegram_alert(
        _daily_summary(mode, today, signals, results, plan, equity, headline)
    )

    return PipelineResult(dry_run=dry_run, orders=orders, results=results, plan=plan,
                          headline=headline)


def _daily_summary(
    mode: str,
    today: str,
    signals: dict[str, str],
    results: list[OrderResult],
    plan: list[Allocation] | None = None,
    equity: float | None = None,
    headline: str = "",
) -> str:
    """One Telegram message summarizing the run — sent even on all-hold days."""
    lines = [f"📊 EOD run ({mode}) — {today}"]
    if signals:
        lines.append("Signals: " + " · ".join(f"{s} {a.upper()}" for s, a in signals.items()))
    if headline:
        lines.append(headline)
    if plan:
        if equity and not headline:
            invested = sum(a.held for a in plan)
            lines.append(f"Equity ${equity:,.2f} · invested {invested / equity:.0%}")
        lines += [f"• {a.describe()}" for a in plan]
    if results:
        lines += [
            f"{'🟢' if r.order.side == 'buy' else '🔴'} {r.order.describe()} "
            f"→ {r.status}"
            for r in results
        ]
    else:
        lines.append("No orders today (holdings on target)." if plan
                     else "No orders today (all hold).")
    return "\n".join(lines)
