"""Provider interface + normalized schema (PLAN.md 'Implementation additions').

A provider's only job is to return canonical OHLCV frames (see ``data.py``):
UTC tz-aware index of candle open times, columns ``[open, high, low, close,
volume]``. The engine never talks to a provider directly during evaluation —
providers feed ``data.py`` / ``live.py`` only.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    name: str

    def time_series(self, symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
        """Return a canonical OHLCV frame for ``symbol`` at ``interval``."""
        ...

    def latest_price(self, symbol: str) -> float | None:
        """Return the current price, or None if unsupported/unavailable."""
        ...
