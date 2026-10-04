"""Daily target-position rebalancing — the live mirror of the backtest.

The backtest holds ``in_trend × vol_weight`` *every* bar; the old live path only
traded on crossover days, sized sells by a fixed $ instead of what was held, and
let repeated buys stack. Here the pipeline instead computes a **target $
position** per symbol each day and trades only the difference to what the broker
actually holds:

    target = in_trend × risk_fraction × equity × vol_weight      (0 when out)
    trade  = target − held      if |target − held| > band × target (and ≥ min $)

- **In trend** = fast SMA above slow SMA — the *state* the crossover events
  toggle (identical to the backtest's forward-filled position), so a missed run
  or a failed order self-heals the next day instead of leaving a stray lot.
- **Exit** (target 0) sells the full held quantity, never a guessed $ amount.
- **No-trade band** (``rebalance_band``, relative to target): vol drift is
  continuous, so without a band the account would churn tiny orders daily.
  Kaufman (ch.23) sizes to equal risk; the band is the standard practical
  compromise between tracking that target and paying turnover.
- Synthetic (mock) bars never trade — a data outage must not move real holdings.

Requires a broker that reports positions (:meth:`Broker.positions`); otherwise
the pipeline falls back to :func:`energy_trader.strategy.analyze`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from energy_trader.brokers.base import Order, Position
from energy_trader.config import Settings
from energy_trader.strategy import _size_entry

logger = logging.getLogger(__name__)


@dataclass
class Allocation:
    """One symbol's target vs. held position for today (for logs + the summary)."""

    symbol: str
    state: str  # "LONG" / "FLAT" / "SKIP" (trend) or "48% → 50%" (allocation)
    target: float
    held: float
    order: Order | None = None
    note: str = ""

    def describe(self) -> str:
        return (f"{self.symbol} {self.state} target ${self.target:,.2f} · "
                f"held ${self.held:,.2f}{' · ' + self.note if self.note else ''}")


def in_trend(close: pd.Series, fast: int, slow: int) -> bool:
    """Long regime: fast SMA above slow SMA on the latest bar (warmup ⇒ flat)."""
    if len(close) < slow:
        return False
    return bool(close.rolling(fast).mean().iloc[-1] > close.rolling(slow).mean().iloc[-1])


def _plan_symbol(
    symbol: str,
    close: pd.Series,
    settings: Settings,
    equity: float | None,
    pos: Position | None,
) -> Allocation:
    trend = in_trend(close, settings.fast_window, settings.slow_window)
    held = pos.market_value if pos else 0.0
    tag = f"SMA{settings.fast_window}/{settings.slow_window}"

    if not trend:
        alloc = Allocation(symbol, "FLAT", 0.0, held)
        if pos and pos.qty > 0:
            alloc.order = Order(symbol, "sell", quantity=pos.qty,
                                extended_hours=settings.extended_hours,
                                reason=f"{tag} out of trend · exit full position")
            alloc.note = "exit"
        return alloc

    target, sizing = _size_entry(close, settings, equity)
    alloc = Allocation(symbol, "LONG", target, held)
    drift = target - held
    threshold = max(settings.rebalance_band * target, settings.rebalance_min_trade)
    if abs(drift) <= threshold:
        alloc.note = f"within band (±${threshold:,.2f})"
        return alloc

    reason = f"{tag} rebalance · target {sizing}"
    if drift > 0:
        alloc.order = Order(symbol, "buy", notional=round(drift, 2),
                            extended_hours=settings.extended_hours, reason=reason)
    elif target < settings.rebalance_min_trade:
        # Target is ~$0 (shock vol) — trimming to dust is just an exit.
        alloc.order = Order(symbol, "sell", quantity=pos.qty,
                            extended_hours=settings.extended_hours,
                            reason=f"{tag} vol shock · exit full position")
    else:
        alloc.order = Order(symbol, "sell", notional=round(-drift, 2),
                            extended_hours=settings.extended_hours, reason=reason)
    alloc.note = "add" if drift > 0 else "trim"
    return alloc


def plan_rebalance(
    data: dict[str, pd.DataFrame],
    settings: Settings,
    equity: float | None,
    positions: dict[str, Position],
) -> list[Allocation]:
    """Target-vs-held plan for every configured symbol (orders where drift > band).

    Held symbols outside ``settings.assets`` are left alone — this bot only
    manages its own universe.
    """
    plan: list[Allocation] = []
    for symbol in settings.assets:
        df = data.get(symbol)
        pos = positions.get(symbol)
        if df is None or df.empty or "Close" not in df.columns:
            logger.warning("No usable Close series for %s; not rebalancing.", symbol)
            continue
        if df.attrs.get("synthetic"):
            logger.warning("%s bars are synthetic (data outage); not rebalancing.", symbol)
            plan.append(Allocation(symbol, "SKIP", 0.0, pos.market_value if pos else 0.0,
                                   note="skipped: synthetic data"))
            continue
        alloc = _plan_symbol(symbol, df["Close"].dropna().astype(float),
                             settings, equity, pos)
        logger.info("%s", alloc.describe())
        plan.append(alloc)
    return plan
