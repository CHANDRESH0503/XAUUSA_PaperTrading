"""The LONG / SHORT / FLAT rules (LOGIC.md §9, §12, §15).

MVP spine only — the measurable, deterministic signals from PLAN.md §3:
breakout, rejection/reversion, fakeout/sweep, pullback-flip continuation, plus
the force-FLAT filters and the HTF veto. OB/FVG/QM/BIGEY live behind config
flags and are *not* implemented here yet.

Each rule is a small pure function: ``(snap, m30_vis, cfg) -> SignalState | None``.
``None`` means "this rule did not fire" (so the engine tries the next one);
the FLAT decisions come from the filters and veto, not the rules.

``reasons`` strings must match the rule names so the audit log is readable.
"""

from __future__ import annotations

import pandas as pd

from .config import EngineConfig, Direction
from .contracts import FeatureSnapshot, SignalState, Level


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _level(snap: FeatureSnapshot, name: str) -> Level | None:
    return next((l for l in snap.levels if l.name == name), None)


def _confidence(snap: FeatureSnapshot, direction: Direction, reasons: list[str]) -> float:
    """Simple MVP confidence (LOGIC.md §13 is the full version, deferred).

    Count a few cheap confluences; map to 0..1. Advisory only — never treated
    as proof of edge until the walk-forward report says so.
    """
    score = 1  # the rule itself fired
    if snap.in_kill_zone:
        score += 1
    if direction == "LONG" and snap.zone == "DISCOUNT":
        score += 1
    if direction == "SHORT" and snap.zone == "PREMIUM":
        score += 1
    if direction == "LONG" and snap.htf_bias == "BULL":
        score += 1
    if direction == "SHORT" and snap.htf_bias == "BEAR":
        score += 1
    if snap.volume_ok:
        score += 1
    return round(min(0.9, 0.3 + 0.1 * score), 3)


def _make(direction: Direction, snap: FeatureSnapshot, stop: float,
          reasons: list[str], cfg: EngineConfig,
          tp_level: float | None = None) -> SignalState | None:
    """Build a directional signal, applying the fixed-R / level take-profit and
    the minimum-RR gate (LOGIC.md §14, §16). Returns ``None`` if RR is too
    small (handled as 'reward too small' → engine stays FLAT)."""
    entry = snap.close
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if direction == "LONG":
        tp_fixed = entry + cfg.target_rr * risk
        tp = min(tp_level, tp_fixed) if tp_level and tp_level > entry else tp_fixed
        reward = tp - entry
    else:
        tp_fixed = entry - cfg.target_rr * risk
        tp = max(tp_level, tp_fixed) if tp_level and tp_level < entry else tp_fixed
        reward = entry - tp
    if reward / risk < cfg.min_rr:
        return None
    if tp_level is not None and ((direction == "LONG" and tp == tp_level) or
                                 (direction == "SHORT" and tp == tp_level)):
        reasons = reasons + ["tp_level"]
    else:
        reasons = reasons + ["tp_fixed_2r"]
    return SignalState(
        direction=direction, entry=round(entry, 2), stop=round(stop, 2),
        take_profit=round(tp, 2), confidence=_confidence(snap, direction, reasons),
        reasons=reasons, bar_time=snap.bar_time,
    )


# --------------------------------------------------------------------------- #
# Force-FLAT filters (LOGIC.md §12)
# --------------------------------------------------------------------------- #
def no_trade_filters(snap: FeatureSnapshot, cfg: EngineConfig) -> list[str] | None:
    """Return reasons if the bar must be FLAT before any rule is tried."""
    r1, s1 = _level(snap, "R1"), _level(snap, "S1")

    # Q1 / Asia: FLAT for all signals — thin volume, stop-runs are noise (LOGIC.md §12)
    # Check before the between_levels filter so Asia always returns the right reason.
    if snap.session == "ASIA":
        return ["no_trade_zone:asia_q1"]

    # no level interaction this bar, price between S1 and R1
    if (r1 and s1 and s1.price < snap.close < r1.price
            and not snap.near_resistance and not snap.near_support
            and not snap.swept_bsl and not snap.swept_ssl):
        return ["no_trade_zone:between_levels"]

    # exhaustion: 4th+ consecutive same-direction candle
    if snap.consecutive_exhausted(cfg.max_consecutive_motion):
        return [f"no_trade_zone:exhaustion_{snap.consecutive_motion}x"]

    # dealing-range midpoint with no confirmation
    if snap.zone == "MID" and not (snap.swept_bsl or snap.swept_ssl):
        return ["no_trade_zone:equilibrium"]

    return None


# --------------------------------------------------------------------------- #
# HTF veto (LOGIC.md §15)
# --------------------------------------------------------------------------- #
def htf_veto(snap: FeatureSnapshot, direction: Direction, stop: float,
             cfg: EngineConfig) -> str | None:
    """Veto a directional signal that fights H4 bias or runs straight into a
    major opposing level within ``2 * stop_distance``."""
    if direction == "LONG" and snap.htf_bias == "BEAR":
        return "htf_veto:against_h4_bias"
    if direction == "SHORT" and snap.htf_bias == "BULL":
        return "htf_veto:against_h4_bias"

    stop_dist = abs(snap.close - stop)
    reach = cfg.htf_veto_stop_multiple * stop_dist
    if direction == "LONG":
        r2 = _level(snap, "R2")
        if r2 and 0 < (r2.price - snap.close) < reach:
            return "htf_veto:resistance_overhead"
    else:
        s2 = _level(snap, "S2")
        if s2 and 0 < (snap.close - s2.price) < reach:
            return "htf_veto:support_below"
    return None


# --------------------------------------------------------------------------- #
# Signals (LOGIC.md §9.1–§9.5)
# --------------------------------------------------------------------------- #
def breakout_long(snap, m30_vis, cfg) -> SignalState | None:  # §9.1
    r1, r2 = _level(snap, "R1"), _level(snap, "R2")
    if not r1 or snap.session not in ("LONDON_OPEN", "NY_OPEN"):
        return None
    if not (snap.prev_close > r1.price and snap.close > r1.price):
        return None
    if not (snap.bull_close and snap.volume_ok and snap.htf_bias != "BEAR"):
        return None
    stop = min(snap.low, r1.price) - snap.body * 0.1
    return _make("LONG", snap, stop, ["breakout_long", "rbs_hold"], cfg,
                 tp_level=r2.price if r2 else None)


def breakout_short(snap, m30_vis, cfg) -> SignalState | None:  # §9.2
    s1, s2 = _level(snap, "S1"), _level(snap, "S2")
    if not s1 or snap.session not in ("LONDON_OPEN", "NY_OPEN"):
        return None
    if not (snap.prev_close < s1.price and snap.close < s1.price):
        return None
    if not (snap.bear_close and snap.volume_ok and snap.htf_bias != "BULL"):
        return None
    stop = max(snap.high, s1.price) + snap.body * 0.1
    return _make("SHORT", snap, stop, ["breakout_short", "sbr_hold"], cfg,
                 tp_level=s2.price if s2 else None)


def rejection_short(snap, m30_vis, cfg) -> SignalState | None:  # §9.3
    r1, s1 = _level(snap, "R1"), _level(snap, "S1")
    if not r1 or not snap.near_resistance or snap.zone != "PREMIUM":
        return None
    if not (snap.rejection_up and not snap.volume_ok):
        return None
    # D1 must confirm: only short a rejection when D1 structure is bearish (LOGIC.md §9.3)
    if snap.d1_bias != "BEAR":
        return None
    # Kill-zone gate: rejections outside London/NY lack institutional follow-through (§9.3)
    if snap.session not in ("LONDON_OPEN", "NY_OPEN"):
        return None
    stop = max(snap.high, r1.price) + snap.body * 0.2
    return _make("SHORT", snap, stop, ["rejection_short", "classic_a"], cfg,
                 tp_level=s1.price if s1 else snap.dr_mid)


def rejection_long(snap, m30_vis, cfg) -> SignalState | None:  # §9.3
    r1, s1 = _level(snap, "R1"), _level(snap, "S1")
    if not s1 or not snap.near_support or snap.zone != "DISCOUNT":
        return None
    if not (snap.rejection_down and not snap.volume_ok):
        return None
    # D1 must confirm: only buy a rejection when D1 structure is bullish (LOGIC.md §9.3)
    if snap.d1_bias != "BULL":
        return None
    # Kill-zone gate: rejections outside London/NY lack institutional follow-through (§9.3)
    if snap.session not in ("LONDON_OPEN", "NY_OPEN"):
        return None
    stop = min(snap.low, s1.price) - snap.body * 0.2
    return _make("LONG", snap, stop, ["rejection_long", "classic_v"], cfg,
                 tp_level=r1.price if r1 else snap.dr_mid)


def sweep_short(snap, m30_vis, cfg) -> SignalState | None:  # §9.4
    r1, s1 = _level(snap, "R1"), _level(snap, "S1")
    if not (snap.swept_bsl and not snap.volume_ok):
        return None
    # Zone gate: don't sell in discount zone — price is already cheap (LOGIC.md §9.4)
    if snap.zone == "DISCOUNT":
        return None
    # D1 alignment: don't sell against a daily uptrend (LOGIC.md §9.4)
    if snap.d1_bias == "BULL":
        return None
    # London gate: London carries strong momentum; only short when full HTF is bearish (§9.4)
    if snap.session == "LONDON_OPEN" and snap.htf_bias != "BEAR":
        return None
    # M30 structure: BSL sweep lacks follow-through when M30 is choppy/neutral (§9.4)
    if snap.m30_trend == "NEUTRAL":
        return None
    stop = snap.high + snap.body * 0.2
    return _make("SHORT", snap, stop, ["sweep_short", "bsl_sweep"], cfg,
                 tp_level=s1.price if s1 else snap.dr_mid)


def sweep_long(snap, m30_vis, cfg) -> SignalState | None:  # §9.4
    r1, s1 = _level(snap, "R1"), _level(snap, "S1")
    if not (snap.swept_ssl and not snap.volume_ok):
        return None
    # Zone gate: don't buy in premium zone — price is already expensive (LOGIC.md §9.4)
    if snap.zone == "PREMIUM":
        return None
    # D1 alignment: don't buy against a daily downtrend (LOGIC.md §9.4)
    if snap.d1_bias == "BEAR":
        return None
    # M30 structure: SSL sweep works as a reversal — not as a dip in a bull M30 (§9.4)
    if snap.m30_trend == "BULL":
        return None
    # CHoCH guard: signal bar itself made the structural break → chasing an extended bar (§9.4)
    if snap.choch:
        return None
    stop = snap.low - snap.body * 0.2
    return _make("LONG", snap, stop, ["sweep_long", "ssl_sweep"], cfg,
                 tp_level=r1.price if r1 else snap.dr_mid)


def pullback_flip_long(snap, m30_vis, cfg) -> SignalState | None:  # §9.5
    r1, r2 = _level(snap, "R1"), _level(snap, "R2")
    if not r1 or snap.htf_bias == "BEAR":
        return None
    band = snap.close * cfg.near_level_pct
    recent = m30_vis.iloc[-7:-1]
    broke = bool((recent["close"] > r1.price).any()) if len(recent) else False
    pulled_back = snap.low <= r1.price + band and snap.close > r1.price
    confirm = snap.bull_close and (snap.rejection_down or snap.bull_engulfing)
    if not (broke and pulled_back and confirm):
        return None
    stop = r1.price - band
    return _make("LONG", snap, stop, ["pullback_flip_long", "rbs_retest"], cfg,
                 tp_level=r2.price if r2 else None)


def pullback_flip_short(snap, m30_vis, cfg) -> SignalState | None:  # §9.5
    s1, s2 = _level(snap, "S1"), _level(snap, "S2")
    if not s1 or snap.htf_bias == "BULL":
        return None
    band = snap.close * cfg.near_level_pct
    recent = m30_vis.iloc[-7:-1]
    broke = bool((recent["close"] < s1.price).any()) if len(recent) else False
    pulled_back = snap.high >= s1.price - band and snap.close < s1.price
    confirm = snap.bear_close and (snap.rejection_up or snap.bear_engulfing)
    if not (broke and pulled_back and confirm):
        return None
    stop = s1.price + band
    return _make("SHORT", snap, stop, ["pullback_flip_short", "sbr_retest"], cfg,
                 tp_level=s2.price if s2 else None)


# Priority order: breakouts (session-gated) → sweeps → rejections → pullbacks.
# Higher-conviction structural breaks first; mean-reversion last.
SPINE_RULES = [
    breakout_long, breakout_short,
    sweep_long, sweep_short,
    rejection_long, rejection_short,
    pullback_flip_long, pullback_flip_short,
]
