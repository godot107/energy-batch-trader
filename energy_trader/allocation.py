"""Strategic allocation: fixed target weights, quarterly rebalance, monthly deposits.

The default live strategy (``EOD_STRATEGY=allocation``). Instead of timing the
market with the SMA (which trailed buy-and-hold in research), hold a fixed energy
mix — by default **50% XLE / 30% USO / 20% cash** — and keep it there:

- **Quarterly rebalance** (calendar + tolerance): on runs in the first
  ``rebalance_window_days`` of Jan/Apr/Jul/Oct, if any weight is more than
  ``alloc_tolerance`` (3pp) off target, trade *every* symbol back to target
  (sells and buys). The window gives several daily runs a chance to fire if one
  fails; the tolerance stops it re-trading once it's on target. Stateless — no
  "last rebalanced" file to keep in sync.
- **Deposits** (e.g. $100/month, made by hand on bank rails): on any day,
  cash above the cash target by ≥ ``deploy_min_cash`` is invested **buy-only**
  into the underweight symbols, so new money doesn't idle until the next quarter
  and nothing is sold (no realized gains) between rebalances.

Deposits need no schedule or config: they simply show up as extra cash in the
broker's equity and get deployed on the next run. (Alpaca's paper Trading API
can't deposit, so on paper this path only fires if cash is added by hand.)

Market values come from the broker, so this needs no price bars (and so can't
be fooled by the synthetic-data fallback). The anomaly gate still halts it.
"""

from __future__ import annotations

import logging
from datetime import date

from energy_trader.brokers.base import Order, Position
from energy_trader.config import Settings
from energy_trader.rebalance import Allocation

logger = logging.getLogger(__name__)

REBALANCE_MONTHS = (1, 4, 7, 10)


def in_rebalance_window(settings: Settings, today: date) -> bool:
    return today.month in REBALANCE_MONTHS and today.day <= settings.rebalance_window_days


def next_rebalance(today: date) -> date:
    for m in REBALANCE_MONTHS:
        if m > today.month:
            return date(today.year, m, 1)
    return date(today.year + 1, 1, 1)


def plan_allocation(
    settings: Settings,
    equity: float,
    positions: dict[str, Position],
    today: date,
) -> tuple[list[Allocation], str]:
    """Today's per-symbol plan + a one-line headline for the summary.

    Orders are listed sells-first so their proceeds fund the buys.
    """
    weights = settings.target_weights
    cash_weight = 1.0 - sum(weights.values())
    held = {s: (positions[s].market_value if s in positions else 0.0) for s in weights}
    # Cash = equity minus *everything* held (incl. symbols outside the mix).
    cash = equity - sum(p.market_value for p in positions.values())

    plan = [
        Allocation(s, f"{held[s] / equity:.0%} → {w:.0%}", round(w * equity, 2), held[s])
        for s, w in weights.items()
    ]
    head = f"Equity ${equity:,.2f} · cash {cash / equity:.0%} → {cash_weight:.0%}"

    max_drift = max(abs(a.held - a.target) / equity for a in plan)
    if in_rebalance_window(settings, today) and max_drift > settings.alloc_tolerance:
        head += f" · QUARTERLY REBALANCE (max drift {max_drift:.1%})"
        for a in plan:
            delta = a.target - a.held
            if abs(delta) < settings.rebalance_min_trade:
                continue
            side = "buy" if delta > 0 else "sell"
            a.order = Order(a.symbol, side, notional=round(abs(delta), 2),
                            extended_hours=settings.extended_hours,
                            reason=f"quarterly rebalance to {weights[a.symbol]:.0%}")
            a.note = side
    else:
        excess = cash - cash_weight * equity
        if excess >= settings.deploy_min_cash:
            # Buy-only: steer new cash to the underweights (by shortfall), or pro
            # rata to the targets if nothing is underweight.
            short = {a.symbol: max(0.0, a.target - a.held) for a in plan}
            basis = short if sum(short.values()) > 0 else dict(weights)
            total = sum(basis.values())
            head += f" · deploying ${excess:,.2f} new cash"
            for a in plan:
                amount = round(excess * basis[a.symbol] / total, 2)
                if amount >= settings.rebalance_min_trade:
                    a.order = Order(a.symbol, "buy", notional=amount,
                                    extended_hours=settings.extended_hours,
                                    reason="deploy new cash toward target weights")
                    a.note = "deposit buy"
        head += f" · next rebalance {next_rebalance(today):%Y-%m-%d}"

    plan.sort(key=lambda a: 0 if a.order and a.order.side == "sell" else 1)
    for a in plan:
        logger.info("%s", a.describe())
    return plan, head
