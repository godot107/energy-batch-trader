"""Deterministic signal generation (SMA crossover).

Kept in plain pandas on purpose: ``vectorbt`` is a *backtesting* tool for
sweeping parameters offline (see RUNBOOK), not a runtime dependency for the
once-daily live signal. The trade decision must be reproducible and LLM-free.
"""

from __future__ import annotations

import logging

import pandas as pd

from energy_trader.brokers.base import Order
from energy_trader.config import Settings

logger = logging.getLogger(__name__)


def crossover_series(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Per-bar "buy"/"sell"/"hold" signal from fast/slow SMA crossovers.

    The single source of truth for the signal rule: the live point-in-time
    decision (:func:`_crossover_signal`) returns the *last* element of this, and
    the backtest replays the *whole* series — so the two can never drift.
    """
    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean()
    diff = fast_ma - slow_ma
    prev = diff.shift(1)  # NaN warmup bars compare False → "hold"

    signal = pd.Series("hold", index=close.index, dtype=object)
    signal[(prev <= 0) & (diff > 0)] = "buy"
    signal[(prev >= 0) & (diff < 0)] = "sell"
    return signal


def _crossover_signal(close: pd.Series, fast: int, slow: int) -> str:
    """Return "buy", "sell", or "hold" from the latest fast/slow SMA cross."""
    if len(close) < slow + 1:
        return "hold"
    return str(crossover_series(close, fast, slow).iloc[-1])


def _vol_target_notional(close: pd.Series, settings: Settings) -> tuple[float, str]:
    """Scale ``default_notional`` by the latest vol-target weight (Kaufman ch.23).

    Returns ``(notional, reason_suffix)``. This is *entry sizing*: it sets the buy
    size from current volatility, but does not rebalance the position daily as vol
    drifts (the backtest does — full live rebalancing needs position-aware brokers,
    a ``TODO(rebalance)``). A weight of ~0 (vol unmeasurable / a shock) ⇒ $0, which
    the caller treats as "skip this entry".
    """
    from energy_trader.sizing import vol_target_weight

    weight = float(
        vol_target_weight(close, settings.vol_window, settings.vol_target_annual,
                          settings.vol_max_leverage).iloc[-1]
    )
    notional = round(settings.default_notional * weight, 2)
    suffix = f" · voltgt {weight:.2f}× (target {settings.vol_target_annual:.0%})"
    return notional, suffix


def analyze(data: dict[str, pd.DataFrame], settings: Settings) -> list[Order]:
    """Turn per-asset price history into a list of intended orders (long-biased)."""
    orders: list[Order] = []

    for symbol, df in data.items():
        if "Close" not in df.columns or df.empty:
            logger.warning("No usable Close series for %s; skipping.", symbol)
            continue

        action = _crossover_signal(
            df["Close"], settings.fast_window, settings.slow_window
        )
        logger.info("%s SMA(%d/%d) signal: %s", symbol, settings.fast_window,
                    settings.slow_window, action.upper())

        if action == "hold":
            continue

        notional = settings.default_notional
        reason = f"SMA{settings.fast_window}/{settings.slow_window} crossover"
        # Vol-target only the entry size; a sell flattens the position in full.
        if action == "buy" and settings.vol_target_live:
            notional, suffix = _vol_target_notional(df["Close"], settings)
            reason += suffix
            logger.info("%s vol-target sizing → $%.2f (from $%.2f)", symbol,
                        notional, settings.default_notional)
            if notional < 1.0:
                logger.info("%s vol weight ~0 (high vol / warmup); skipping entry.",
                            symbol)
                continue

        orders.append(
            Order(
                symbol=symbol,
                side=action,
                notional=notional,
                extended_hours=settings.extended_hours,
                reason=reason,
            )
        )

    return orders
