"""USO roll-decay / contango diagnostic.

USO holds front-month WTI crude futures and rolls them monthly, so in *contango*
(front cheaper than the next month) it bleeds value versus physical oil on every
roll; in *backwardation* it earns roll yield. This measures that drag empirically
— USO's return minus WTI spot's over the same window — so the backtest's USO
numbers are honest about it. No CME contract specs needed: the gap *is* the roll
(plus fee) drag. WTI spot comes from EIA (see :func:`energy_trader.eia.fetch_wti_spot`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


@dataclass
class RollDecay:
    days: int
    uso_total: float
    wti_total: float
    uso_cagr: float
    wti_cagr: float
    annual_decay: float  # uso_cagr - wti_cagr; negative ⇒ USO lagged spot (contango)

    def describe(self) -> str:
        regime = "contango drag" if self.annual_decay < 0 else "roll yield"
        return (
            f"USO {self.uso_total:+.1%} (CAGR {self.uso_cagr:+.1%}) vs "
            f"WTI spot {self.wti_total:+.1%} (CAGR {self.wti_cagr:+.1%}) "
            f"over {self.days} sessions  →  {self.annual_decay:+.1%}/yr {regime}"
        )


def _to_naive_dates(s: pd.Series) -> pd.Series:
    """Reindex a price series to tz-naive midnight dates for clean alignment."""
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out = s.copy()
    out.index = idx.normalize()
    return out


def roll_decay(uso_close: pd.Series, wti_spot: pd.Series) -> RollDecay | None:
    """Compare USO total return to WTI spot over their overlapping dates."""
    df = pd.concat(
        [
            _to_naive_dates(uso_close).rename("uso"),
            _to_naive_dates(wti_spot).rename("wti"),
        ],
        axis=1,
    ).dropna()
    if len(df) < 30:
        return None

    n = len(df)
    uso_total = float(df["uso"].iloc[-1] / df["uso"].iloc[0] - 1.0)
    wti_total = float(df["wti"].iloc[-1] / df["wti"].iloc[0] - 1.0)
    uso_cagr = float((1.0 + uso_total) ** (TRADING_DAYS / n) - 1.0)
    wti_cagr = float((1.0 + wti_total) ** (TRADING_DAYS / n) - 1.0)
    return RollDecay(n, uso_total, wti_total, uso_cagr, wti_cagr, uso_cagr - wti_cagr)
