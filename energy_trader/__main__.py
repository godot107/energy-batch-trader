"""CLI entrypoint: ``python -m energy_trader [--live] [--asset USO ...]``.

Defaults to dry-run. ``--live`` is the only thing that arms real orders, and it
requires the Robinhood MCP token to be configured.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from energy_trader.config import get_settings
from energy_trader.pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="energy_trader", description=__doc__)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Execute on Alpaca PAPER trading (no real money) — Phase 2.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Arm REAL order execution via the Robinhood MCP — Phase 3.",
    )
    parser.add_argument(
        "--asset",
        action="append",
        dest="assets",
        metavar="SYMBOL",
        help="Override the universe (repeatable). Default: USO XLE.",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Backtest the strategy over history instead of running the pipeline.",
    )
    parser.add_argument(
        "--years",
        type=float,
        default=3.0,
        help="Years of history for --backtest (default: 3).",
    )
    parser.add_argument(
        "--strategy",
        choices=["sma", "pairs", "carry", "voltgt"],
        default="sma",
        help="Backtest strategy: 'sma' crossover (default), 'pairs' (USO/XLE "
        "spread mean reversion; needs exactly 2 assets), 'carry' (USO roll-yield "
        "vs SMA vs buy-and-hold; needs USO + EIA_API_KEY), or 'voltgt' (SMA "
        "before/after volatility-targeted sizing).",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save backtest charts (equity, drawdown, carry regime) as PNGs. "
        "Research-only; needs matplotlib.",
    )
    parser.add_argument(
        "--plot-dir",
        default="plots",
        metavar="DIR",
        help="Where --plot writes PNGs (default: ./plots).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Debug logging."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    )

    if args.paper and args.live:
        parser.error("--paper and --live are mutually exclusive.")

    settings = get_settings()
    if args.assets:
        settings.assets = args.assets

    if args.backtest:
        return _run_backtest(settings, args.years, args.strategy,
                             plot=args.plot, plot_dir=args.plot_dir)

    if args.paper:
        settings.broker = "alpaca_paper"
    elif args.live:
        settings.broker = "robinhood"

    result = run_pipeline(dry_run=not (args.paper or args.live), settings=settings)

    mode = "PAPER (alpaca)" if args.paper else "LIVE" if args.live else "DRY-RUN"
    print("\n=== EOD Pipeline Result ===")
    print(f"mode: {mode}")
    print(result.summary())
    return 0


def _run_backtest(settings, years: float, strategy: str, *,
                  plot: bool = False, plot_dir: str = "plots") -> int:
    # Backtests need far more history than the ~200-bar live lookback.
    from energy_trader.data import extract_market_data

    bt_settings = dataclasses.replace(settings, lookback_days=int(years * 252))
    data = extract_market_data(bt_settings)

    # USO roll-decay (contango) diagnostic — honest about USO's drag vs spot oil.
    if "USO" in data and bt_settings.eia_api_key:
        _print_roll_decay(data, bt_settings)

    if strategy == "pairs":
        from energy_trader.pairs import backtest_pair, format_sweep, sweep_pairs

        syms = bt_settings.assets
        if len(syms) < 2:
            print("Pairs backtest needs at least 2 assets "
                  "(e.g. --asset USO --asset XLE).")
            return 2
        if len(syms) == 2:
            a, b = syms
            res = backtest_pair(data[a]["Close"].rename(a),
                                data[b]["Close"].rename(b), bt_settings)
            print("\n=== Pairs Backtest ===")
            print(res.describe() if res else "Insufficient history for the pair.")
        else:
            print("\n=== Pairs Sweep ===")
            print(format_sweep(sweep_pairs(data, bt_settings), bt_settings))
        return 0

    if strategy == "carry":
        from energy_trader.backtest import compare_uso_carry, format_comparison
        from energy_trader.eia import fetch_wti_spot

        if "USO" not in data:
            print("Carry strategy needs USO in the universe (--asset USO).")
            return 2
        wti = fetch_wti_spot(bt_settings, length=bt_settings.lookback_days * 2 + 60)
        if wti.empty:
            print("Carry needs WTI spot from EIA — set EIA_API_KEY (see RUNBOOK).")
            return 2
        results = compare_uso_carry(data["USO"]["Close"], wti, bt_settings)
        title = (
            f"USO: SMA baseline vs carry-filtered SMA vs Buy-and-Hold  "
            f"(carry {bt_settings.carry_window}d/band {bt_settings.carry_band:.1%}, "
            f"long-only, fee {0.0005:.2%}/trade)"
        )
        print("\n=== Carry-filter Backtest ===")
        print(format_comparison(results, title))
        if plot:
            from pathlib import Path

            from energy_trader.plots import (
                equity_curves,
                plot_carry_regime,
                plot_equity_drawdown,
            )

            out = Path(plot_dir)
            curves = equity_curves(results, bench_close=data["USO"]["Close"])
            p1 = plot_equity_drawdown(
                curves, path=out / "carry_equity_drawdown.png",
                title="USO — SMA vs SMA+carry vs Buy-and-Hold")
            p2 = plot_carry_regime(
                data["USO"]["Close"], wti, bt_settings.carry_window,
                bt_settings.carry_band, path=out / "carry_regime.png")
            print(f"\nPlots: {p1}  {p2}")
        return 0

    if strategy == "voltgt":
        from energy_trader.backtest import compare_vol_target, format_comparison

        syms = [s for s in bt_settings.assets if s in data]
        for sym in syms:
            results = compare_vol_target(sym, data[sym]["Close"], bt_settings)
            if not results:
                continue
            title = (
                f"{sym}: SMA vs SMA+vol-target  (target "
                f"{bt_settings.vol_target_annual:.0%}, {bt_settings.vol_window}d, "
                f"cap {bt_settings.vol_max_leverage:.1f}x, fee {0.0005:.2%}/trade)"
            )
            print(f"\n=== Vol-targeting (before/after) — {sym} ===")
            print(format_comparison(results, title))
            if plot:
                from pathlib import Path

                from energy_trader.plots import (
                    equity_curves,
                    plot_equity_drawdown,
                    plot_vol_target,
                )

                out = Path(plot_dir)
                curves = equity_curves(results, bench_close=data[sym]["Close"])
                p1 = plot_equity_drawdown(
                    curves, path=out / f"voltgt_{sym}.png",
                    title=f"{sym} — SMA vs SMA+vol-target vs Buy-and-Hold")
                p2 = plot_vol_target(data[sym]["Close"], bt_settings,
                                     path=out / f"voltgt_{sym}_mechanism.png")
                print(f"Plots: {p1}  {p2}")
        return 0

    from energy_trader.backtest import format_report, run_backtest

    results = run_backtest(data, bt_settings)
    print("\n=== Backtest Result ===")
    print(format_report(results, bt_settings))
    if plot:
        from pathlib import Path

        from energy_trader.plots import equity_curves, plot_equity_drawdown

        out = Path(plot_dir)
        for sym, res in results.items():
            curves = equity_curves([res], bench_close=data[sym]["Close"])
            p = plot_equity_drawdown(
                curves, path=out / f"sma_{sym}.png",
                title=f"{sym} — SMA({bt_settings.fast_window}/"
                f"{bt_settings.slow_window}) vs Buy-and-Hold")
            print(f"Plot: {p}")
    return 0


def _print_roll_decay(data, settings) -> None:
    from energy_trader.eia import fetch_wti_spot
    from energy_trader.roll import roll_decay

    wti = fetch_wti_spot(settings, length=settings.lookback_days * 2 + 60)
    if wti.empty:
        return
    rd = roll_decay(data["USO"]["Close"], wti)
    if rd is not None:
        print("\n=== USO roll-decay (contango) ===")
        print(rd.describe())


if __name__ == "__main__":
    sys.exit(main())
