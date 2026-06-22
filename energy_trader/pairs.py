"""Pairs trading (USO/XLE) — market-neutral spread mean reversion.

A deterministic alternative to the directional SMA crossover: trade the *spread*
between two cointegrated energy instruments instead of their direction. When the
hedge-ratio-adjusted spread stretches to a z-score extreme, fade it (short the
rich leg / long the cheap leg) and exit as it reverts. Being market-neutral, it
does not need to beat a rising market — the regime where the trend-follower lost.

Method (Jansen, *ML for Algorithmic Trading 2e*, ch.9; Kaufman p.633):
  - hedge ratio ``beta`` from a rolling regression of log prices,
  - rolling z-score of the spread,
  - enter at ``|z| >= entry_z``, exit near ``|z| <= exit_z``, bail at ``|z| >= stop_z``,
  - diagnostics: Engle-Granger cointegration p-value (optional, statsmodels) and
    the Ornstein-Uhlenbeck half-life of mean reversion.

numpy/pandas for the strategy; statsmodels is used lazily only for the coint test.
To avoid lookahead, both the hedge ratio and the position are lagged one bar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from energy_trader.backtest import perf_metrics
from energy_trader.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class PairsResult:
    pair: str
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    n_trades: int
    win_rate: float
    hedge_ratio: float
    half_life: float
    coint_pvalue: float | None
    tradeable: bool  # passes the cointegration gate (coint p <= pairs_coint_max)
    equity: pd.Series = field(repr=False)

    def describe(self) -> str:
        cp = f"{self.coint_pvalue:.3f}" if self.coint_pvalue is not None else "n/a"
        hl = f"{self.half_life:.0f}d" if np.isfinite(self.half_life) else "inf"
        gate = "PASS" if self.tradeable else "FAIL"
        return (
            f"{self.pair:<9} ret {self.total_return:+7.1%}  CAGR {self.cagr:+6.1%}  "
            f"Sharpe {self.sharpe:5.2f}  MaxDD {self.max_drawdown:6.1%}  "
            f"trades {self.n_trades:3d}  win {self.win_rate:4.0%}  |  "
            f"beta {self.hedge_ratio:.2f}  half-life {hl}  coint p {cp}  gate {gate}"
        )


def hedge_ratio(y: pd.Series, x: pd.Series) -> float:
    """OLS slope of ``y`` on ``x`` (with intercept)."""
    return float(np.polyfit(x.values, y.values, 1)[0])


def half_life(spread: pd.Series) -> float:
    """Ornstein-Uhlenbeck half-life of mean reversion, in days.

    Regress Δspread on lagged spread; ``half-life = -ln(2)/slope``. Returns ``inf``
    when the spread is not mean-reverting (slope >= 0).
    """
    s = spread.dropna()
    s_lag, ds = s.shift(1), s.diff()
    s_lag, ds = s_lag.align(ds, join="inner")
    mask = s_lag.notna() & ds.notna()
    if mask.sum() < 3:
        return float("inf")
    slope = np.polyfit(s_lag[mask].values, ds[mask].values, 1)[0]
    return float(-np.log(2) / slope) if slope < 0 else float("inf")


def cointegration_pvalue(y: pd.Series, x: pd.Series) -> float | None:
    """Engle-Granger cointegration p-value (lower ⇒ more cointegrated).

    Lazily uses statsmodels; returns ``None`` if it isn't installed.
    """
    try:
        from statsmodels.tsa.stattools import coint
    except ImportError:
        logger.info("statsmodels not installed — skipping cointegration test.")
        return None
    return float(coint(y.dropna(), x.dropna())[1])


def _positions(z: pd.Series, settings: Settings) -> pd.Series:
    """Spread state machine: +1 long spread (z low), -1 short (z high), 0 flat."""
    entry, exit_, stop = (
        settings.pairs_entry_z,
        settings.pairs_exit_z,
        settings.pairs_stop_z,
    )
    state, out = 0.0, []
    for zv in z:
        if np.isnan(zv):
            out.append(state)
            continue
        if state == 0.0:
            if zv >= entry:
                state = -1.0  # spread rich → short it
            elif zv <= -entry:
                state = 1.0  # spread cheap → long it
        elif state == 1.0:  # long spread: take profit near 0, or bail past stop
            if zv >= -exit_ or zv <= -stop:
                state = 0.0
        elif state == -1.0:
            if zv <= exit_ or zv >= stop:
                state = 0.0
        out.append(state)
    return pd.Series(out, index=z.index)


def _trade_returns(pos: pd.Series, spread_ret: pd.Series) -> list[float]:
    """Compounded return of each holding period (a contiguous non-zero run)."""
    rets: list[float] = []
    cum, prev = 1.0, 0.0
    for p, r in zip(pos, spread_ret):
        if p != 0.0:
            cum *= 1.0 + p * r
        if prev != 0.0 and p == 0.0:
            rets.append(cum - 1.0)
            cum = 1.0
        prev = p
    if prev != 0.0:
        rets.append(cum - 1.0)
    return rets


def backtest_pair(
    y: pd.Series,
    x: pd.Series,
    settings: Settings,
    *,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> PairsResult | None:
    """Backtest a market-neutral spread strategy on two aligned Close series."""
    df = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    win = settings.pairs_lookback
    if len(df) < win + 5:
        logger.warning("Pairs backtest: only %d bars; need > %d.", len(df), win + 4)
        return None

    ly, lx = np.log(df["y"]), np.log(df["x"])

    # Rolling hedge ratio (beta) and z-score of the spread; lagged → no lookahead.
    beta = ly.rolling(win).cov(lx) / lx.rolling(win).var()
    spread = ly - beta * lx
    z = (spread - spread.rolling(win).mean()) / spread.rolling(win).std()

    pos = _positions(z, settings).shift(1).fillna(0.0)

    # Dollar-neutral spread return (gross exposure ~1), using lagged beta.
    ry, rx = df["y"].pct_change().fillna(0.0), df["x"].pct_change().fillna(0.0)
    b = beta.shift(1).abs()
    w = 1.0 / (1.0 + b)
    spread_ret = (w * ry - w * b * rx).fillna(0.0)
    turnover = pos.diff().abs().fillna(abs(pos.iloc[0]))
    net = (pos * spread_ret - turnover * fee).fillna(0.0)

    total_return, cagr, sharpe, max_dd, equity = perf_metrics(net, initial_cash)
    trades = _trade_returns(pos, spread_ret)
    win_rate = float(np.mean([t > 0 for t in trades])) if trades else 0.0

    coint_p = cointegration_pvalue(ly, lx)
    tradeable = coint_p is not None and coint_p <= settings.pairs_coint_max

    return PairsResult(
        pair=f"{y.name}/{x.name}",
        total_return=total_return,
        cagr=cagr,
        sharpe=sharpe,
        max_drawdown=max_dd,
        n_trades=len(trades),
        win_rate=win_rate,
        hedge_ratio=hedge_ratio(ly, lx),
        half_life=half_life(spread),
        coint_pvalue=coint_p,
        tradeable=tradeable,
        equity=equity,
    )


def sweep_pairs(
    data: dict[str, pd.DataFrame],
    settings: Settings,
    *,
    fee: float = 0.0005,
    initial_cash: float = 10_000.0,
) -> list[PairsResult]:
    """Backtest every unique pair in ``data``, ranked: cointegrated first, then by
    ascending coint p-value (the most tradeable pairs float to the top)."""
    syms = [s for s in settings.assets if s in data and "Close" in data[s].columns]
    results: list[PairsResult] = []
    for a, b in combinations(syms, 2):
        res = backtest_pair(
            data[a]["Close"].rename(a), data[b]["Close"].rename(b),
            settings, fee=fee, initial_cash=initial_cash,
        )
        if res is not None:
            results.append(res)
    results.sort(
        key=lambda r: (not r.tradeable,
                       r.coint_pvalue if r.coint_pvalue is not None else 1.0)
    )
    return results


def format_sweep(results: list[PairsResult], settings: Settings) -> str:
    if not results:
        return "No pairs could be evaluated (need ≥2 assets with history)."
    head = (
        f"Pairs sweep — coint gate p<={settings.pairs_coint_max}, "
        f"z in/out/stop {settings.pairs_entry_z}/{settings.pairs_exit_z}/"
        f"{settings.pairs_stop_z}, lookback {settings.pairs_lookback}d"
    )
    return "\n".join([head, "-" * len(head)] + [r.describe() for r in results])
