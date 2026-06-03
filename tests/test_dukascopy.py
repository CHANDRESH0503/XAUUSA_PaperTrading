"""Dukascopy bi5 decoding + tick aggregation (offline, deterministic).

No network: we build a known tick payload, LZMA-compress it the way Dukascopy
serves it, and assert the decoder + OHLCV aggregation reproduce the inputs.
"""

import lzma
import struct
from datetime import datetime, timezone

import pandas as pd

from src.providers.dukascopy import decode_hour, hour_url, _point
from src.data import ohlcv_from_ticks

_REC = struct.Struct(">IIIff")


def _make_bi5(ticks, point):
    """ticks: list of (ms, bid, ask, vol). Returns LZMA-compressed payload."""
    buf = b"".join(
        _REC.pack(ms, round(ask * point), round(bid * point), vol / 2, vol / 2)
        for ms, bid, ask, vol in ticks
    )
    return lzma.compress(buf, format=lzma.FORMAT_ALONE)


def test_point_scaling():
    assert _point("XAUUSD") == 1000.0
    assert _point("XAU/USD".replace("/", "")) == 1000.0


def test_hour_url_month_is_zero_indexed():
    dt = datetime(2024, 3, 5, 9, tzinfo=timezone.utc)  # March -> "02"
    url = hour_url("XAUUSD", dt)
    assert "/XAUUSD/2024/02/05/09h_ticks.bi5" in url


def test_decode_hour_roundtrip():
    hour = datetime(2024, 1, 2, 8, tzinfo=timezone.utc)
    point = _point("XAUUSD")
    ticks = [
        (0,     2050.10, 2050.40, 4.0),
        (1000,  2050.30, 2050.55, 2.0),
        (61000, 2049.90, 2050.20, 6.0),
    ]
    raw = _make_bi5(ticks, point)
    df = decode_hour(raw, hour, point)
    assert len(df) == 3
    # prices recovered within rounding of 1/point
    assert abs(df["bid"].iloc[0] - 2050.10) < 1e-3
    assert abs(df["ask"].iloc[0] - 2050.40) < 1e-3
    assert abs(df["mid"].iloc[0] - 2050.25) < 1e-3
    # timestamps land at the right offsets within the hour
    assert df.index[0] == pd.Timestamp("2024-01-02 08:00:00", tz="UTC")
    assert df.index[2] == pd.Timestamp("2024-01-02 08:01:01", tz="UTC")


def test_empty_payload_decodes_to_empty():
    hour = datetime(2024, 1, 2, 8, tzinfo=timezone.utc)
    assert decode_hour(b"", hour, 1000.0).empty


def test_ohlcv_from_ticks_aggregates():
    hour = datetime(2024, 1, 2, 8, tzinfo=timezone.utc)
    point = _point("XAUUSD")
    # two M30 buckets: bar1 at :00, bar2 at :30
    ticks = [
        (0,        2000.0, 2000.2, 2.0),   # 08:00:00
        (5 * 60_000, 2003.0, 2003.2, 2.0), # 08:05:00 (high)
        (20 * 60_000, 1999.0, 1999.2, 2.0),# 08:20:00 (low) -> bar1 close-ish
        (31 * 60_000, 2001.0, 2001.2, 2.0),# 08:31:00 bar2
    ]
    df = decode_hour(_make_bi5(ticks, point), hour, point)
    ohlcv = ohlcv_from_ticks(df, "M30", price_col="mid", volume_col="volume")
    assert len(ohlcv) == 2
    bar1 = ohlcv.iloc[0]
    assert abs(bar1["open"] - 2000.1) < 1e-2
    assert bar1["high"] >= 2003.0
    assert bar1["low"] <= 1999.2
    assert bar1["volume"] == 6.0   # three ticks * 2.0
