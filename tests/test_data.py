"""Data normalisation, resampling and no-look-ahead slicing."""

import numpy as np
import pandas as pd
import pytest

from src.data import (normalize_ohlcv, resample, build_frames, visible_frame,
                      timeframe_delta)
from tests.conftest import make_frame


def test_normalize_parses_and_sorts_utc():
    raw = pd.DataFrame({
        "datetime": ["2024-01-02 10:00", "2024-01-02 09:00"],
        "open": [1, 2], "high": [3, 4], "low": [0.5, 1.5],
        "close": [2, 3], "volume": [10, 20],
    })
    out = normalize_ohlcv(raw, timestamp_col="datetime")
    assert str(out.index.tz) == "UTC"
    assert out.index.is_monotonic_increasing
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_rejects_bad_ohlc():
    raw = pd.DataFrame({
        "datetime": ["2024-01-02 10:00"],
        "open": [1], "high": [0.5], "low": [2], "close": [1], "volume": [1],
    })
    with pytest.raises(ValueError):
        normalize_ohlcv(raw, timestamp_col="datetime")


def test_resample_aggregates_ohlcv():
    # 4 x M15 -> 1 x H1
    rows = [(10, 12, 9, 11, 100), (11, 13, 10, 12, 100),
            (12, 14, 11, 13, 100), (13, 15, 12, 14, 100)]
    m15 = make_frame(rows, start="2024-01-02 08:00", timeframe="M15")
    h1 = resample(m15, "H1")
    assert len(h1) == 1
    bar = h1.iloc[0]
    assert bar["open"] == 10 and bar["close"] == 14
    assert bar["high"] == 15 and bar["low"] == 9
    assert bar["volume"] == 400


def test_visible_frame_excludes_unclosed_bars():
    m30 = make_frame([(1, 2, 0.5, 1.5, 10)] * 5, start="2024-01-02 08:00")
    # bar opened 09:00 closes 09:30; as-of 09:30 it is visible, 09:29 it is not
    nine30 = pd.Timestamp("2024-01-02 09:30", tz="UTC")
    vis = visible_frame(m30, nine30, "M30")
    assert vis.index[-1] == pd.Timestamp("2024-01-02 09:00", tz="UTC")
    nine29 = pd.Timestamp("2024-01-02 09:29", tz="UTC")
    vis2 = visible_frame(m30, nine29, "M30")
    assert vis2.index[-1] == pd.Timestamp("2024-01-02 08:30", tz="UTC")


def test_build_frames_consistency(m1_data):
    frames = build_frames(m1_data, ["M30", "H1", "H4", "D1"], base_timeframe="M1")
    # H1 bar count should be roughly half of M30 bar count
    assert len(frames["H1"]) <= len(frames["M30"])
    assert (timeframe_delta("H1") == pd.Timedelta(hours=1))
    for tf, df in frames.items():
        assert df.index.is_monotonic_increasing
        assert str(df.index.tz) == "UTC"
