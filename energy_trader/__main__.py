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
        "--live",
        action="store_true",
        help="Arm real order execution via the Robinhood MCP (default: dry-run).",
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
        "-v", "--verbose", action="store_true", help="Debug logging."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    )

    settings = get_settings()
    if args.assets:
        settings.assets = args.assets

    if args.backtest:
        return _run_backtest(settings, args.years)

    result = run_pipeline(dry_run=not args.live, settings=settings)

    print("\n=== EOD Pipeline Result ===")
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(result.summary())
    return 0


def _run_backtest(settings, years: float) -> int:
    # Backtests need far more history than the ~200-bar live lookback.
    from energy_trader.backtest import format_report, run_backtest
    from energy_trader.data import extract_market_data

    bt_settings = dataclasses.replace(settings, lookback_days=int(years * 252))
    data = extract_market_data(bt_settings)
    results = run_backtest(data, bt_settings)

    print("\n=== Backtest Result ===")
    print(format_report(results, bt_settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
