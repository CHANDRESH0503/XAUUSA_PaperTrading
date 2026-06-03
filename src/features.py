"""Measurable features for the MVP spine (LOGIC.md §3, §4, §6.2, §7, §8, §10).

Each function takes only data that has already closed and returns numeric /
boolean facts. ``build_snapshot`` is the orchestrator: given the primary-frame
bar index and the aligned frame dict, it produces one :class:`FeatureSnapshot`
that the rules consume. No function here may read a bar that closes after the
decision bar — slicing is done with :func:`data.visible_frame`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import EngineConfig
from .contracts import FeatureSnapshot, Level
from .data import visible_frame, timeframe_delta


# --------------------------------------------------------------------------- #
# Market structure: confirmed pivots + trend + BOS/CHoCH (LOGIC.md §3)
# --------------------------------------------------------------------------- #
def confirmed_pivots(frame: pd.DataFrame, p: int) -> tuple[list[tuple[int, float]],
                                                           list[tuple[int, float]]]:
    """Return ``(swing_highs, swing_lows)`` as ``(iloc, price)`` for pivots that
    are *confirmed* — i.e. ``p`` bars have closed on each side. The most recent
    ``p`` bars can never be confirmed yet (no look-ahead)."""
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    n = len(frame)
    sh: list[tuple[int, float]] = []
    sl: list[tuple[int, float]] = []
    for i in range(p, n - p):
        left_h, right_h = highs[i - p:i], highs[i + 1:i + 1 + p]
        if highs[i] > left_h.max() and highs[i] >= right_h.max():
            sh.append((i, float(highs[i])))
        left_l, right_l = lows[i - p:i], lows[i + 1:i + 1 + p]
        if lows[i] < left_l.min() and lows[i] <= right_l.min():
            sl.append((i, float(lows[i])))
    return sh, sl


def trend_from_swings(swing_highs, swing_lows) -> str:
    """Classify HH/HL (BULL) vs LH/LL (BEAR) from the last two of each."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL"
    hh = swing_highs[-1][1] > swing_highs[-2][1]
    hl = swing_lows[-1][1] > swing_lows[-2][1]
    lh = swing_highs[-1][1] < swing_highs[-2][1]
    ll = swing_lows[-1][1] < swing_lows[-2][1]
    if hh and hl:
        return "BULL"
    if lh and ll:
        return "BEAR"
    return "NEUTRAL"


def structure_event(frame: pd.DataFrame, p: int) -> tuple[bool, bool, str]:
    """BOS / CHoCH for the **last** bar of ``frame`` (must be a closed slice).

    Returns ``(bos, choch, trend)``. A break = the last bar's *close* beyond the
    most recent confirmed swing (LOGIC.md §3: close, not wick).
    """
    sh, sl = confirmed_pivots(frame, p)
    trend = trend_from_swings(sh, sl)
    if frame.empty:
        return False, False, trend
    close = float(frame["close"].iloc[-1])
    last_idx = len(frame) - 1
    # only swings strictly before the signalling bar count
    last_high = next((pr for i, pr in reversed(sh) if i < last_idx), None)
    last_low = next((pr for i, pr in reversed(sl) if i < last_idx), None)

    bos = choch = False
    if last_high is not None and close > last_high:
        if trend == "BULL":
            bos = True
        else:
            choch = True
    elif last_low is not None and close < last_low:
        if trend == "BEAR":
            bos = True
        else:
            choch = True
    return bos, choch, trend


# --------------------------------------------------------------------------- #
# S/R levels & dealing range (LOGIC.md §4.1, §10)
# --------------------------------------------------------------------------- #
def _cluster(prices: list[float], tol_abs: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for pr in sorted(prices):
        if clusters and abs(pr - np.mean(clusters[-1])) <= tol_abs:
            clusters[-1].append(pr)
        else:
            clusters.append([pr])
    return clusters


def build_levels(h1: pd.DataFrame, price: float, cfg: EngineConfig) -> list[Level]:
    """Construct ``[S2, S1, R1, R2]`` around ``price`` from H1 swings.

    A candidate level is a cluster of confirmed pivot extremes within the
    tolerance band; it is valid only if at least ``k`` bars *touch* it (a touch
    = a bar whose high (res) / low (sup) enters the band). Levels are picked as
    the nearest valid clusters above (R) and below (S) the current price.
    """
    window = h1.tail(cfg.sr_lookback)
    if len(window) < cfg.pivot_confirm * 2 + 1:
        return []
    sh, sl = confirmed_pivots(window, cfg.pivot_confirm)
    tol_abs = price * cfg.sr_tolerance_pct
    highs = window["high"].to_numpy()
    lows = window["low"].to_numpy()

    def make(cands: list[tuple[int, float]], kind: str) -> list[Level]:
        levels: list[Level] = []
        for cl in _cluster([pr for _, pr in cands], tol_abs):
            lvl_price = float(np.mean(cl))
            series = highs if kind == "res" else lows
            touches = int(np.sum(np.abs(series - lvl_price) <= tol_abs))
            if touches >= cfg.sr_touch_count:
                levels.append(Level(name="", price=round(lvl_price, 2),
                                     kind=kind, touches=touches))
        return levels

    resistances = [l for l in make(sh, "res") if l.price > price]
    supports = [l for l in make(sl, "sup") if l.price < price]
    resistances.sort(key=lambda l: l.price)        # nearest above first
    supports.sort(key=lambda l: l.price, reverse=True)  # nearest below first

    out: list[Level] = []
    if supports:
        supports[0].name = "S1"
        out.append(supports[0])
    if len(supports) > 1:
        supports[1].name = "S2"
        out.append(supports[1])
    if resistances:
        resistances[0].name = "R1"
        out.append(resistances[0])
    if len(resistances) > 1:
        resistances[1].name = "R2"
        out.append(resistances[1])
    return out


def dealing_range(h1: pd.DataFrame, cfg: EngineConfig) -> tuple[float | None, float | None, float | None]:
    window = h1.tail(cfg.dealing_range_lookback)
    if window.empty:
        return None, None, None
    drh = float(window["high"].max())
    drl = float(window["low"].min())
    return drh, drl, (drh + drl) / 2.0


def zone_of(price: float, drh: float | None, drl: float | None, cfg: EngineConfig) -> str:
    if drh is None or drl is None or drh <= drl:
        return "MID"
    mid = (drh + drl) / 2.0
    band = price * cfg.midpoint_band_pct
    if abs(price - mid) <= band:
        return "MID"
    return "PREMIUM" if price > mid else "DISCOUNT"


# --------------------------------------------------------------------------- #
# Volume gate & candle shape (LOGIC.md §8)
# --------------------------------------------------------------------------- #
def volume_gate(m30: pd.DataFrame, cfg: EngineConfig) -> tuple[bool, float, str]:
    """Return ``(volume_ok, volume_ma, source)`` for the last bar of ``m30``.

    Uses provider volume when present; otherwise the candle-range participation
    proxy (LOGIC.md §8.1) and tags the source so the audit log is honest.
    """
    window = m30.tail(cfg.volume_ma_window + 1)
    if len(window) < 2:
        return False, 0.0, "missing"
    cur = window.iloc[-1]
    prior = window.iloc[:-1]
    vol = prior["volume"]
    if vol.notna().all() and (vol > 0).any():
        ma = float(vol.mean())
        ok = bool(cur["volume"] > ma * cfg.volume_multiplier)
        return ok, ma, "provider"
    if not cfg.allow_range_proxy:
        return False, 0.0, "missing"
    rng = (prior["high"] - prior["low"]).mean()
    cur_rng = float(cur["high"] - cur["low"])
    ma = float(rng)
    ok = bool(cur_rng > ma * cfg.volume_multiplier) if ma > 0 else False
    return ok, ma, "range_proxy"


def candle_shape(o: float, h: float, l: float, c: float, cfg: EngineConfig) -> dict:
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return {
        "bull_close": c > o,
        "bear_close": c < o,
        "body": body,
        "upper_wick": upper,
        "lower_wick": lower,
        "strong_body": body > rng * cfg.strong_body_ratio,
        "rejection_up": upper > body * cfg.rejection_wick_ratio,
        "rejection_down": lower > body * cfg.rejection_wick_ratio,
    }


def engulfing(cur: pd.Series, prev: pd.Series) -> tuple[bool, bool]:
    bull = (cur["close"] > cur["open"] and prev["close"] < prev["open"]
            and cur["close"] >= prev["open"] and cur["open"] <= prev["close"])
    bear = (cur["close"] < cur["open"] and prev["close"] > prev["open"]
            and cur["open"] >= prev["close"] and cur["close"] <= prev["open"])
    return bool(bull), bool(bear)


def consecutive_motion(m30: pd.DataFrame) -> tuple[int, str]:
    """Count consecutive same-direction closes ending at the last bar."""
    closes = m30["close"].to_numpy()
    opens = m30["open"].to_numpy()
    n = len(closes)
    if n == 0:
        return 0, "FLAT"
    direction = "FLAT"
    last_dir = "BULL" if closes[-1] > opens[-1] else "BEAR" if closes[-1] < opens[-1] else "FLAT"
    if last_dir == "FLAT":
        return 0, "FLAT"
    count = 0
    for i in range(n - 1, -1, -1):
        d = "BULL" if closes[i] > opens[i] else "BEAR" if closes[i] < opens[i] else "FLAT"
        if d == last_dir:
            count += 1
        else:
            break
    return count, last_dir


# --------------------------------------------------------------------------- #
# Liquidity sweeps (LOGIC.md §6.2, §9.4)
# --------------------------------------------------------------------------- #
def detect_sweeps(m30: pd.DataFrame, r1: float | None, s1: float | None,
                  cfg: EngineConfig) -> tuple[bool, bool]:
    """Detect a one/two-bar liquidity sweep against R1 (BSL) or S1 (SSL).

    BSL sweep: within the last ``sweep_max_bars_back_inside`` bars a high pierced
    above R1, and the current bar **closes back below** R1 with a bearish body.
    SSL sweep is the mirror below S1.
    """
    swept_bsl = swept_ssl = False
    n = len(m30)
    if n < 2:
        return False, False
    look = min(cfg.sweep_max_bars_back_inside, n - 1)
    cur = m30.iloc[-1]
    recent = m30.iloc[-(look + 1):]
    if r1 is not None:
        pierced = bool((recent["high"] > r1).any())
        if pierced and cur["close"] < r1 and cur["close"] <= cur["open"]:
            swept_bsl = True
    if s1 is not None:
        pierced = bool((recent["low"] < s1).any())
        if pierced and cur["close"] > s1 and cur["close"] >= cur["open"]:
            swept_ssl = True
    return swept_bsl, swept_ssl


# --------------------------------------------------------------------------- #
# Multi-TF bias consensus (LOGIC.md §15)
# --------------------------------------------------------------------------- #
def consensus_bias(h4_trend: str, d1_trend: str) -> str:
    """D1 is the highest-level arbiter (LOGIC.md §15).

    When H4 and D1 agree → use that direction.
    When one is NEUTRAL → defer to the other.
    When they disagree directionally (H4 pullback inside a D1 trend) → NEUTRAL:
    the HTF veto does not fire; zone + pattern requirements filter entries.
    """
    if h4_trend == d1_trend:
        return h4_trend
    if h4_trend == "NEUTRAL":
        return d1_trend
    if d1_trend == "NEUTRAL":
        return h4_trend
    return "NEUTRAL"


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def _qt_phase(session: str) -> str:
    return {"ASIA": "Q1", "LONDON_OPEN": "Q2", "NY_OPEN": "Q3", "NY_PM": "Q4"}.get(session, "Q1")


def build_snapshot(bar_index: int, frames: dict[str, pd.DataFrame],
                   cfg: EngineConfig) -> FeatureSnapshot:
    """Build the closed-bar feature bundle for primary-frame bar ``bar_index``."""
    pf = cfg.primary_frame
    m30 = frames[pf]
    if bar_index < 0 or bar_index >= len(m30):
        raise IndexError(f"bar_index {bar_index} out of range for {pf}")

    bar_time = m30.index[bar_index]
    close_time = bar_time + timeframe_delta(pf)
    bar = m30.iloc[bar_index]
    prev = m30.iloc[bar_index - 1] if bar_index > 0 else bar

    # primary frame closed up to & including the signalling bar
    m30_vis = m30.iloc[:bar_index + 1]

    # HTF / LTF frames sliced to bars closed at/before this bar's close.
    # Cap H4 / D1 to a rolling tail to avoid O(n²) on long datasets:
    # structure_event only needs recent pivots (2 each side confirmed); 100 H4
    # bars = ~17 days and 50 D1 bars = ~7 weeks — more than sufficient.
    h1 = visible_frame(frames["H1"], close_time, "H1") if "H1" in frames else m30_vis
    h4 = (visible_frame(frames["H4"], close_time, "H4").tail(100)
          if "H4" in frames else h1)
    d1 = (visible_frame(frames["D1"], close_time, "D1").tail(50)
          if "D1" in frames else h4)

    price = float(bar["close"])
    levels = build_levels(h1, price, cfg)
    by_name = {l.name: l for l in levels}
    r1 = by_name.get("R1")
    s1 = by_name.get("S1")
    drh, drl, dr_mid = dealing_range(h1, cfg)
    zone = zone_of(price, drh, drl, cfg)

    session = cfg.session_of(int(bar_time.hour))
    in_kz = cfg.is_kill_zone(int(bar_time.hour))

    vol_ok, vol_ma, vol_src = volume_gate(m30_vis, cfg)
    shape = candle_shape(float(bar["open"]), float(bar["high"]),
                         float(bar["low"]), price, cfg)
    bull_eng, bear_eng = engulfing(bar, prev)

    _, _, h4_trend = structure_event(h4, cfg.pivot_confirm)
    min_bars = cfg.pivot_confirm * 2 + 1
    _, _, d1_trend = (structure_event(d1, cfg.pivot_confirm)
                      if len(d1) >= min_bars else (False, False, "NEUTRAL"))
    htf_consensus = consensus_bias(h4_trend, d1_trend)
    # Limit M30 structure scan to recent bars — 200 M30 bars = ~4 days, well
    # beyond the 5 bars needed for 2 confirmed pivots. Avoids O(n²) on long runs.
    bos, choch, m30_trend = structure_event(m30_vis.iloc[-200:], cfg.pivot_confirm)

    swept_bsl, swept_ssl = detect_sweeps(
        m30_vis, r1.price if r1 else None, s1.price if s1 else None, cfg)

    motion_n, motion_dir = consecutive_motion(m30_vis)

    near_band = price * cfg.near_level_pct
    near_res = next((l for l in levels if l.kind == "res"
                     and abs(price - l.price) <= near_band), None)
    near_sup = next((l for l in levels if l.kind == "sup"
                     and abs(price - l.price) <= near_band), None)

    # room to the nearest opposing level (used by the reward-too-small filter)
    res_above = [l.price for l in levels if l.kind == "res" and l.price > price]
    sup_below = [l.price for l in levels if l.kind == "sup" and l.price < price]
    nearest_opp = None
    if res_above:
        nearest_opp = min(res_above) - price
    if sup_below:
        d = price - max(sup_below)
        nearest_opp = d if nearest_opp is None else min(nearest_opp, d)

    vol = float(bar["volume"]) if not pd.isna(bar["volume"]) else float("nan")

    return FeatureSnapshot(
        bar_index=bar_index,
        bar_time=bar_time,
        open=float(bar["open"]), high=float(bar["high"]),
        low=float(bar["low"]), close=price, volume=vol,
        prev_open=float(prev["open"]), prev_high=float(prev["high"]),
        prev_low=float(prev["low"]), prev_close=float(prev["close"]),
        session=session, qt_phase=_qt_phase(session), in_kill_zone=in_kz,
        volume_ma=vol_ma, volume_ok=vol_ok, volume_source=vol_src,
        bull_close=shape["bull_close"], bear_close=shape["bear_close"],
        body=shape["body"], upper_wick=shape["upper_wick"], lower_wick=shape["lower_wick"],
        strong_body=shape["strong_body"], rejection_up=shape["rejection_up"],
        rejection_down=shape["rejection_down"],
        bull_engulfing=bull_eng, bear_engulfing=bear_eng,
        levels=levels, near_resistance=near_res, near_support=near_sup,
        nearest_opposing_dist=nearest_opp,
        drh=drh, drl=drl, dr_mid=dr_mid, zone=zone,
        htf_bias=htf_consensus, d1_bias=d1_trend,
        m30_trend=m30_trend, bos=bos, choch=choch,
        swept_bsl=swept_bsl, swept_ssl=swept_ssl,
        consecutive_motion=motion_n, motion_dir=motion_dir,
    )
