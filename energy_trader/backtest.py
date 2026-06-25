"""Lightweight backtest harness — replays the *live* signal over history.

Reuses :func:`energy_trader.strategy.crossover_series`, so the backtested rule is
provably identical to what runs live (no logic drift — the usual way backtests
lie). Long-only, all-in/all-out, matching the current long-biased crossover.

numpy/pandas only — no vectorbt (that stays an optional research path). To avoid
lookahead, a signal computed on bar *t*'s close is entered on bar *t+1*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from energy_trader.config import Settings
from energy_trader.strategy import crossover_series

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def perf_metrics(net: pd.Series, initial_cash: float = 10_000.0):
    """Equity curve + headline metrics from a net per-bar return series.

    Returns ``(total_return, cagr, sharpe, max_drawdown, equity)``. Shared by the
    single-asset and pairs backtests so they're scored identically.
    """
    equity = (1.0 + net).cumprod() * initial_cash
    n = len(net)
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    cagr = (
        float((equity.iloc[-1] / initial_cash) ** (TRADING_DAYS / n) - 1.0) if n else 0.0
    )
    std = net.std()
    sharpe = float(net.mean() / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    max_dd = float((equity / equity.cummax() - 1.0).min())
    return total_return, cagr, sharpe, max_dd, equity


@dataclass
class BacktestResult:
    symbol: str
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    n_trades: int
    win_rate: float
    bh_total_return: float  # buy-and-hold benchmark over the same window
    equity: pd.Series = field(repr=False)

    def describe(self) -> str:
        return (
            f"{self.symbol:<9} ret {self.total_return:+7.1%}  CAGR {self.cagr:+6.1%}  "
            f"Sharpe {self.sharpe:5.2f}  MaxDD {self.max_drawdown:6.1%}  "
            f"trades {self.n_trades:3d}  win {self.win_rate:4.0%}  |  "
            f"B&H {self.bh_total_return:+7.1%}"
        )


def _positions(signal: pd.Series) -> pd.Series:
    """Forward-fill the buy/sell signal into a 0/1 long-only target position."""
    state, out = 0.0, []
    for s in signal:
        if s == "buy":
            state = 1.0
        elif s == "sell":
            state = 0.0
        out.append(state)
    return pd.Series(out, index=signal.index)


def _trade_returns(pos: pd.Series, close: pd.Series) -> list[float]:
    """Per-trade returns (entry→exit fills), for the win rate. Open trade closes
    at the final bar."""
    rets: list[float] = []
    entry: float | None = None
    prev = 0.0
    for ts, p in pos.items():
        if prev == 0.0 and p == 1.0:
            entry = float(close.loc[ts])
        elif prev == 1.0 and p == 0.0 and entry is not None:
            rets.append(float(close.loc[ts]) / entry - 1.0)
            entry = None
        prev = p
    if entry is not None:
        rets.append(float(close.iloc[-1]) / entry - 1.0)
    return rets


def backtest_symbol(
    symbol: str,
    close: pd.Series,
    settings: Settings,
    *,
    vol_target: float | None = None,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> BacktestResult | None:
    """Backtest one symbol's Close series. Returns ``None`` if history is too short.

    ``vol_target`` (annualized) turns on the Kaufman ch.23 volatility-sizing
    overlay: the SMA decides direction, the position is scaled by
    ``target / realized_vol`` (capped at ``settings.vol_max_leverage``). ``None``
    keeps the original all-in/all-out behavior. Trade count / win rate are taken
    from the underlying SMA round-trips so the two variants stay comparable.
    """
    close = close.dropna().astype(float)
    if len(close) < settings.slow_window + 2:
        logger.warning("%s: only %d bars; need > %d. Skipping.",
                       symbol, len(close), settings.slow_window + 1)
        return None

    signal = crossover_series(close, settings.fast_window, settings.slow_window)
    binary = _positions(signal).shift(1).fillna(0.0)  # enter next bar (no lookahead)
    if vol_target is None:
        pos = binary
    else:
        from energy_trader.sizing import vol_target_weight

        weight = vol_target_weight(close, settings.vol_window, vol_target,
                                   settings.vol_max_leverage).shift(1).fillna(0.0)
        pos = binary * weight  # both lagged → no lookahead
    ret = close.pct_change().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.iloc[0])
    net = pos * ret - turnover * fee
    total_return, cagr, sharpe, max_dd, equity = perf_metrics(net, initial_cash)

    trades = _trade_returns(binary, close)  # round-trips from the signal, not sizing
    win_rate = float(np.mean([t > 0 for t in trades])) if trades else 0.0

    bh_total = float(close.iloc[-1] / close.iloc[0] - 1.0)
    return BacktestResult(symbol, total_return, cagr, sharpe, max_dd,
                          len(trades), win_rate, bh_total, equity)


def compare_vol_target(
    symbol: str,
    close: pd.Series,
    settings: Settings,
    *,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """Before/after: SMA baseline vs the same SMA with vol-targeted sizing.

    Both share the B&H column (the same close), so the table reads as a direct
    risk/return trade: the overlay should cut drawdown sharply for some give-up
    in raw return. Returns ``[baseline, vol-targeted]`` (skips any too-short).
    """
    tv = settings.vol_target_annual
    candidates = [
        backtest_symbol(f"{symbol} SMA", close, settings,
                        fee=fee, initial_cash=initial_cash),
        backtest_symbol(f"{symbol} +voltgt{tv:.0%}", close, settings,
                        vol_target=tv, fee=fee, initial_cash=initial_cash),
    ]
    return [r for r in candidates if r is not None]


def backtest_carry(
    uso_close: pd.Series,
    wti_spot: pd.Series,
    settings: Settings,
    *,
    label: str = "carry",
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> BacktestResult | None:
    """Backtest the carry / roll-yield signal on USO (long-only, all-in/all-out).

    Scored with the same machinery as the SMA backtest (``perf_metrics``,
    ``_positions``, ``_trade_returns``) over the USO∩WTI aligned window, so the
    result is directly comparable to the SMA and buy-and-hold baselines.
    """
    from energy_trader.carry import carry_frame

    close, signal = carry_frame(
        uso_close, wti_spot, settings.carry_window, settings.carry_band
    )
    close = close.astype(float)
    if len(close) < settings.carry_window + 2:
        logger.warning("Carry backtest: only %d aligned bars; need > %d.",
                       len(close), settings.carry_window + 1)
        return None

    pos = _positions(signal).shift(1).fillna(0.0)  # enter next bar (no lookahead)
    ret = close.pct_change().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.iloc[0])
    net = pos * ret - turnover * fee
    total_return, cagr, sharpe, max_dd, equity = perf_metrics(net, initial_cash)

    trades = _trade_returns(pos, close)
    win_rate = float(np.mean([t > 0 for t in trades])) if trades else 0.0
    bh_total = float(close.iloc[-1] / close.iloc[0] - 1.0)
    return BacktestResult(label, total_return, cagr, sharpe, max_dd,
                          len(trades), win_rate, bh_total, equity)


def backtest_sma_carry(
    uso_close: pd.Series,
    wti_spot: pd.Series,
    settings: Settings,
    *,
    label: str = "SMA+carry",
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> BacktestResult | None:
    """Backtest the SMA trend signal *gated by the carry regime filter*.

    Long only when the SMA crossover is long **and** USO is not in deep contango
    (``carry_filter`` True) — the textbook use of roll yield: stay out of the
    bleed rather than trade on it. Same scoring/window as the other USO backtests.
    """
    from energy_trader.carry import carry_filter
    from energy_trader.strategy import crossover_series

    close, ok = carry_filter(
        uso_close, wti_spot, settings.carry_window, settings.carry_band
    )
    close = close.astype(float)
    warmup = max(settings.slow_window, settings.carry_window)
    if len(close) < warmup + 2:
        logger.warning("SMA+carry backtest: only %d aligned bars; need > %d.",
                       len(close), warmup + 1)
        return None

    sma_pos = _positions(crossover_series(close, settings.fast_window,
                                          settings.slow_window))
    pos = (sma_pos * ok.astype(float)).shift(1).fillna(0.0)  # gate, then lag
    ret = close.pct_change().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.iloc[0])
    net = pos * ret - turnover * fee
    total_return, cagr, sharpe, max_dd, equity = perf_metrics(net, initial_cash)

    trades = _trade_returns(pos, close)
    win_rate = float(np.mean([t > 0 for t in trades])) if trades else 0.0
    bh_total = float(close.iloc[-1] / close.iloc[0] - 1.0)
    return BacktestResult(label, total_return, cagr, sharpe, max_dd,
                          len(trades), win_rate, bh_total, equity)


def compare_uso_carry(
    uso_close: pd.Series,
    wti_spot: pd.Series,
    settings: Settings,
    *,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """SMA baseline vs the carry-filtered SMA vs standalone carry, on USO.

    All run on the *same* USO∩WTI aligned closes, so the ``B&H`` column is one
    shared buy-and-hold benchmark — the bar every row must clear. The question
    this answers: does gating the SMA by the carry regime (``SMA+carry``) beat
    the plain SMA *baseline* (and B&H)? Returns rows in that reading order.
    """
    from energy_trader.carry import carry_frame

    aligned_close, _ = carry_frame(
        uso_close, wti_spot, settings.carry_window, settings.carry_band
    )
    sma_label = f"SMA({settings.fast_window}/{settings.slow_window})"
    candidates = [
        backtest_symbol(sma_label, aligned_close, settings,
                        fee=fee, initial_cash=initial_cash),  # baseline
        backtest_sma_carry(uso_close, wti_spot, settings, label="SMA+carry",
                           fee=fee, initial_cash=initial_cash),  # candidate
        backtest_carry(uso_close, wti_spot, settings, label="carry-only",
                       fee=fee, initial_cash=initial_cash),  # reference
    ]
    return [r for r in candidates if r is not None]


def format_comparison(results: list[BacktestResult], title: str) -> str:
    """Render the carry/SMA/B&H comparison (B&H is the shared benchmark column)."""
    if not results:
        return "Carry comparison produced no results (insufficient history)."
    lines = [title, "-" * len(title)]
    lines += [r.describe() for r in results]
    lines.append("(B&H column = USO buy-and-hold over the same window — the bar to beat.)")
    return "\n".join(lines)


def run_backtest(
    data: dict[str, pd.DataFrame],
    settings: Settings,
    *,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> dict[str, BacktestResult]:
    """Backtest every symbol in ``data``; returns ``{symbol: BacktestResult}``."""
    results: dict[str, BacktestResult] = {}
    for symbol, df in data.items():
        if "Close" not in df.columns:
            continue
        res = backtest_symbol(symbol, df["Close"], settings, fee=fee,
                              initial_cash=initial_cash)
        if res is not None:
            results[symbol] = res
    return results


def format_report(results: dict[str, BacktestResult], settings: Settings) -> str:
    """A human-readable report of the run."""
    if not results:
        return "Backtest produced no results (insufficient history)."
    header = (
        f"Backtest — SMA({settings.fast_window}/{settings.slow_window}), "
        f"long-only, fee {0.0005:.2%}/trade"
    )
    lines = [header, "-" * len(header)]
    lines += [r.describe() for r in results.values()]
    return "\n".join(lines)
