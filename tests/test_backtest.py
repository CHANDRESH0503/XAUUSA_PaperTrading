"""Backtest lifecycle, cost application, metrics honesty (PLAN.md test plan)."""

import pandas as pd
import pytest

from src.config import DEFAULT_CONFIG as CFG
from src.backtest import (run, compute_metrics, flat_frequency, insample_oos,
                          sensitivity, _apply_entry_cost, _r_after_costs)


def test_entry_cost_is_adverse():
    long_fill = _apply_entry_cost("LONG", 2000.0, CFG)
    short_fill = _apply_entry_cost("SHORT", 2000.0, CFG)
    assert long_fill > 2000.0      # buy fills higher
    assert short_fill < 2000.0     # sell fills lower
    assert long_fill - 2000.0 == pytest.approx(CFG.spread_price + CFG.slippage_price)


def test_r_after_costs_penalises():
    # LONG, entry 2000, stop 1990 (risk 10), exit at tp 2020 -> gross 2R minus costs
    r = _r_after_costs("LONG", entry=2000, exit_price=2020, stop=1990, cfg=CFG)
    assert r < 2.0          # slippage on exit reduces it
    assert r > 1.5


def test_backtest_runs_and_metrics_consistent(frames):
    trades = run(frames, CFG)
    m = compute_metrics(trades)
    assert "trade_count" in m
    if m["trade_count"] > 0:
        # every trade has a valid R and a known outcome
        for t in trades:
            assert t.outcome in ("tp", "stop", "timeout")
            assert t.stop != t.entry
        # win_rate within [0,1]
        assert 0.0 <= m["win_rate"] <= 1.0
        assert m["longest_losing_streak"] >= 0


def test_flat_is_frequent(frames):
    ff = flat_frequency(frames, CFG)
    # FLAT must dominate per LOGIC.md; sanity floor well below 1.0 of action
    assert ff["flat_pct"] >= 0.5


def test_no_overlapping_positions(frames):
    trades = run(frames, CFG)
    # entries are strictly ordered and a new trade starts only after the prior
    # one exits (single-position MVP)
    last_exit = None
    for t in trades:
        et = pd.Timestamp(t.entry_time)
        if last_exit is not None:
            assert et >= last_exit
        last_exit = pd.Timestamp(t.exit_time)


def test_sensitivity_and_oos_run(frames):
    s = sensitivity(frames, CFG)
    assert {row["variant"] for row in s} == {"baseline", "-10%", "+10%"}
    io = insample_oos(frames, CFG)
    assert "in_sample" in io and "out_of_sample" in io
