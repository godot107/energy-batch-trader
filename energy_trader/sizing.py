"""Volatility-targeted position sizing (Kaufman, *Trading Systems and Methods*,
ch.23) — a risk overlay, not a signal.

The directional signal (SMA, carry, …) decides *whether* to be long; this decides
*how much*. We scale the position so its expected volatility is roughly constant:

    weight = target_vol / realized_vol     (capped at max_leverage)

When the market gets violent — exactly the fat-tail episodes the energy texts warn
about (2020 negative-WTI, war spikes) — realized vol blows up and the weight
shrinks *before* the worst of the drawdown, instead of riding a full position into
it. Kaufman (p.1048): a de-leveraged program "is 10 times more likely to survive a
price shock." With ``max_leverage = 1.0`` this only ever *cuts* exposure (long-only,
no margin) — appropriate for a small real-money cash account.

Close-only (no intraday high/low), so realized vol is the rolling std of daily
returns rather than ATR; same idea, available from the data we already fetch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def realized_vol(close: pd.Series, window: int) -> pd.Series:
    """Annualized rolling realized volatility from daily returns."""
    ret = close.pct_change()
    return ret.rolling(window).std() * np.sqrt(TRADING_DAYS)


def vol_target_weight(
    close: pd.Series,
    window: int,
    target_vol: float,
    max_leverage: float = 1.0,
) -> pd.Series:
    """Per-bar sizing factor in ``[0, max_leverage]`` (``target_vol / realized_vol``).

    Warmup bars (no vol estimate yet) size to 0 — flat until risk is measurable.
    """
    rv = realized_vol(close, window)
    weight = (target_vol / rv).clip(upper=max_leverage)
    return weight.where(rv > 0).fillna(0.0)
