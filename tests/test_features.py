"""Unit tests for measurable features (hand-built fixtures)."""

import pandas as pd

from src.config import DEFAULT_CONFIG as CFG
from src.features import (confirmed_pivots, trend_from_swings, build_levels,
                          dealing_range, zone_of, volume_gate, candle_shape,
                          engulfing, consecutive_motion, detect_sweeps)
from tests.conftest import make_frame


def test_confirmed_pivots_finds_central_extreme():
    # a clear swing high at index 3, swing low at index 7
    highs = [10, 11, 12, 20, 12, 11, 10, 9, 10, 11, 12]
    rows = [(h - 1, h, h - 2, h - 0.5, 100) for h in highs]
    frame = make_frame(rows)
    sh, sl = confirmed_pivots(frame, p=2)
    assert any(i == 3 for i, _ in sh)
    # the last p bars cannot be confirmed
    assert all(i <= len(frame) - 1 - 2 for i, _ in sh)


def test_trend_classification():
    assert trend_from_swings([(0, 10), (1, 12)], [(0, 5), (1, 7)]) == "BULL"
    assert trend_from_swings([(0, 12), (1, 10)], [(0, 7), (1, 5)]) == "BEAR"
    assert trend_from_swings([(0, 12), (1, 12)], [(0, 7), (1, 8)]) == "NEUTRAL"


def _double_top_bottom_h1():
    """H1 with resistance ~2000 (two highs) and support ~1980 (two lows)."""
    seq = [
        (1985, 1990, 1982, 1988, 100),
        (1988, 1995, 1986, 1992, 100),
        (1992, 2000, 1990, 1996, 100),   # high touches 2000
        (1996, 1998, 1990, 1992, 100),
        (1992, 1995, 1986, 1988, 100),
        (1988, 1992, 1980, 1983, 100),   # low touches 1980
        (1983, 1990, 1981, 1988, 100),
        (1988, 1999, 1985, 1996, 100),
        (1996, 2001, 1992, 1995, 100),   # high ~2000 again
        (1995, 1998, 1988, 1990, 100),
        (1990, 1994, 1981, 1984, 100),   # low ~1980 again
        (1984, 1992, 1982, 1990, 100),
        (1990, 1996, 1988, 1993, 100),
    ]
    return make_frame(seq, start="2024-01-02 00:00", timeframe="H1")


def test_build_levels_finds_support_and_resistance():
    h1 = _double_top_bottom_h1()
    price = 1993.0
    levels = build_levels(h1, price, CFG)
    names = {l.name for l in levels}
    assert "R1" in names or "S1" in names  # at least one valid clustered level
    for l in levels:
        if l.kind == "res":
            assert l.price > price
        else:
            assert l.price < price


def test_dealing_range_and_zone():
    h1 = _double_top_bottom_h1()
    drh, drl, mid = dealing_range(h1, CFG)
    assert drh > drl
    assert zone_of(drh - 0.01, drh, drl, CFG) == "PREMIUM"
    assert zone_of(drl + 0.01, drh, drl, CFG) == "DISCOUNT"
    assert zone_of(mid, drh, drl, CFG) == "MID"


def test_volume_gate_provider_and_proxy():
    rows = [(1, 2, 0.5, 1.5, 100)] * 20 + [(1, 2, 0.5, 1.5, 500)]
    frame = make_frame(rows)
    ok, ma, src = volume_gate(frame, CFG)
    assert ok and src == "provider"

    # NaN volume -> range proxy on a wide last bar
    import numpy as np
    rows2 = [(1, 1.2, 0.8, 1.0, np.nan)] * 20 + [(1, 5, 0.5, 4, np.nan)]
    frame2 = make_frame(rows2)
    ok2, ma2, src2 = volume_gate(frame2, CFG)
    assert src2 == "range_proxy" and ok2


def test_candle_shape_rejection_up():
    s = candle_shape(o=100, h=110, l=99, c=100.5, cfg=CFG)
    assert s["rejection_up"] and not s["strong_body"]


def test_engulfing():
    prev = pd.Series({"open": 10, "high": 11, "low": 8, "close": 9})   # bearish
    cur = pd.Series({"open": 8.5, "high": 12, "low": 8, "close": 11})  # bullish engulf
    bull, bear = engulfing(cur, prev)
    assert bull and not bear


def test_consecutive_motion_counts_run():
    rows = [(1, 2, 0.5, 1.5, 1)] * 3 + [(2, 3, 1.5, 2.5, 1), (2.5, 3.5, 2, 3, 1)]
    frame = make_frame(rows)
    n, d = consecutive_motion(frame)
    assert d == "BULL" and n == 5


def test_detect_bsl_sweep():
    # last bar pierces above R1=100 then closes back below, bearish
    rows = [(98, 99, 97, 98, 1), (99, 101.5, 98.5, 99.0, 1)]
    frame = make_frame(rows)
    bsl, ssl = detect_sweeps(frame, r1=100.0, s1=90.0, cfg=CFG)
    assert bsl and not ssl
