"""EIA Open Data API v2 — the fundamental supply signal for the anomaly gate.

Pulls the headline Weekly Petroleum Status Report number — U.S. crude oil ending
stocks *excluding* the SPR (series ``WCESTUS1``, thousand barrels) — and flags an
*outsized* week-over-week inventory surprise. An unusually large build or draw is
the kind of shock that whipsaws WTI/USO, so it warrants halting mechanical
trading for the session.

Deterministic by design: the shock test is a z-score of the latest weekly change
against the trailing year. No LLM here — this is fundamentals, not sentiment.

Docs: https://www.eia.gov/opendata/documentation.php
(The API key must be in the URL; EIA ignores it in HTTP headers.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
import requests

from energy_trader.config import Settings

logger = logging.getLogger(__name__)

EIA_V2_BASE = "https://api.eia.gov/v2"
CRUDE_STOCKS_ROUTE = "petroleum/stoc/wstk/data"
CRUDE_STOCKS_SERIES = "WCESTUS1"  # U.S. ending stocks excl. SPR, thousand barrels

WTI_SPOT_ROUTE = "petroleum/pri/spt/data"
WTI_SPOT_SERIES = "RWTC"  # Cushing, OK WTI spot price FOB, $/bbl (daily)


@dataclass
class EIASignal:
    """Outcome of the fundamental inventory check."""

    detected: bool
    reason: str
    latest_value: float | None = None
    weekly_change: float | None = None
    zscore: float | None = None


def fetch_crude_stocks(settings: Settings, weeks: int = 53) -> list[float]:
    """Return weekly crude-stock levels (thousand bbl) in chronological order.

    Pulls the most recent ``weeks`` observations. Raises on transport/HTTP errors
    so the caller can decide how to degrade (see :func:`check_inventory_shock`).
    """
    params = {
        "api_key": settings.eia_api_key,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[series][]": CRUDE_STOCKS_SERIES,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": weeks,
    }
    resp = requests.get(f"{EIA_V2_BASE}/{CRUDE_STOCKS_ROUTE}", params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()["response"]["data"]
    # EIA returns newest-first; reverse to oldest→newest for diffing.
    return [float(r["value"]) for r in reversed(rows) if r.get("value") is not None]


def assess_inventory_shock(values: list[float], threshold: float) -> EIASignal:
    """Flag the latest weekly change as a shock when ``|z| >= threshold``.

    Pure function (no I/O) so the gate logic is unit-testable without an API key.
    ``values`` must be chronological (oldest→newest).
    """
    if len(values) < 10:
        return EIASignal(False, "insufficient EIA history for a baseline")

    diffs = [later - earlier for earlier, later in zip(values, values[1:])]
    latest, prior = diffs[-1], diffs[:-1]
    mean = sum(prior) / len(prior)
    std = (sum((d - mean) ** 2 for d in prior) / (len(prior) - 1)) ** 0.5
    if std == 0:
        return EIASignal(False, "no variance in EIA history")

    z = (latest - mean) / std
    if abs(z) >= threshold:
        direction = "draw" if latest < 0 else "build"
        reason = (
            f"Crude stocks (excl. SPR) WoW {direction} of {latest:+,.0f} kbbl is "
            f"{z:+.1f}σ vs the trailing year — outsized inventory surprise."
        )
        return EIASignal(True, reason, values[-1], latest, z)
    return EIASignal(
        False,
        f"crude-stock WoW change within normal range ({z:+.1f}σ)",
        values[-1],
        latest,
        z,
    )


def check_inventory_shock(settings: Settings) -> EIASignal:
    """Fetch + assess the inventory signal, degrading benignly if unavailable."""
    if not settings.eia_api_key:
        logger.info("EIA_API_KEY not set — skipping fundamental inventory check.")
        return EIASignal(False, "EIA check disabled")
    try:
        values = fetch_crude_stocks(settings)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the daily run
        logger.error("EIA fetch failed (%s); skipping inventory check.", exc)
        return EIASignal(False, "EIA fetch failed")

    signal = assess_inventory_shock(values, settings.eia_stock_z_threshold)
    logger.info("EIA inventory check: %s", signal.reason)
    return signal


def fetch_wti_spot(settings: Settings, length: int = 2000) -> pd.Series:
    """Return daily WTI (Cushing) spot price, chronological.

    Used to measure USO's roll/contango drag against physical oil. Returns an
    empty Series if no key is set or the fetch fails (caller degrades gracefully).
    """
    if not settings.eia_api_key:
        return pd.Series(dtype=float)
    params = {
        "api_key": settings.eia_api_key,
        "frequency": "daily",
        "data[0]": "value",
        "facets[series][]": WTI_SPOT_SERIES,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": length,
    }
    try:
        resp = requests.get(f"{EIA_V2_BASE}/{WTI_SPOT_ROUTE}", params=params, timeout=20)
        resp.raise_for_status()
        rows = [r for r in resp.json()["response"]["data"] if r.get("value") is not None]
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the run
        logger.error("EIA WTI spot fetch failed (%s).", exc)
        return pd.Series(dtype=float)
    rows = list(reversed(rows))  # oldest→newest
    return pd.Series(
        [float(r["value"]) for r in rows],
        index=pd.to_datetime([r["period"] for r in rows]),
        name="wti_spot",
    )
