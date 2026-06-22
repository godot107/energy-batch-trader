"""Anomaly detection — the risk gate that can *halt* mechanical trading.

This aggregates one or more risk signals into a single halt decision. Trade
decisions themselves stay deterministic (see :mod:`energy_trader.strategy`); this
gate only ever *stops* a run, it never sizes or directs a trade.

Signals:
  - **Fundamental (live):** EIA Weekly Petroleum Status Report inventory surprise
    (:mod:`energy_trader.eia`) — deterministic, no LLM.
  - **Geopolitical (TODO next):** LLM web-search research and/or prediction-market
    probabilities for war/OPEC/sanction shocks. This is the only place an LLM is
    permitted to run, and only as a risk gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from energy_trader.config import Settings
from energy_trader.eia import check_inventory_shock

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    detected: bool
    reason: str


def detect_anomalies(settings: Settings) -> AnomalyResult:
    """Aggregate risk signals into a halt decision (``detected=True`` ⇒ halt)."""
    reasons: list[str] = []

    # Fundamental supply shock — EIA crude inventory surprise (deterministic).
    eia = check_inventory_shock(settings)
    if eia.detected:
        reasons.append(eia.reason)

    # Geopolitical sentiment (LLM research / prediction markets) — not yet wired.
    if settings.openai_api_key or settings.gemini_api_key:
        logger.info("Geopolitical LLM scan not yet wired; skipping for now.")

    if reasons:
        return AnomalyResult(detected=True, reason=" | ".join(reasons))
    return AnomalyResult(detected=False, reason="no anomalies detected")
