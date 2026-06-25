"""Carry / roll-yield signal for USO — the energy-specific term-structure edge.

USO holds front-month WTI futures and rolls them monthly, so its return is WTI
*spot* return **plus a roll yield**. That roll yield is positive in
**backwardation** (front richer than the next contract) and negative in
**contango**. Edwards (*Energy Trading & Investing*, 2e, pp.116, 127, 129)
frames this as the cost-of-carry / convenience-yield structure of the forward
curve; it's a slow-moving, persistent regime driven by inventories — which makes
*trailing* roll yield a reasonable predictor of *next-period* roll yield.

We don't need the futures curve to measure it. The realized roll yield over a
window is just USO's return minus WTI spot's return over the same window — the
exact identity :mod:`energy_trader.roll` uses for its contango diagnostic. Here
we turn that diagnostic into a signal:

    carry = (trailing USO return) − (trailing WTI-spot return)
    long USO while carry > +band (backwardation), flat while carry < −band.

Deterministic and LLM-free. It mirrors :func:`strategy.crossover_series` event
semantics exactly — a "buy"/"sell" fires only when the regime *flips* — so the
live point-in-time decision is the last element of the same series the backtest
replays, and the two can never drift.

WTI spot comes from EIA (``RWTC``); without an ``EIA_API_KEY`` there is no carry
signal (the caller degrades gracefully).
"""

from __future__ import annotations

import logging

import pandas as pd

from energy_trader.roll import _to_naive_dates

logger = logging.getLogger(__name__)


def carry_value(
    uso_close: pd.Series, wti_spot: pd.Series, window: int
) -> tuple[pd.Series, pd.Series]:
    """Return ``(aligned_uso_close, carry)`` on the USO∩WTI common dates.

    ``carry`` is the per-bar realized roll yield = trailing USO return − trailing
    WTI-spot return over ``window`` aligned bars (positive ⇒ backwardation). The
    aligned close is returned alongside so a backtest scores returns and
    buy-and-hold over exactly the window the signal saw. Single source of truth:
    both the standalone signal and the trend filter derive from here.
    """
    df = pd.concat(
        [
            _to_naive_dates(uso_close).rename("uso"),
            _to_naive_dates(wti_spot).rename("wti"),
        ],
        axis=1,
    ).dropna()

    uso_ret = df["uso"] / df["uso"].shift(window) - 1.0
    wti_ret = df["wti"] / df["wti"].shift(window) - 1.0
    return df["uso"], uso_ret - wti_ret


def carry_frame(
    uso_close: pd.Series, wti_spot: pd.Series, window: int, band: float = 0.0
) -> tuple[pd.Series, pd.Series]:
    """Return ``(aligned_uso_close, signal)`` — per-bar "buy"/"sell"/"hold".

    Event semantics matching :func:`strategy.crossover_series`: "buy" fires only
    when carry crosses *up* through ``+band`` (entering backwardation), "sell"
    when it crosses *down* through ``−band`` (entering contango). The live
    point-in-time decision is the last element; the backtest replays the series.
    """
    close, carry = carry_value(uso_close, wti_spot, window)
    prev = carry.shift(1)  # NaN warmup bars compare False → "hold"

    signal = pd.Series("hold", index=close.index, dtype=object)
    signal[(prev <= band) & (carry > band)] = "buy"  # entered backwardation
    signal[(prev >= -band) & (carry < -band)] = "sell"  # entered contango
    return close, signal


def carry_filter(
    uso_close: pd.Series, wti_spot: pd.Series, window: int, band: float = 0.0
) -> tuple[pd.Series, pd.Series]:
    """Return ``(aligned_uso_close, ok)`` — a per-bar boolean regime gate.

    ``ok`` is True when USO is *not* in deep contango (``carry >= -band``), i.e.
    safe to hold. Used to gate a trend signal away from roll-yield bleed: hold
    only when the trend says long *and* ``ok``. NaN warmup bars gate False
    (stand aside until the regime is known) — conservative by design.
    """
    close, carry = carry_value(uso_close, wti_spot, window)
    return close, (carry >= -band).fillna(False)


def carry_series(
    uso_close: pd.Series, wti_spot: pd.Series, window: int, band: float = 0.0
) -> pd.Series:
    """Per-bar "buy"/"sell"/"hold" from the USO-vs-WTI realized roll yield."""
    return carry_frame(uso_close, wti_spot, window, band)[1]


def carry_signal(
    uso_close: pd.Series, wti_spot: pd.Series, window: int, band: float = 0.0
) -> str:
    """Latest "buy"/"sell"/"hold" carry decision (the live point-in-time call)."""
    signal = carry_series(uso_close, wti_spot, window, band)
    return str(signal.iloc[-1]) if len(signal) else "hold"
