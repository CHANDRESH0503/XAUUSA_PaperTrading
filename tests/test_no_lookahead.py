"""CLAUDE.md hard rule #1: a signal for bar t may not use any data that closes
after t. We verify this by mutating every 'future' bar and asserting the signal
for bar t is byte-for-byte identical."""

import copy

import pandas as pd

from src.config import DEFAULT_CONFIG as CFG
from src.signal import evaluate
from src.data import timeframe_delta


def _perturb_future(frames, bar_close):
    """Return a copy of frames with all bars that close AFTER ``bar_close``
    replaced by absurd values (and future M30 bars too)."""
    out = {}
    for tf, df in frames.items():
        df2 = df.copy()
        close_times = df2.index + timeframe_delta(tf)
        future = close_times > bar_close
        # also treat the primary frame's bars strictly after the signal bar
        df2.loc[future, ["open", "high", "low", "close"]] = 99999.0
        df2.loc[future, "volume"] = 1.0
        out[tf] = df2
    return out


def test_future_bars_do_not_change_signal(frames):
    pf = CFG.primary_frame
    n = len(frames[pf])
    # sample several bars across the series
    for i in range(n // 2, n - 5, max(1, (n // 2) // 20)):
        bar_time = frames[pf].index[i]
        bar_close = bar_time + timeframe_delta(pf)
        base = evaluate(i, frames, CFG)
        mutated = _perturb_future(frames, bar_close)
        after = evaluate(i, mutated, CFG)
        assert base.direction == after.direction, f"dir changed at bar {i}"
        assert base.entry == after.entry
        assert base.stop == after.stop
        assert base.take_profit == after.take_profit
        assert base.reasons == after.reasons


def test_truncation_equivalence(frames):
    """Evaluating bar t on the full frames must equal evaluating it on frames
    truncated to only bars closed by t (the visible set)."""
    pf = CFG.primary_frame
    n = len(frames[pf])
    i = int(n * 0.8)
    bar_close = frames[pf].index[i] + timeframe_delta(pf)
    full = evaluate(i, frames, CFG)

    truncated = {}
    for tf, df in frames.items():
        close_times = df.index + timeframe_delta(tf)
        truncated[tf] = df.loc[close_times <= bar_close]
    # the signal bar must remain the last bar of the truncated primary frame
    j = len(truncated[pf]) - 1
    trunc_sig = evaluate(j, truncated, CFG)
    assert full.direction == trunc_sig.direction
    assert full.reasons == trunc_sig.reasons
    assert full.entry == trunc_sig.entry
