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


def _base_notional(settings: Settings, account_equity: float | None) -> tuple[float, str]:
    """Dollar base for a position, before volatility scaling.

    **Fixed-fractional / percent-of-equity** (Kaufman, *Trading Systems and
    Methods*, ch.23, p.1070): ``risk_fraction × account_equity`` when the broker
    reports equity, so size scales with the account instead of a hardcoded amount;
    otherwise the fixed ``default_notional`` (dry-run, or equity unavailable).
    """
    if account_equity is not None and account_equity > 0:
        return (account_equity * settings.risk_fraction,
                f"{settings.risk_fraction:.0%} of ${account_equity:,.0f} equity")
    return settings.default_notional, f"fixed ${settings.default_notional:.0f}"


def _size_entry(
    close: pd.Series, settings: Settings, account_equity: float | None
) -> tuple[float, str]:
    """Dollar size for one long entry + a human-readable sizing reason.

    The money-management ladder (see RUNBOOK "Position sizing" for the full
    reasoning and references):

    1. **base** = percent-of-equity (:func:`_base_notional`), not a hardcoded $;
    2. **× volatility-target weight** (``vol_target / realized_vol``, capped at
       ``vol_max_leverage``) so each position carries roughly *equal risk* —
       Kaufman's preferred method over equal-dollar sizing, which otherwise
       concentrates risk in the most volatile name (ch.23, p.1071), rescaled
       toward a target volatility (p.1068).

    Entry sizing only — it doesn't rebalance the held position as vol drifts (the
    backtest does; full live rebalancing needs position-aware brokers,
    ``TODO(rebalance)``). A weight of ~0 (a shock / warmup) ⇒ ~$0 ⇒ skip the entry.
    """
    base, reason = _base_notional(settings, account_equity)
    notional = base
    if settings.vol_target_live:
        from energy_trader.sizing import vol_target_weight

        weight = float(
            vol_target_weight(close, settings.vol_window, settings.vol_target_annual,
                              settings.vol_max_leverage).iloc[-1]
        )
        notional = base * weight
        reason += f" × voltgt {weight:.2f} (target {settings.vol_target_annual:.0%})"
    return round(notional, 2), reason


def current_signals(
    data: dict[str, pd.DataFrame], settings: Settings
) -> dict[str, str]:
    """Per-symbol "buy"/"sell"/"hold" for the latest bar — today's decision.

    The reporting view of the signal (used by the daily summary), kept separate
    from order construction so a "hold" day still has something to report.
    """
    signals: dict[str, str] = {}
    for symbol, df in data.items():
        if "Close" not in df.columns or df.empty:
            logger.warning("No usable Close series for %s; skipping.", symbol)
            continue
        signals[symbol] = _crossover_signal(
            df["Close"], settings.fast_window, settings.slow_window
        )
    return signals


def analyze(
    data: dict[str, pd.DataFrame],
    settings: Settings,
    signals: dict[str, str] | None = None,
    account_equity: float | None = None,
) -> list[Order]:
    """Turn per-asset price history into a list of intended orders (long-biased).

    ``signals`` may be precomputed (so the pipeline computes them once for both
    the orders and the daily summary); otherwise they're derived here.
    ``account_equity`` (from the broker) enables percent-of-equity sizing; when
    ``None`` the fixed ``default_notional`` is used (dry-run / equity unknown).
    """
    orders: list[Order] = []
    if signals is None:
        signals = current_signals(data, settings)

    for symbol, action in signals.items():
        logger.info("%s SMA(%d/%d) signal: %s", symbol, settings.fast_window,
                    settings.slow_window, action.upper())

        if action == "hold":
            continue

        df = data[symbol]
        # A buy is a vol-targeted entry; a sell flattens, so just the base notional.
        if action == "buy":
            notional, sizing = _size_entry(df["Close"], settings, account_equity)
        else:
            notional, sizing = _base_notional(settings, account_equity)

        if notional < 1.0:
            logger.info("%s sized to ~$0 (shock vol / no equity); skipping.", symbol)
            continue

        logger.info("%s sizing: $%.2f (%s)", symbol, notional, sizing)
        orders.append(
            Order(
                symbol=symbol,
                side=action,
                notional=round(notional, 2),
                extended_hours=settings.extended_hours,
                reason=f"SMA{settings.fast_window}/{settings.slow_window} "
                       f"crossover · {sizing}",
            )
        )

    return orders
