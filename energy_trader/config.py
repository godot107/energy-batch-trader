"""Centralized settings, sourced from environment / ``.env``.

Reading config in one place keeps the orchestrator-agnostic core portable: the
CLI, an Airflow task, and an Azure Function all get identical behavior from the
same environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Load a local .env when present. In Azure Functions / Airflow the variables are
# supplied by the platform, so a missing python-dotenv is non-fatal.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional dependency
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    """Runtime configuration for one pipeline invocation."""

    # --- Universe ---------------------------------------------------------
    assets: list[str] = field(default_factory=lambda: ["USO", "XLE"])

    # --- Strategy params --------------------------------------------------
    fast_window: int = 10
    slow_window: int = 50
    lookback_days: int = 200

    # --- Execution --------------------------------------------------------
    # Energy EOD orders are queued after the close for the next session, so
    # extended-hours is on by default.
    extended_hours: bool = True
    default_notional: float = 100.0  # dollar size per order

    # Execution target when not in dry-run:
    #   "dry_run" (default) | "alpaca_paper" (Phase 2) | "robinhood" (Phase 3).
    broker: str = field(default_factory=lambda: _env("EOD_BROKER", "dry_run"))

    # --- Robinhood official Agentic Trading MCP (Phase 2) -----------------
    rh_mcp_url: str = field(
        default_factory=lambda: _env(
            "ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading"
        )
    )
    rh_mcp_token: str | None = field(default_factory=lambda: _env("ROBINHOOD_MCP_TOKEN"))

    # --- Alpaca market data ----------------------------------------------
    alpaca_api_key: str | None = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    alpaca_secret_key: str | None = field(
        default_factory=lambda: _env("ALPACA_SECRET_KEY")
    )

    # --- Telegram notifications ------------------------------------------
    telegram_token: str | None = field(
        default_factory=lambda: _env("TELEGRAM_BOT_TOKEN")
    )
    telegram_chat_id: str | None = field(
        default_factory=lambda: _env("TELEGRAM_CHAT_ID")
    )

    # --- EIA Open Data API (fundamental supply signal) -------------------
    eia_api_key: str | None = field(default_factory=lambda: _env("EIA_API_KEY"))
    # Halt when the latest weekly crude-stock change is this many std devs (vs the
    # trailing year) from normal — an outsized inventory surprise.
    eia_stock_z_threshold: float = 2.5

    # --- LLM anomaly detection -------------------------------------------
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))


def get_settings() -> Settings:
    """Build a fresh :class:`Settings` from the current environment."""
    return Settings()
