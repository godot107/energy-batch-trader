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
        choices=["sma", "pairs"],
        default="sma",
        help="Backtest strategy: 'sma' crossover (default) or 'pairs' "
        "(USO/XLE spread mean reversion; needs exactly 2 assets).",
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
        return _run_backtest(settings, args.years, args.strategy)

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


def _run_backtest(settings, years: float, strategy: str) -> int:
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

    from energy_trader.backtest import format_report, run_backtest

    results = run_backtest(data, bt_settings)
    print("\n=== Backtest Result ===")
    print(format_report(results, bt_settings))
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
