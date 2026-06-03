"""Public data contracts shared across the engine.

These types are the stable interface between ``features.py`` (what the market
looks like at a closed bar), ``rules.py`` / ``signal.py`` (the decision), and
``backtest.py`` / ``live.py`` (what consumes the decision). Keep them small and
serializable so signals can be logged to JSON for the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd

from .config import Direction


@dataclass
class Level:
    """One support/resistance level (LOGIC.md §4.1)."""

    name: str            # "S2" | "S1" | "R1" | "R2"
    price: float
    kind: str            # "sup" | "res"
    touches: int = 0     # how many touches formed it


@dataclass
class FeatureSnapshot:
    """Everything measurable about one *closed* primary (M30) bar.

    Built only from data closed at or before ``bar_time`` (CLAUDE.md hard rule
    #1). Rules consume this; they must not re-read raw frames for future bars.
    """

    bar_index: int
    bar_time: pd.Timestamp

    # OHLCV of the signalling (closed) bar
    open: float
    high: float
    low: float
    close: float
    volume: float

    # previous closed bar (for engulfing / close-back-in / change)
    prev_open: float
    prev_high: float
    prev_low: float
    prev_close: float

    # session context (LOGIC.md §7)
    session: str = "OFF"
    qt_phase: str = "Q1"
    in_kill_zone: bool = False

    # volume gate (LOGIC.md §8.1)
    volume_ma: float = 0.0
    volume_ok: bool = False
    volume_source: str = "missing"   # provider | range_proxy | missing

    # candle shape (LOGIC.md §8.2)
    bull_close: bool = False
    bear_close: bool = False
    body: float = 0.0
    upper_wick: float = 0.0
    lower_wick: float = 0.0
    strong_body: bool = False
    rejection_up: bool = False
    rejection_down: bool = False
    bull_engulfing: bool = False
    bear_engulfing: bool = False

    # levels (LOGIC.md §4.1)
    levels: list[Level] = field(default_factory=list)
    near_resistance: Optional[Level] = None
    near_support: Optional[Level] = None
    nearest_opposing_dist: Optional[float] = None  # to a LONG, the room above; etc.

    # dealing range (LOGIC.md §10)
    drh: Optional[float] = None
    drl: Optional[float] = None
    dr_mid: Optional[float] = None
    zone: str = "MID"                # PREMIUM | DISCOUNT | MID

    # structure (LOGIC.md §3, §15)
    htf_bias: str = "NEUTRAL"        # consensus(H4, D1): BULL | BEAR | NEUTRAL
    d1_bias: str = "NEUTRAL"         # raw D1 structural trend (audit log)
    m30_trend: str = "NEUTRAL"
    bos: bool = False
    choch: bool = False

    # liquidity sweeps (LOGIC.md §6.2)
    swept_bsl: bool = False          # swept buy-side liq (above R) then closed back below
    swept_ssl: bool = False          # swept sell-side liq (below S) then closed back above

    # exhaustion (LOGIC.md §12)
    consecutive_motion: int = 0
    motion_dir: str = "FLAT"

    def consecutive_exhausted(self, limit: int) -> bool:
        return self.consecutive_motion >= limit + 1


@dataclass
class SignalState:
    """The engine's per-bar verdict. ``reasons`` is mandatory and must name the
    rules that fired so the audit log is human-readable (CLAUDE.md contract)."""

    direction: Direction
    entry: Optional[float]
    stop: Optional[float]
    take_profit: Optional[float]
    confidence: float
    reasons: list[str]
    bar_time: pd.Timestamp

    def is_trade(self) -> bool:
        return self.direction in ("LONG", "SHORT")

    def to_dict(self) -> dict:
        d = asdict(self)
        # make JSON-friendly
        d["bar_time"] = (
            self.bar_time.isoformat() if isinstance(self.bar_time, pd.Timestamp)
            else str(self.bar_time)
        )
        return d


def flat(bar_time: pd.Timestamp, reasons: list[str], confidence: float = 0.0) -> SignalState:
    """Convenience constructor for the most common (correct) output."""
    return SignalState(
        direction="FLAT",
        entry=None,
        stop=None,
        take_profit=None,
        confidence=confidence,
        reasons=list(reasons),
        bar_time=bar_time,
    )
