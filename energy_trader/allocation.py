"""Strategic allocation: fixed target weights, slow trend filter, quarterly rebalance.

The default live strategy (``EOD_STRATEGY=allocation``). Hold a fixed energy mix
— by default **70% XLE / 10% USO / 20% cash** — and keep it there, stepping a
symbol aside only while its slow trend is down. Why this mix (20y backtest,
2006–2026, see RUNBOOK "Strategic allocation"): USO bleeds roll yield (−6%/yr,
−98% peak-to-trough), so it's a small oil-price sleeve, not a core holding; the
50/200 filter cut max drawdown from ~−60% to ~−27% in both decades tested.

- **Trend filter** (``alloc_trend_filter``, SMA ``alloc_fast``/``alloc_slow``
  = 50/200): a symbol's *effective* target is its weight while fast SMA > slow
  SMA, else 0 (its share waits in cash). A flip trades **on the day it happens**:
  exit sells the full held quantity; re-entry buys back up to target. Slow by
  design — ~1 round trip a year, unlike the fast 5/20 that churned 10x+/yr.

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

Market values come from the broker; bars are only needed for the trend filter,
and synthetic (mock) or too-short bars **block all trading** for the day rather
than fake a trend flip. The anomaly gate still halts it.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from energy_trader.brokers.base import Order, Position
from energy_trader.config import Settings
from energy_trader.rebalance import Allocation, in_trend

logger = logging.getLogger(__name__)

REBALANCE_MONTHS = (1, 4, 7, 10)


def in_rebalance_window(settings: Settings, today: date) -> bool:
    return today.month in REBALANCE_MONTHS and today.day <= settings.rebalance_window_days


def next_rebalance(today: date) -> date:
    for m in REBALANCE_MONTHS:
        if m > today.month:
            return date(today.year, m, 1)
    return date(today.year + 1, 1, 1)


def trend_states(
    settings: Settings, closes: dict[str, pd.Series] | None
) -> tuple[dict[str, bool] | None, str]:
    """Per-symbol slow-trend state, or ``(None, why)`` if it can't be trusted."""
    if not settings.alloc_trend_filter:
        return {s: True for s in settings.target_weights}, ""
    states: dict[str, bool] = {}
    for s in settings.target_weights:
        df = (closes or {}).get(s)
        if df is None or df.empty or "Close" not in df.columns:
            return None, f"no bars for {s}"
        if df.attrs.get("synthetic"):
            return None, f"{s} bars are synthetic (data outage)"
        close = df["Close"].dropna()
        if len(close) < settings.alloc_slow:
            return None, f"{s} has {len(close)} bars < {settings.alloc_slow}"
        states[s] = in_trend(close, settings.alloc_fast, settings.alloc_slow)
    return states, ""


def _sell_all(settings: Settings, pos: Position, reason: str) -> Order:
    return Order(pos.symbol, "sell", quantity=pos.qty,
                 extended_hours=settings.extended_hours, reason=reason)


def plan_allocation(
    settings: Settings,
    equity: float,
    positions: dict[str, Position],
    today: date,
    closes: dict[str, pd.DataFrame] | None = None,
) -> tuple[list[Allocation], str]:
    """Today's per-symbol plan + a one-line headline for the summary.

    Priority: quarterly rebalance (in the window, if drifted) > trend flips >
    deploying new cash. Orders are listed sells-first so proceeds fund buys.
    """
    states, why = trend_states(settings, closes)
    if states is None:
        logger.warning("Trend filter unavailable (%s); no trades today.", why)
        return [], f"Equity ${equity:,.2f} · NO TRADES — trend filter unavailable ({why})"

    weights = settings.target_weights
    eff = {s: (w if states[s] else 0.0) for s, w in weights.items()}
    cash_weight = 1.0 - sum(eff.values())
    held = {s: (positions[s].market_value if s in positions else 0.0) for s in weights}
    # Cash = equity minus *everything* held (incl. symbols outside the mix).
    cash = equity - sum(p.market_value for p in positions.values())

    plan = [
        Allocation(s, f"{held[s] / equity:.0%} → {eff[s]:.0%}"
                      + ("" if states[s] else " (trend ↓)"),
                   round(eff[s] * equity, 2), held[s])
        for s in weights
    ]
    trend_tag = " ".join(f"{s}{'↑' if states[s] else '↓'}" for s in weights)
    head = (f"Equity ${equity:,.2f} · cash {cash / equity:.0%} → {cash_weight:.0%}"
            + (f" · trend {trend_tag}" if settings.alloc_trend_filter else ""))
    filt = f"SMA{settings.alloc_fast}/{settings.alloc_slow}"

    max_drift = max(abs(a.held - a.target) / equity for a in plan)
    if in_rebalance_window(settings, today) and max_drift > settings.alloc_tolerance:
        head += f" · QUARTERLY REBALANCE (max drift {max_drift:.1%})"
        for a in plan:
            delta = a.target - a.held
            if a.target == 0 and a.symbol in positions:
                a.order = _sell_all(settings, positions[a.symbol],
                                    f"quarterly rebalance · {filt} trend down → 0%")
            elif abs(delta) >= settings.rebalance_min_trade:
                side = "buy" if delta > 0 else "sell"
                a.order = Order(a.symbol, side, notional=round(abs(delta), 2),
                                extended_hours=settings.extended_hours,
                                reason=f"quarterly rebalance to {eff[a.symbol]:.0%}")
            if a.order:
                a.note = a.order.side
    else:
        # Trend flips trade the day they happen.
        for a in plan:
            if a.target == 0 and a.symbol in positions and positions[a.symbol].qty > 0:
                a.order = _sell_all(settings, positions[a.symbol],
                                    f"{filt} trend turned down · exit")
                a.note = "trend exit"
            elif a.target > 0 and a.held < 0.5 * a.target:
                a.order = Order(a.symbol, "buy", notional=round(a.target - a.held, 2),
                                extended_hours=settings.extended_hours,
                                reason=f"{filt} trend up · enter to {eff[a.symbol]:.0%}")
                a.note = "trend entry"
        # New cash (deposits) → buy-only into in-trend underweights.
        entries = sum(a.order.notional for a in plan
                      if a.order and a.order.side == "buy")
        excess = cash - cash_weight * equity - entries
        free = [a for a in plan if a.order is None and a.target > 0]
        if excess >= settings.deploy_min_cash and free:
            # By shortfall, or pro rata to the targets if nothing is underweight.
            short = {a.symbol: max(0.0, a.target - a.held) for a in free}
            basis = short if sum(short.values()) > 0 else {a.symbol: a.target for a in free}
            total = sum(basis.values())
            head += f" · deploying ${excess:,.2f} new cash"
            for a in free:
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
