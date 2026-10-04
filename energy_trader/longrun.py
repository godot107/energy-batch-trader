"""20-year reality check (research-only): live strategies vs. SPY, 2006 → today.

Alpaca's bar history starts in 2016, which skips the two regimes that matter
most for energy — 2008 and the 2014–15 oil crash — and flattered every earlier
result (50/30/20 looked like +9%/yr from 2016; it was +3.6%/yr from 2006). This
module pulls split/dividend-adjusted daily closes from Yahoo (``yfinance``,
lazy import, research-only dep) and replays the *live rules* at the account
level:

- signal on close t, trade at close t+1 (no lookahead); holdings drift between
  trades; cost ``COST`` × traded $; idle cash earns the 13-week T-bill (^IRX),
  which is fair to the cash-heavy trend variants.

Never imported by the live pipeline. Used by ``notebooks/research.ipynb`` §5
and the numbers in ``blog.md`` / RUNBOOK "Strategic allocation".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

TRADING_DAYS = 252
COST = 0.0010  # per $ traded (commission-free, but spread + slippage)
REBALANCE_MONTHS = (1, 4, 7, 10)

Weights = dict[str, float]
TargetFn = Callable[[int], Weights]
RuleFn = Callable[[int, Weights, Weights], list[str]]


def load_history(symbols=("SPY", "XLE", "USO"), start="2006-04-10"):
    """Adjusted daily closes + the daily T-bill rate (as a decimal) from Yahoo."""
    import yfinance as yf

    px = yf.download(list(symbols), start=start, auto_adjust=True,
                     progress=False)["Close"].dropna()
    irx = yf.download("^IRX", start=start, auto_adjust=True, progress=False)["Close"]
    tbill = irx.squeeze().reindex(px.index).ffill().fillna(0.0) / 100
    return px, tbill


# --- engine -----------------------------------------------------------------
@dataclass
class Run:
    equity: pd.Series
    exposure: pd.Series
    traded: float


def simulate(px, tbill, symbols, target: TargetFn, rule: RuleFn, cash0=10_000.0) -> Run:
    ret = px[symbols].pct_change().fillna(0.0).values
    rate = tbill.values / TRADING_DAYS
    hold = np.zeros(len(symbols))
    cash, traded = cash0, 0.0
    eq_out, ex_out = np.empty(len(px)), np.empty(len(px))
    for i in range(len(px)):
        hold *= 1.0 + ret[i]
        cash *= 1.0 + rate[i]
        eq = cash + hold.sum()
        if i >= 1:
            tgt = target(i - 1)  # decided on yesterday's close
            held_w = {s: hold[k] / eq for k, s in enumerate(symbols)}
            for s in rule(i, held_w, tgt):
                k = symbols.index(s)
                d = tgt.get(s, 0.0) * eq - hold[k]
                hold[k] += d
                cash -= d + abs(d) * COST
                traded += abs(d)
        eq_out[i] = cash + hold.sum()
        ex_out[i] = hold.sum() / eq_out[i]
    return Run(pd.Series(eq_out, px.index), pd.Series(ex_out, px.index), traded)


def metrics(run: Run, tbill: pd.Series) -> dict[str, float]:
    e = run.equity
    r = e.pct_change().dropna()
    yrs = len(r) / TRADING_DAYS
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    excess = r - tbill.reindex(r.index).values / TRADING_DAYS
    dd = (e / e.cummax() - 1).min()
    return {"CAGR": cagr, "Vol": r.std() * np.sqrt(TRADING_DAYS),
            "Sharpe": excess.mean() / r.std() * np.sqrt(TRADING_DAYS),  # excess of T-bill
            "MaxDD": dd, "Calmar": cagr / -dd, "Exposure": run.exposure.mean(),
            "Turnover": run.traded / e.mean() / yrs}


# --- rebalance rules (mirror allocation.py / rebalance.py) ------------------
def buy_and_hold(px) -> RuleFn:
    return lambda i, held, tgt: list(held) if i == 1 else []


def quarterly(px, tol=0.03) -> RuleFn:
    """allocation.py: days 1–7 of Jan/Apr/Jul/Oct, only if a weight drifted > tol."""
    def rule(i, held, tgt):
        d = px.index[i]
        if i == 1:
            return list(held)
        if d.month in REBALANCE_MONTHS and d.day <= 7:
            if max(abs(held[s] - tgt.get(s, 0.0)) for s in held) > tol:
                return list(held)
        return []
    return rule


def quarterly_with_flips(px, tol=0.03) -> RuleFn:
    """allocation.py with the trend filter: quarterly, plus trade any trend flip."""
    q = quarterly(px, tol)

    def rule(i, held, tgt):
        flips = [s for s in held if (tgt.get(s, 0.0) > 0) != (held[s] > 1e-3)]
        return sorted(set(flips) | set(q(i, held, tgt)))
    return rule


def band(rel=0.20, min_abs=0.005) -> RuleFn:
    """rebalance.py (trend mode): trade when drift > rel × target; exit on 0."""
    def rule(i, held, tgt):
        out = []
        for s in held:
            t = tgt.get(s, 0.0)
            if (t == 0.0 and held[s] > 0) or (t and abs(t - held[s]) > max(rel * t, min_abs)):
                out.append(s)
        return out
    return rule


# --- targets ----------------------------------------------------------------
def static(w: Weights) -> TargetFn:
    return lambda i: w


def trend(px, w: Weights, fast: int, slow: int, voltgt=False,
          vol_window=20, vol_target=0.20) -> TargetFn:
    """w × (SMA fast > SMA slow) [× min(1, vol_target / realized vol)]."""
    cols = {}
    for s in w:
        c = px[s]
        on = (c.rolling(fast).mean() > c.rolling(slow).mean()).astype(float)
        if voltgt:
            rv = c.pct_change().rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
            on = on * (vol_target / rv).clip(upper=1.0).where(rv > 0).fillna(0.0)
        cols[s] = (on * w[s]).values
    return lambda i: {s: float(cols[s][i]) for s in w}


def strategies(px) -> dict[str, tuple[list[str], TargetFn, RuleFn]]:
    """The comparison set: benchmarks, old + new live allocation, SMA variants."""
    old, new = {"XLE": 0.5, "USO": 0.3}, {"XLE": 0.7, "USO": 0.1}
    E = ["XLE", "USO"]
    return {
        "SPY buy & hold": (["SPY"], static({"SPY": 1.0}), buy_and_hold(px)),
        "XLE buy & hold": (["XLE"], static({"XLE": 1.0}), buy_and_hold(px)),
        "USO buy & hold": (["USO"], static({"USO": 1.0}), buy_and_hold(px)),
        "50/30/20 quarterly (first live mix)": (E, static(old), quarterly(px)),
        "70/10/20 quarterly, no filter": (E, static(new), quarterly(px)),
        "70/10/20 + SMA50/200 filter (LIVE)": (
            E, trend(px, new, 50, 200), quarterly_with_flips(px)),
        "SMA5/20 as first configured (10%/sym, voltgt)": (
            E, trend(px, {"XLE": .1, "USO": .1}, 5, 20, voltgt=True), band()),
        "SMA5/20 on 50/30, voltgt": (E, trend(px, old, 5, 20, voltgt=True), band()),
        "SMA50/200 on 50/30": (E, trend(px, old, 50, 200), band()),
        "SPY + SMA50/200 filter": (["SPY"], trend(px, {"SPY": 1.0}, 50, 200), band()),
    }


def compare(px, tbill) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    rows, curves = [], {}
    for name, (syms, tgt, rule) in strategies(px).items():
        run = simulate(px, tbill, syms, tgt, rule)
        curves[name] = run.equity
        rows.append({"strategy": name, **metrics(run, tbill)})
    return pd.DataFrame(rows).set_index("strategy"), curves


PERIODS = {"2006–10 (GFC)": ("2006", "2010"), "2011–15 (oil crash)": ("2011", "2015"),
           "2016–19": ("2016", "2019"), "2020 (COVID, −$37 WTI)": ("2020", "2020"),
           "2021–22 (energy bull)": ("2021", "2022"), "2023–26": ("2023", "2026")}


def subperiods(curves: dict[str, pd.Series]) -> pd.DataFrame:
    """Total return of each strategy inside each regime."""
    return pd.DataFrame({
        name: {p: e.loc[a:b].iloc[-1] / e.loc[a:b].iloc[0] - 1
               for p, (a, b) in PERIODS.items()}
        for name, e in curves.items()
    }).T


def sma_sweep(px, tbill, w: Weights | None = None) -> pd.DataFrame:
    """Every fast/slow (± vol-target) on the same budget — judge the
    *distribution*, not the best cell (the best of 34 is a selection-biased pick)."""
    w = w or {"XLE": 0.5, "USO": 0.3}
    rows = []
    for fast in (5, 10, 20, 50):
        for slow in (20, 50, 100, 150, 200):
            if fast >= slow:
                continue
            for vt in (True, False):
                run = simulate(px, tbill, list(w), trend(px, w, fast, slow, voltgt=vt), band())
                rows.append({"fast": fast, "slow": slow, "voltgt": vt, **metrics(run, tbill)})
    return pd.DataFrame(rows)
