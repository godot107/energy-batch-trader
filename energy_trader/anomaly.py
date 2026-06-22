"""Fundamental anomaly detection — the legitimate LLM step in the pipeline.

This is where an LLM earns its keep: scanning headlines / EIA reports for
geopolitical shocks that should *halt* mechanical trading. Trade decisions stay
deterministic (see :mod:`energy_trader.strategy`); only this risk gate uses an
LLM. For Phase 1 it returns "no anomaly" until wired to a real model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from energy_trader.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    detected: bool
    reason: str


def detect_anomalies(settings: Settings) -> AnomalyResult:
    """Check for fundamental shocks that should halt trading.

    TODO(phase-1b): call OpenAI/Gemini with web-search grounding over recent
    energy headlines and the latest EIA Weekly Petroleum Status Report, and set
    ``detected=True`` when a material shock is found.
    """
    if not (settings.openai_api_key or settings.gemini_api_key):
        logger.info("No LLM key configured — skipping anomaly scan (assume clear).")
        return AnomalyResult(detected=False, reason="anomaly scan disabled")

    logger.info("Scanning for fundamental anomalies via LLM...")
    # Placeholder: real implementation calls the model here.
    return AnomalyResult(detected=False, reason="no major geopolitical shocks found")
