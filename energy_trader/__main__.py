"""CLI entrypoint: ``python -m energy_trader [--live] [--asset USO ...]``.

Defaults to dry-run. ``--live`` is the only thing that arms real orders, and it
requires the Robinhood MCP token to be configured.
"""

from __future__ import annotations

import argparse
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

    result = run_pipeline(dry_run=not args.live, settings=settings)

    print("\n=== EOD Pipeline Result ===")
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
