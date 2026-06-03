"""Shared test fixtures and synthetic-data builders.

Two kinds of data here:
- ``make_frame`` builds a tiny hand-specified OHLCV frame (known input ->
  known output) for unit tests of features and rules.
- ``synthetic_frames`` builds a multi-day random-walk dataset resampled into all
  timeframes, for integration tests (no-look-ahead, FLAT frequency, backtest).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import TIMEFRAME_MINUTES, DEFAULT_CONFIG
from src.data import build_frames


def make_frame(rows, start="2024-01-02 08:00", timeframe="M30"):
    """Build a canonical frame from ``rows`` of (open, high, low, close, volume)."""
    minutes = TIMEFRAME_MINUTES[timeframe]
    idx = pd.date_range(start=pd.Timestamp(start, tz="UTC"), periods=len(rows),
                        freq=f"{minutes}min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                      index=idx)
    df.index.name = "timestamp"
    return df


def synthetic_m1(days=20, seed=7, start="2024-01-01"):
    """Deterministic 1-minute random walk during weekday trading hours."""
    rng = np.random.default_rng(seed)
    start_ts = pd.Timestamp(start, tz="UTC")
    minutes = days * 24 * 60
    idx = pd.date_range(start=start_ts, periods=minutes, freq="1min")
    # keep weekdays only (gold trades ~24x5)
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    price = 2000.0
    rows = []
    for i in range(n):
        hour = idx[i].hour
        vol_boost = 1.8 if (7 <= hour < 10 or 12 <= hour < 15) else 1.0
        drift = rng.normal(0, 0.25) * vol_boost
        o = price
        c = o + drift
        h = max(o, c) + abs(rng.normal(0, 0.15)) * vol_boost
        l = min(o, c) - abs(rng.normal(0, 0.15)) * vol_boost
        v = max(1.0, rng.normal(1000, 200) * vol_boost)
        rows.append((o, h, l, c, v))
        price = c
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)
    df.index.name = "timestamp"
    return df


@pytest.fixture(scope="session")
def m1_data():
    return synthetic_m1()


@pytest.fixture(scope="session")
def frames(m1_data):
    needed = [DEFAULT_CONFIG.primary_frame, *DEFAULT_CONFIG.htf_frames,
              *DEFAULT_CONFIG.ltf_frames]
    return build_frames(m1_data, needed, base_timeframe="M1")
