"""Compatibility shim — canonical implementation lives in ``energy_trader.notify``.

Kept so the Airflow ``plugins/`` import path keeps working. New code should
import from :mod:`energy_trader.notify` directly.
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from energy_trader.notify import send_telegram_alert  # noqa: E402,F401
