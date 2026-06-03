"""Hand-built fixtures: known candle series -> known signal (PLAN.md test plan).

These craft an H1 with resistance ~2000 / support ~1980 and drive the signal
bar on M30. They cover rejection, sweep, and forced FLAT. Breakout / pullback
families are additionally exercised by the integration tests.
"""

import pandas as pd

from src.config import DEFAULT_CONFIG as CFG
from src.features import consensus_bias
from src.signal import evaluate
from tests.conftest import make_frame


def _h1_with_levels():
    seq = [
        (1985, 1990, 1982, 1988, 100),
        (1988, 1995, 1986, 1992, 100),
        (1992, 2000, 1990, 1996, 100),   # high ~2000 (R)
        (1996, 1998, 1990, 1992, 100),
        (1992, 1995, 1986, 1988, 100),
        (1988, 1992, 1980, 1983, 100),   # low ~1980 (S)
        (1983, 1990, 1981, 1988, 100),
        (1988, 1999, 1985, 1996, 100),
        (1996, 2001, 1992, 1995, 100),   # high ~2000 again
        (1995, 1998, 1988, 1990, 100),
        (1990, 1994, 1981, 1984, 100),   # low ~1980 again
        (1984, 1992, 1982, 1990, 100),
        (1990, 1996, 1988, 1993, 100),
    ]
    return make_frame(seq, start="2024-01-02 00:00", timeframe="H1")


def _m30_with_signal(last_bar, n_filler=21):
    """21 doji fillers (vol 100) + one signalling bar, ending 2024-01-02 09:00."""
    filler = [(1993, 1993.2, 1992.8, 1993, 100)] * n_filler
    rows = filler + [last_bar]
    # start so the last bar lands at 09:00 UTC (London window)
    return make_frame(rows, start="2024-01-01 22:30", timeframe="M30")


def _m30_with_ssl_sweep(signal_close, n_filler=19):
    """19 doji fillers + a wick-below-S1 bar + signal bar; signal at 09:30 UTC.

    The penultimate bar has low=1977 (below S1≈1980) which triggers the SSL
    sweep detection.  The signal bar uses a *small body* (open = close − 2) so
    the stop placement (low − body×0.2) produces ≥ 1.5 R to target — ensuring
    the zone gate, not the RR filter, is what controls the outcome.
    """
    filler     = [(1993, 1993.2, 1992.8, 1993, 100)] * n_filler
    sweep_bar  = (1983, 1984, 1977, 1982, 100)           # low pierces S1≈1980
    o = signal_close - 2.0                               # small bull body
    signal_bar = (o, signal_close + 1.0, o - 0.5, signal_close, 100)
    rows = filler + [sweep_bar, signal_bar]
    return make_frame(rows, start="2024-01-01 22:30", timeframe="M30")


def _m30_with_bsl_sweep(signal_close, n_filler=19):
    """19 doji fillers + a wick-above-R1 bar + signal bar; signal at 09:30 UTC.

    The penultimate bar has high=2003 (above R1≈2000).  The signal bar uses a
    *small body* (open = close + 2) so the stop placement produces ≥ 1.5 R,
    ensuring the zone gate controls the outcome and not the RR filter.
    """
    filler     = [(1993, 1993.2, 1992.8, 1993, 100)] * n_filler
    sweep_bar  = (1998, 2003, 1997, 2001, 100)           # high pierces R1≈2000
    o = signal_close + 2.0                               # small bear body
    signal_bar = (o, o + 0.5, signal_close - 0.5, signal_close, 100)
    rows = filler + [sweep_bar, signal_bar]
    return make_frame(rows, start="2024-01-01 22:30", timeframe="M30")


def _m30_asia_signal(last_bar, n_filler=21):
    """Same structure as _m30_with_signal but bar lands at 02:00 UTC (Asia)."""
    filler = [(1993, 1993.2, 1992.8, 1993, 100)] * n_filler
    rows = filler + [last_bar]
    return make_frame(rows, start="2024-01-01 15:30", timeframe="M30")


def test_rejection_short_fires_at_resistance():
    # small body, large upper wick, bearish close, low volume, just under R1
    # D1 must be BEAR for rejection_short to fire (LOGIC.md §9.3)
    last = (1998.7, 1999.9, 1998.0, 1998.5, 100)
    frames = {"M30": _m30_with_signal(last), "H1": _h1_with_levels(),
              "D1": _d1_bear_trend()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "SHORT"
    assert "rejection_short" in sig.reasons
    assert sig.stop > sig.entry > sig.take_profit
    assert sig.reasons  # never empty for a directional call


def test_sweep_short_fires_on_bsl_pierce():
    # BSL sweep: zone=PREMIUM, htf=BEAR, d1=BEAR, m30=BEAR, session=NY → must fire
    # (Tests the rule function directly so fixture M30 trend doesn't interfere)
    from src.rules import sweep_short
    snap = _snap(
        open=2000, high=2001, low=1997, close=1999,
        bull_close=False, bear_close=True, body=1, upper_wick=2, lower_wick=2,
        swept_bsl=True, swept_ssl=False,
        zone="PREMIUM", htf_bias="BEAR", d1_bias="BEAR", m30_trend="BEAR",
        session="NY_OPEN",
    )
    result = sweep_short(snap, pd.DataFrame(), CFG)
    assert result is not None, f"sweep_short must fire; got None"
    assert result.direction == "SHORT"
    assert "sweep_short" in result.reasons


def test_flat_between_levels():
    # quiet bar in the middle of the range, not near any level, no sweep
    last = (1995.0, 1995.3, 1994.7, 1995.0, 100)
    frames = {"M30": _m30_with_signal(last), "H1": _h1_with_levels()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "FLAT"
    assert sig.reasons[0].startswith("no_trade_zone")
    assert sig.entry is None and sig.stop is None


def test_exhaustion_forces_flat():
    # 5 consecutive bearish closes ending near S1 (so the between-levels filter
    # is skipped and the exhaustion guard is the one that fires)
    bears = [(1991 - 2 * i, 1991.3 - 2 * i, 1988.7 - 2 * i, 1989 - 2 * i, 100)
             for i in range(5)]
    rows = [(1993, 1993.2, 1992.8, 1993, 100)] * 17 + bears
    frames = {"M30": make_frame(rows, start="2024-01-01 22:30", timeframe="M30"),
              "H1": _h1_with_levels()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "FLAT"
    assert any("exhaustion" in r for r in sig.reasons)


def test_every_directional_signal_is_explained(frames):
    """Across the synthetic dataset, no LONG/SHORT may have empty reasons."""
    from src.signal import evaluate_series
    sigs = evaluate_series(frames, CFG)
    for s in sigs:
        if s.is_trade():
            assert s.reasons, "directional signal with no reasons"
            assert s.entry is not None and s.stop is not None and s.take_profit is not None


# ---------------------------------------------------------------------------
# consensus_bias unit tests (LOGIC.md §15)
# ---------------------------------------------------------------------------

def test_consensus_bias_agreement():
    assert consensus_bias("BULL", "BULL") == "BULL"
    assert consensus_bias("BEAR", "BEAR") == "BEAR"
    assert consensus_bias("NEUTRAL", "NEUTRAL") == "NEUTRAL"


def test_consensus_bias_neutral_defers_to_other():
    assert consensus_bias("NEUTRAL", "BULL") == "BULL"
    assert consensus_bias("NEUTRAL", "BEAR") == "BEAR"
    assert consensus_bias("BULL", "NEUTRAL") == "BULL"
    assert consensus_bias("BEAR", "NEUTRAL") == "BEAR"


def test_consensus_bias_disagreement_yields_neutral():
    # H4 pullback inside D1 uptrend → NEUTRAL (longs not vetoed)
    assert consensus_bias("BEAR", "BULL") == "NEUTRAL"
    # H4 bounce inside D1 downtrend → NEUTRAL (shorts not vetoed)
    assert consensus_bias("BULL", "BEAR") == "NEUTRAL"


_BEAR_ROWS = [
    # warmup
    (5010, 5015, 5005, 5012, 100),
    (5012, 5020, 5008, 5018, 100),
    # SH1 at idx 2, high=5090 — left: 5015, 5020 < 5090 ✓
    (5018, 5090, 5015, 5080, 100),
    (5080, 5082, 5040, 5045, 100),   # right-1 of SH1
    (5045, 5048, 5010, 5015, 100),   # right-2 of SH1 ✓  → SH1 confirmed
    # descend to SL1
    (5015, 5018, 4970, 4975, 100),
    # SL1 at idx 6, low=4930
    (4975, 4980, 4930, 4935, 100),
    (4935, 4950, 4932, 4945, 100),   # right-1 of SL1
    (4945, 4960, 4938, 4955, 100),   # right-2 of SL1 ✓  → SL1 confirmed
    # bounce toward SH2 (must be < SH1=5090 → LH)
    (4955, 4965, 4950, 4960, 100),
    # SH2 at idx 10, high=5040 < 5090 → LH ✓
    (4960, 5040, 4958, 5030, 100),
    (5030, 5032, 4995, 5000, 100),   # right-1 of SH2
    (5000, 5005, 4965, 4970, 100),   # right-2 of SH2 ✓  → SH2 confirmed
    # descend to SL2 (must be < SL1=4930 → LL)
    (4970, 4975, 4910, 4915, 100),
    # SL2 at idx 14, low=4880 < 4930 → LL ✓
    (4915, 4920, 4880, 4885, 100),
    (4885, 4895, 4882, 4890, 100),   # right-1 of SL2
    (4890, 4900, 4885, 4895, 100),   # right-2 of SL2 ✓  → SL2 confirmed
    (4895, 4905, 4890, 4898, 100),   # current bar, near the low
]

_BULL_ROWS = [
    # warmup
    (4800, 4810, 4795, 4805, 100),
    (4805, 4815, 4800, 4810, 100),
    # SL1 at idx 2, low=4790 — left: 4795, 4800 > 4790 ✓
    (4810, 4815, 4790, 4795, 100),
    (4795, 4810, 4793, 4805, 100),   # right-1 of SL1
    (4805, 4840, 4800, 4835, 100),   # right-2 of SL1 ✓  → SL1 confirmed
    # rise to SH1
    (4835, 4850, 4830, 4845, 100),
    # SH1 at idx 6, high=4870
    (4845, 4870, 4840, 4855, 100),
    (4855, 4858, 4830, 4835, 100),   # right-1 of SH1
    (4835, 4840, 4828, 4832, 100),   # right-2 of SH1 ✓  → SH1 confirmed
    # pull back to SL2 (must be > SL1=4790 → HL)
    (4832, 4838, 4820, 4825, 100),
    # SL2 at idx 10, low=4815 > 4790 → HL ✓
    (4825, 4830, 4815, 4820, 100),
    (4820, 4838, 4818, 4835, 100),   # right-1 of SL2
    (4835, 4860, 4832, 4855, 100),   # right-2 of SL2 ✓  → SL2 confirmed
    # rise to SH2 (must be > SH1=4870 → HH)
    (4855, 4875, 4850, 4870, 100),
    # SH2 at idx 14, high=4910 > 4870 → HH ✓
    (4870, 4910, 4865, 4900, 100),
    (4900, 4905, 4880, 4885, 100),   # right-1 of SH2
    (4885, 4895, 4878, 4890, 100),   # right-2 of SH2 ✓  → SH2 confirmed
    (4890, 4900, 4885, 4895, 100),   # current bar, still in uptrend
]


def _h4_bear_trend():
    """H4 frame in a confirmed LH-LL (BEAR) structure.
    Starts 2023-12-20 so all 18 bars close well before the 2024-01-02 signal."""
    return make_frame(_BEAR_ROWS, start="2023-12-20 00:00", timeframe="H4")


def _d1_bull_trend():
    """D1 frame in a confirmed HH-HL (BULL) structure.
    Starts 2023-12-01 so all 18 bars close well before the 2024-01-02 signal."""
    return make_frame(_BULL_ROWS, start="2023-12-01 00:00", timeframe="D1")


def _d1_bear_trend():
    """D1 frame in a confirmed LH-LL (BEAR) structure."""
    return make_frame(_BEAR_ROWS, start="2023-12-01 00:00", timeframe="D1")


def test_long_not_blocked_when_d1_bull_h4_bear():
    """LOGIC.md §15: when D1=BULL but H4=BEAR (pullback in uptrend), consensus
    bias is NEUTRAL so a valid SSL sweep emits LONG, not FLAT via htf_veto."""
    # SSL sweep on M30: wick below S1, close back above S1, no volume
    last = (1982.0, 1983.0, 1978.5, 1982.5, 100)
    frames = {
        "M30": _m30_with_signal(last),
        "H1": _h1_with_levels(),
        "H4": _h4_bear_trend(),
        "D1": _d1_bull_trend(),
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "LONG", (
        f"Expected LONG on D1-BULL/H4-BEAR pullback, got {sig.direction}: {sig.reasons}"
    )
    assert "sweep_long" in sig.reasons


def test_long_still_blocked_when_both_bear():
    """When both D1 and H4 are BEAR, the HTF veto must still block a LONG."""
    last = (1982.0, 1983.0, 1978.5, 1982.5, 100)
    frames = {
        "M30": _m30_with_signal(last),
        "H1": _h1_with_levels(),
        "H4": _h4_bear_trend(),
        "D1": _d1_bear_trend(),
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "LONG", (
        f"LONG should be vetoed when both H4 and D1 are BEAR, got {sig.direction}: {sig.reasons}"
    )


# ---------------------------------------------------------------------------
# Zone gate on sweeps (LOGIC.md §9.4)
# H1 range ≈ 1980–2001 → midpoint ≈ 1990.5
# PREMIUM  = close > 1990.5   DISCOUNT = close < 1990.5
# ---------------------------------------------------------------------------

def test_sweep_long_blocked_in_premium_zone():
    """SSL sweep with price above the dealing-range midpoint → FLAT (zone gate)."""
    # close=1995 > midpoint≈1990.5 → PREMIUM → sweep_long must not fire
    frames = {"M30": _m30_with_ssl_sweep(signal_close=1995), "H1": _h1_with_levels()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "LONG", (
        f"sweep_long must be blocked in PREMIUM zone, got {sig.direction}: {sig.reasons}"
    )


def test_sweep_long_fires_in_discount_zone():
    """SSL sweep with price below the dealing-range midpoint → LONG (zone gate passes)."""
    # close=1985 < midpoint≈1990.5 → DISCOUNT → sweep_long should fire
    frames = {"M30": _m30_with_ssl_sweep(signal_close=1985), "H1": _h1_with_levels()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "LONG", (
        f"sweep_long should fire in DISCOUNT zone, got {sig.direction}: {sig.reasons}"
    )
    assert "sweep_long" in sig.reasons


def test_sweep_short_blocked_in_discount_zone():
    """BSL sweep with price below the dealing-range midpoint → FLAT (zone gate)."""
    # close=1984 < midpoint≈1990.5 → DISCOUNT → sweep_short must not fire
    frames = {"M30": _m30_with_bsl_sweep(signal_close=1984), "H1": _h1_with_levels()}
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "SHORT", (
        f"sweep_short must be blocked in DISCOUNT zone, got {sig.direction}: {sig.reasons}"
    )


def test_sweep_short_fires_in_premium_zone():
    """BSL sweep in PREMIUM zone with full BEAR context → SHORT (zone gate passes)."""
    from src.rules import sweep_short
    snap = _snap(
        open=1995, high=1996, low=1991, close=1993,
        bull_close=False, bear_close=True, body=2, upper_wick=1, lower_wick=2,
        swept_bsl=True, swept_ssl=False,
        zone="PREMIUM", htf_bias="BEAR", d1_bias="BEAR", m30_trend="BEAR",
        session="NY_OPEN",
    )
    result = sweep_short(snap, pd.DataFrame(), CFG)
    assert result is not None, "sweep_short should fire in PREMIUM with BEAR context"
    assert result.direction == "SHORT"
    assert "sweep_short" in result.reasons



# ---------------------------------------------------------------------------
# M30 trend + CHoCH guards on sweeps (LOGIC.md §9.4)
# These tests confirm the *absence* of a signal when a guard fires.
# The positive cases (sweep fires when guard doesn't block) are already
# covered by test_sweep_long_fires_in_discount_zone etc.
# ---------------------------------------------------------------------------

def _snap(**overrides):
    """Minimal FeatureSnapshot for rule unit tests. Override only the fields
    relevant to the gate being tested; everything else defaults to safe values."""
    from src.contracts import FeatureSnapshot, Level
    defaults = dict(
        bar_index=0, bar_time=pd.Timestamp("2024-01-02 09:00", tz="UTC"),
        open=1985, high=1987, low=1982, close=1985, volume=100,
        prev_open=1984, prev_high=1986, prev_low=1983, prev_close=1984,
        session="NY_OPEN", qt_phase="Q3", in_kill_zone=True,
        volume_ok=False, volume_ma=100, volume_source="provider",
        bull_close=True, bear_close=False, body=2, upper_wick=1,
        lower_wick=3, strong_body=False, rejection_up=False, rejection_down=False,
        bull_engulfing=False, bear_engulfing=False,
        levels=[Level("S1", 1980, "sup"), Level("R1", 2000, "res")],
        near_resistance=None, near_support=None, nearest_opposing_dist=None,
        drh=2005, drl=1975, dr_mid=1990, zone="DISCOUNT",
        htf_bias="NEUTRAL", d1_bias="NEUTRAL", m30_trend="BEAR",
        bos=False, choch=False,
        swept_bsl=False, swept_ssl=True,
        consecutive_motion=1, motion_dir="BULL",
    )
    defaults.update(overrides)
    return FeatureSnapshot(**defaults)


def test_sweep_long_blocked_when_m30_bull():
    """SSL sweep in DISCOUNT with m30=BULL → None (rule gate, LOGIC.md §9.4)."""
    from src.rules import sweep_long
    assert sweep_long(_snap(m30_trend="BULL"), pd.DataFrame(), CFG) is None


def test_sweep_long_allowed_when_m30_bear():
    """SSL sweep in DISCOUNT with m30=BEAR → fires (best SMC reversal context)."""
    from src.rules import sweep_long
    result = sweep_long(_snap(m30_trend="BEAR"), pd.DataFrame(), CFG)
    assert result is not None, "sweep_long must fire in DISCOUNT/BEAR m30 context"
    assert result.direction == "LONG"


def test_sweep_long_blocked_when_choch():
    """SSL sweep on the CHoCH bar itself → None (chasing an extended candle, §9.4)."""
    from src.rules import sweep_long
    assert sweep_long(_snap(choch=True, m30_trend="BEAR"), pd.DataFrame(), CFG) is None


def test_sweep_short_blocked_when_m30_neutral():
    """BSL sweep with m30=NEUTRAL → None (no directional follow-through, §9.4)."""
    from src.rules import sweep_short
    snap = _snap(
        bull_close=False, bear_close=True,
        swept_bsl=True, swept_ssl=False,
        zone="PREMIUM", htf_bias="BEAR", d1_bias="BEAR",
        m30_trend="NEUTRAL", session="NY_OPEN",
        open=1998, high=2001, low=1996, close=1997,
    )
    assert sweep_short(snap, pd.DataFrame(), CFG) is None


# ---------------------------------------------------------------------------
# Asia session block (LOGIC.md §12) — sweeps must not fire in Q1
# ---------------------------------------------------------------------------

def test_asia_blocks_ssl_sweep():
    """A valid SSL sweep that lands in the Asia session must return FLAT."""
    # signal bar at 02:00 UTC → Asia session; SSL sweep in discount zone
    last = (1982, 1987, 1981, 1985, 100)   # close=1985 < midpoint → DISCOUNT
    frames = {
        "M30": _m30_asia_signal(last),
        "H1": _h1_with_levels(),
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "FLAT", (
        f"Asia session must force FLAT even on a sweep, got {sig.direction}: {sig.reasons}"
    )
    assert any("asia" in r for r in sig.reasons)


# ---------------------------------------------------------------------------
# D1 alignment for rejection signals (LOGIC.md §9.3)
# ---------------------------------------------------------------------------

def test_rejection_short_requires_d1_bear():
    """rejection_short must be FLAT when D1 is BULL (D1 uptrend)."""
    last = (1998.7, 1999.9, 1998.0, 1998.5, 100)   # rejection candle at R1
    frames = {
        "M30": _m30_with_signal(last),
        "H1":  _h1_with_levels(),
        "D1":  _d1_bull_trend(),          # D1 = BULL → rejection_short blocked
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "SHORT", (
        f"rejection_short must be blocked when D1 is BULL, got {sig.direction}: {sig.reasons}"
    )


def test_rejection_short_fires_with_d1_bear():
    """rejection_short should fire when D1 is BEAR (D1 downtrend aligns)."""
    last = (1998.7, 1999.9, 1998.0, 1998.5, 100)
    frames = {
        "M30": _m30_with_signal(last),
        "H1":  _h1_with_levels(),
        "D1":  _d1_bear_trend(),          # D1 = BEAR → rejection_short allowed
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "SHORT", (
        f"rejection_short should fire with D1 BEAR, got {sig.direction}: {sig.reasons}"
    )
    assert "rejection_short" in sig.reasons


def test_rejection_long_requires_d1_bull():
    """rejection_long must be FLAT when D1 is BEAR (D1 downtrend)."""
    last = (1982.0, 1983.0, 1978.5, 1982.5, 100)   # rejection candle at S1
    frames = {
        "M30": _m30_with_signal(last),
        "H1":  _h1_with_levels(),
        "D1":  _d1_bear_trend(),          # D1 = BEAR → rejection_long blocked
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "LONG", (
        f"rejection_long must be blocked when D1 is BEAR, got {sig.direction}: {sig.reasons}"
    )


def test_rejection_long_fires_with_d1_bull():
    """rejection_long should fire when D1 is BULL (D1 uptrend aligns).

    Bar chosen with low=1980.9 (above S1≈1980.5) so the SSL sweep rule does
    NOT fire, and the tiny body + larger lower wick satisfies rejection_down.
    """
    # open=1981.0 close=1981.2 (0.2 body), high=1984.0, low=1980.6
    # lower_wick=0.4  body=0.2  → rejection_down=0.4>0.3 ✓
    # low=1980.6 > S1=1980 → no SSL sweep; |close−S1|=1.2 < near_band≈1.58 → near_support ✓
    last = (1981.0, 1984.0, 1980.6, 1981.2, 100)
    frames = {
        "M30": _m30_with_signal(last),
        "H1":  _h1_with_levels(),
        "D1":  _d1_bull_trend(),
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction == "LONG", (
        f"rejection_long should fire with D1 BULL, got {sig.direction}: {sig.reasons}"
    )
    assert "rejection_long" in sig.reasons


# ---------------------------------------------------------------------------
# Rejection kill-zone gate (LOGIC.md §9.3)
# ---------------------------------------------------------------------------

def _m30_london_rejection_short():
    """Rejection candle at R1 landing at 09:00 UTC (London) — should fire with D1 BEAR."""
    last = (1998.7, 1999.9, 1998.0, 1998.5, 100)
    return make_frame(
        [(1993, 1993.2, 1992.8, 1993, 100)] * 21 + [last],
        start="2024-01-01 22:30", timeframe="M30",  # lands 09:00 UTC = London
    )


def _m30_nypm_rejection_short():
    """Same rejection candle landing at 17:00 UTC (NY_PM) — must NOT fire."""
    last = (1998.7, 1999.9, 1998.0, 1998.5, 100)
    return make_frame(
        [(1993, 1993.2, 1992.8, 1993, 100)] * 21 + [last],
        start="2024-01-01 04:30", timeframe="M30",  # 04:30+21*30min=15:00→17:00 = NY_PM
    )


def test_rejection_short_blocked_in_nypm():
    """rejection_short must be FLAT in NY_PM even with D1 BEAR."""
    frames = {
        "M30": _m30_nypm_rejection_short(),
        "H1":  _h1_with_levels(),
        "D1":  _d1_bear_trend(),
    }
    sig = evaluate(len(frames["M30"]) - 1, frames, CFG)
    assert sig.direction != "SHORT", (
        f"rejection_short must be blocked in NY_PM, got {sig.direction}: {sig.reasons}"
    )
