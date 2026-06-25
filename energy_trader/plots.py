"""Backtest visualization — research-only, never imported by the live pipeline.

matplotlib is an optional research dependency (see requirements.txt), lazily
imported here so the core once-daily job keeps its tiny footprint. Everything
renders headless (Agg backend) and is saved to PNG — no interactive windows, so
it works the same from the CLI, a notebook, Airflow, or CI.

The equity/drawdown curves are exactly the ones :func:`backtest.perf_metrics`
already computes for every ``BacktestResult`` (and otherwise throws away), so the
pictures can't disagree with the printed metrics.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _plt():
    """Lazily import matplotlib with the headless Agg backend."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # no display needed; we save to file
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "matplotlib is required for --plot; install the research extras: "
            "pip install matplotlib (it's in requirements.txt)."
        ) from exc


# Known energy shocks within the tradable USO/XLE era — the fat-tail events the
# textbooks warn about (Edwards p.272; Swindle pp.281-282; Kaufman p.27). Shaded
# on charts so outliers in the equity/regime curves are explained, not mysterious.
ENERGY_CRISES: list[tuple[str, str, str]] = [
    ("2015-11-01", "2016-02-29", "OPEC/shale price war"),
    ("2018-10-01", "2018-12-31", "Q4-2018 oil selloff"),
    ("2020-03-01", "2020-05-31", "COVID / negative WTI"),
    ("2022-02-01", "2022-06-30", "Russia–Ukraine"),
]


def _shade_crises(ax, *, label: bool, xmin=None, xmax=None) -> None:
    """Shade known energy-shock windows; optionally label them (top panel only).

    Robust to tz-aware axes: the SMA backtest carries Alpaca's tz-aware index
    while the carry path is tz-naive, so match the crisis bounds to the axis tz.
    """
    tz = getattr(xmin, "tz", None) or getattr(xmax, "tz", None)
    for start, end, name in ENERGY_CRISES:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        if tz is not None:
            s, e = s.tz_localize(tz), e.tz_localize(tz)
        if (xmax is not None and s > xmax) or (xmin is not None and e < xmin):
            continue  # crisis fully outside the plotted range
        if xmin is not None:
            s = max(s, xmin)
        if xmax is not None:
            e = min(e, xmax)
        ax.axvspan(s, e, color="0.5", alpha=0.12, zorder=0)
        if label:
            ax.text(s, 0.98, name, transform=ax.get_xaxis_transform(),
                    rotation=90, va="top", ha="left", fontsize=6.5, color="0.35")


def equity_curves(
    results, bench_close: pd.Series | None = None, initial_cash: float = 10_000.0
) -> dict[str, pd.Series]:
    """Assemble ``{label: equity_series}`` from results, plus a B&H curve.

    All results in a comparison share one index (the aligned window), so the
    buy-and-hold benchmark is reindexed onto it and normalized to the same
    starting cash — an apples-to-apples overlay.
    """
    curves: dict[str, pd.Series] = {r.symbol: r.equity for r in results}
    if bench_close is not None and results:
        idx = results[0].equity.index
        b = bench_close.reindex(idx).ffill().bfill()
        curves["Buy & Hold"] = b / b.iloc[0] * initial_cash
    return curves


def plot_equity_drawdown(
    curves: dict[str, pd.Series], *, path: str | Path, title: str,
    shade_crises: bool = True,
) -> Path:
    """Two stacked panels: equity (log-y) on top, drawdown (underwater) below."""
    from matplotlib.ticker import PercentFormatter

    plt = _plt()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    if shade_crises and curves:
        idx = next(iter(curves.values())).index
        _shade_crises(ax_eq, label=True, xmin=idx.min(), xmax=idx.max())
        _shade_crises(ax_dd, label=False, xmin=idx.min(), xmax=idx.max())
    for label, eq in curves.items():
        is_bh = label == "Buy & Hold"
        ax_eq.plot(eq.index, eq.values, label=label,
                   lw=2.2 if is_bh else 1.4,
                   color="0.4" if is_bh else None,
                   ls="--" if is_bh else "-", zorder=1 if is_bh else 2)
        dd = eq / eq.cummax() - 1.0
        ax_dd.plot(dd.index, dd.values, lw=1.0)
        ax_dd.fill_between(dd.index, dd.values, 0, alpha=0.12)

    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("Equity ($, log scale)")
    ax_eq.set_title(title, fontsize=11)
    ax_eq.legend(loc="upper left", fontsize=8, ncol=2)
    ax_eq.grid(True, which="both", alpha=0.25)

    ax_dd.set_ylabel("Drawdown")
    ax_dd.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax_dd.grid(True, alpha=0.25)
    ax_dd.set_xlabel("Date")

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Wrote %s", path)
    return path


def plot_vol_target(close: pd.Series, settings, *, path: str | Path,
                    shade_crises: bool = True) -> Path:
    """Why vol-targeting works: realized vol (top) drives the position (bottom).

    When vol spikes in a shock, the target/realized weight collapses, so the
    effective exposure (SMA signal × weight) shrinks *before* the worst of the
    drawdown — the mechanism behind the tamer equity curve.
    """
    from matplotlib.ticker import PercentFormatter

    from energy_trader.backtest import _positions
    from energy_trader.sizing import realized_vol, vol_target_weight
    from energy_trader.strategy import crossover_series

    plt = _plt()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    close = close.dropna().astype(float)

    rv = realized_vol(close, settings.vol_window)
    w = vol_target_weight(close, settings.vol_window, settings.vol_target_annual,
                          settings.vol_max_leverage)
    binary = _positions(crossover_series(close, settings.fast_window,
                                         settings.slow_window))
    exposure = (binary * w).fillna(0.0)

    fig, (ax_v, ax_e) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    if shade_crises and len(close):
        _shade_crises(ax_v, label=True, xmin=close.index.min(), xmax=close.index.max())
        _shade_crises(ax_e, label=False, xmin=close.index.min(), xmax=close.index.max())

    ax_v.plot(rv.index, rv.values, color="firebrick", lw=1.0)
    ax_v.axhline(settings.vol_target_annual, color="k", ls="--", lw=0.9,
                 label=f"target {settings.vol_target_annual:.0%}")
    vlim = float(rv.quantile(0.98)) * 1.2
    if float(rv.max()) > vlim:
        ax_v.text(0.99, 0.95, "(2020 spike clipped)", transform=ax_v.transAxes,
                  ha="right", va="top", fontsize=7, color="0.4")
    ax_v.set_ylim(0, vlim)
    ax_v.set_ylabel("Realized vol (ann.)")
    ax_v.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax_v.set_title(f"Vol-targeting mechanism — {settings.vol_window}d realized vol "
                   f"sets the position size", fontsize=11)
    ax_v.legend(loc="upper left", fontsize=8)
    ax_v.grid(True, alpha=0.25)

    ax_e.fill_between(exposure.index, exposure.values, 0, color="steelblue", alpha=0.5)
    ax_e.set_ylabel("Position (signal × weight)")
    ax_e.set_ylim(0, max(1.0, settings.vol_max_leverage) * 1.05)
    ax_e.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax_e.set_xlabel("Date")
    ax_e.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Wrote %s", path)
    return path


def plot_carry_regime(
    uso_close: pd.Series,
    wti_spot: pd.Series,
    window: int,
    band: float,
    *,
    path: str | Path,
    shade_crises: bool = True,
) -> Path:
    """USO price (top) over the carry regime (bottom): trailing roll yield with
    backwardation shaded green, contango red, and the filter threshold marked."""
    plt = _plt()
    from energy_trader.carry import carry_value

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    close, carry = carry_value(uso_close, wti_spot, window)
    carry = carry.dropna()
    close = close.reindex(carry.index)

    fig, (ax_p, ax_c) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [1.3, 1]}
    )
    if shade_crises and len(close):
        _shade_crises(ax_p, label=True, xmin=close.index.min(), xmax=close.index.max())
        _shade_crises(ax_c, label=False, xmin=close.index.min(), xmax=close.index.max())
    ax_p.plot(close.index, close.values, color="black", lw=1.1)
    ax_p.set_ylabel("USO ($, adjusted)")
    ax_p.set_title(
        f"USO carry regime — trailing {window}d roll yield (USO return − WTI-spot "
        f"return)", fontsize=11)
    ax_p.grid(True, alpha=0.25)

    c = carry.values
    ax_c.fill_between(carry.index, c, 0, where=c >= 0, color="green", alpha=0.4,
                      interpolate=True, label="backwardation (long)")
    ax_c.fill_between(carry.index, c, 0, where=c < 0, color="red", alpha=0.4,
                      interpolate=True, label="contango (bleed)")
    ax_c.axhline(0, color="k", lw=0.8)
    ax_c.axhline(-band, color="red", ls="--", lw=0.9,
                 label=f"filter threshold −{band:.1%}")

    # Clip the y-axis to the normal regime — the 2020 negative-oil chaos sends the
    # trailing roll yield to ~-330%, which would otherwise flatten everything else.
    lim = max(float(carry.abs().quantile(0.95)) * 1.5, 0.05)
    if float(carry.abs().max()) > lim:
        ax_c.text(0.99, 0.04, "(2020 spike clipped)", transform=ax_c.transAxes,
                  ha="right", va="bottom", fontsize=7, color="0.4")
    ax_c.set_ylim(-lim, lim)
    ax_c.set_ylabel("Roll yield")
    ax_c.set_xlabel("Date")
    ax_c.legend(loc="upper left", fontsize=8)
    ax_c.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Wrote %s", path)
    return path
