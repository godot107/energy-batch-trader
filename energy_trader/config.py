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

    # --- Live strategy ----------------------------------------------------
    #   "allocation" (default): fixed target weights, quarterly rebalance,
    #       monthly deposits deployed buy-only (allocation.py).
    #   "trend": SMA-crossover in/out + vol-targeted sizing (rebalance.py).
    strategy: str = field(default_factory=lambda: _env("EOD_STRATEGY", "allocation"))

    # --- Strategic allocation (allocation.py) -----------------------------
    # Target weight per symbol; the remainder is held as cash (here 20%).
    target_weights: dict[str, float] = field(
        default_factory=lambda: {"XLE": 0.50, "USO": 0.30}
    )
    rebalance_window_days: int = 7  # rebalance runs in days 1–7 of Jan/Apr/Jul/Oct
    alloc_tolerance: float = 0.03  # …only if some weight is > 3pp off target
    deploy_min_cash: float = 20.0  # invest excess cash once it's at least this

    # --- Strategy params --------------------------------------------------
    fast_window: int = 5
    slow_window: int = 20
    lookback_days: int = 200

    # --- Pairs trading (USO/XLE spread mean reversion) -------------------
    pairs_lookback: int = 60  # rolling window for hedge ratio + z-score
    pairs_entry_z: float = 2.0
    pairs_exit_z: float = 0.5
    pairs_stop_z: float = 3.5
    pairs_coint_max: float = 0.05  # only trade pairs with coint p-value <= this

    # --- Carry / roll-yield signal (USO term structure) ------------------
    # Realized roll yield = trailing USO return − trailing WTI-spot return over
    # `carry_window` aligned bars (positive ⇒ backwardation ⇒ go long USO).
    carry_window: int = 63  # ~3 months; roll regimes are slow-moving
    carry_band: float = 0.005  # dead-band on the return diff to damp whipsaw

    # --- Volatility-targeted position sizing (Kaufman ch.23) -------------
    # Scale the position to a constant risk: weight = target / realized vol,
    # capped at vol_max_leverage. Shrinks exposure into shocks (the −48% DD fix).
    vol_target_annual: float = 0.20  # target annualized volatility
    vol_window: int = 20  # lookback (days) for realized vol
    vol_max_leverage: float = 1.0  # cap (1.0 = long-only cash, no margin)
    # Live: scale each target position by the vol-target weight.
    vol_target_live: bool = True

    # --- Daily rebalancing (position-aware brokers; see rebalance.py) ------
    # Each day the held position is moved toward its target (in-trend ×
    # risk_fraction × equity × vol weight), but only when it has drifted more
    # than `rebalance_band` of the target — a no-trade band so small daily vol
    # wiggles don't churn fees. Trades under `rebalance_min_trade` $ are skipped.
    rebalance_band: float = 0.20
    rebalance_min_trade: float = 5.0

    # --- Execution / position sizing -------------------------------------
    # Energy EOD orders are queued after the close for the next session, so
    # extended-hours is on by default.
    extended_hours: bool = True
    # Sizing ladder (Kaufman, Trading Systems and Methods, ch.23): a position's
    # base $ is `risk_fraction × account_equity` (fixed-fractional / percent-of-
    # equity) when the broker reports equity, else `default_notional` (dry-run /
    # equity unknown). That base is then volatility-scaled (see vol_* below).
    risk_fraction: float = 0.10  # fraction of account equity per position
    default_notional: float = 100.0  # fallback $ when equity is unknown

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
