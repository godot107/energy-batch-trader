"""Telegram alerting.

Kept dependency-light (env-driven, no import of the rest of the package) so the
Airflow plugin shim and the core pipeline can both use it without import cycles.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_telegram_alert(message: str) -> bool:
    """Push an instant alert to the configured Telegram chat.

    Used for trade alerts, anomaly halts, and error reporting. Silently no-ops
    when Telegram is not configured so the pipeline stays runnable locally.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram credentials not set; skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🔔 *EnergyTrader Alert*\n\n{message}",
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=8)
        if response.status_code == 200:
            logger.info("Telegram notification delivered.")
            return True
        logger.error(
            "Telegram returned %s: %s", response.status_code, response.text
        )
        return False
    except Exception as exc:  # noqa: BLE001 - alerting must never crash the run
        logger.error("Failed to send Telegram notification: %s", exc)
        return False
