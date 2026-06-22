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

        orders.append(
            Order(
                symbol=symbol,
                side=action,
                notional=settings.default_notional,
                extended_hours=settings.extended_hours,
                reason=f"SMA{settings.fast_window}/{settings.slow_window} crossover",
            )
        )

    return orders
