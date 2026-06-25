"""Market-data extraction (Alpaca), with a synthetic fallback.

If ``alpaca-py`` or API keys are unavailable, we return deterministic-ish mock
bars so the full pipeline still runs end-to-end on a fresh checkout.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from energy_trader.config import Settings

logger = logging.getLogger(__name__)


def _mock_bars(symbol: str, periods: int) -> pd.DataFrame:
    """Synthetic daily closes — a gentle uptrend plus noise."""
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=periods)
    drift = np.linspace(70, 85, periods)
    close = drift + rng.normal(0, 2, periods)
    return pd.DataFrame({"Close": close}, index=dates)


def extract_market_data(settings: Settings) -> dict[str, pd.DataFrame]:
    """Return ``{symbol: DataFrame[Close]}`` for the configured universe."""
    use_alpaca = bool(settings.alpaca_api_key and settings.alpaca_secret_key)

    if not use_alpaca:
        logger.warning("Alpaca keys not set — using synthetic market data.")
        return {s: _mock_bars(s, settings.lookback_days) for s in settings.assets}

    try:
        from alpaca.data.enums import Adjustment
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(
            settings.alpaca_api_key, settings.alpaca_secret_key
        )
        start = pd.Timestamp.now().normalize() - pd.Timedelta(
            days=settings.lookback_days * 2  # weekends/holidays padding
        )
        request = StockBarsRequest(
            symbol_or_symbols=settings.assets,
            timeframe=TimeFrame.Day,
            start=start,
            # Split+dividend adjusted: without this, raw bars show USO's 2020
            # 1-for-8 reverse split as a fake +745% day that corrupts every
            # backtest, the roll-decay diagnostic, and the carry signal.
            adjustment=Adjustment.ALL,
        )
        bars = client.get_stock_bars(request).df
        out: dict[str, pd.DataFrame] = {}
        for symbol in settings.assets:
            sym_df = bars.loc[symbol] if symbol in bars.index.get_level_values(0) else None
            if sym_df is None or sym_df.empty:
                logger.warning("No Alpaca data for %s; using mock.", symbol)
                out[symbol] = _mock_bars(symbol, settings.lookback_days)
            else:
                out[symbol] = sym_df.rename(columns={"close": "Close"})[["Close"]]
        logger.info("Fetched Alpaca bars for %s", ", ".join(settings.assets))
        return out
    except Exception as exc:  # noqa: BLE001 - fall back rather than crash the run
        logger.error("Alpaca fetch failed (%s); falling back to synthetic data.", exc)
        return {s: _mock_bars(s, settings.lookback_days) for s in settings.assets}
