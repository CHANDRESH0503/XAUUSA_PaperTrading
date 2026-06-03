"""The single public entry point: ``evaluate(bar_index, frames) -> SignalState``.

This composes features + rules + vetoes into one auditable decision per closed
primary-frame (M30) bar. It is a *pure* function of the data visible at or
before ``bar_index`` (CLAUDE.md hard rule #1) — no globals, no I/O, no clock.
"""

from __future__ import annotations

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .contracts import SignalState, FeatureSnapshot, flat
from .features import build_snapshot
from . import rules as R


def evaluate(bar_index: int, frames: dict[str, pd.DataFrame],
             cfg: EngineConfig = DEFAULT_CONFIG,
             return_snapshot: bool = False):
    """Evaluate the signal for primary-frame bar ``bar_index``.

    Order (LOGIC.md §18 pseudocode): build features → force-FLAT filters →
    try the spine rules in priority order → apply the HTF veto to any firing
    rule. FLAT is the default and most frequent output.
    """
    snap: FeatureSnapshot = build_snapshot(bar_index, frames, cfg)
    m30_vis = frames[cfg.primary_frame].iloc[:bar_index + 1]

    def done(sig: SignalState) -> SignalState | tuple:
        return (sig, snap) if return_snapshot else sig

    # --- force-FLAT filters first (§12) ---
    nt = R.no_trade_filters(snap, cfg)
    if nt:
        return done(flat(snap.bar_time, nt))

    # --- spine rules in priority order (§9) ---
    for rule in R.SPINE_RULES:
        sig = rule(snap, m30_vis, cfg)
        if sig is None:
            continue
        # --- HTF veto on the firing signal (§15) ---
        veto = R.htf_veto(snap, sig.direction, sig.stop, cfg)
        if veto:
            return done(flat(snap.bar_time, sig.reasons[:1] + [veto]))
        return done(sig)

    return done(flat(snap.bar_time, ["no_setup"]))


def evaluate_series(frames: dict[str, pd.DataFrame], cfg: EngineConfig = DEFAULT_CONFIG,
                    start: int | None = None) -> list[SignalState]:
    """Run ``evaluate`` across every closed primary-frame bar.

    ``start`` defaults to a warmup offset large enough for levels/structure to
    have history. Returns one :class:`SignalState` per bar (mostly FLAT).
    """
    pf = cfg.primary_frame
    n = len(frames[pf])
    warmup = max(cfg.sr_lookback, cfg.dealing_range_lookback,
                 cfg.volume_ma_window) + cfg.pivot_confirm * 2 + 2
    start = warmup if start is None else start
    return [evaluate(i, frames, cfg) for i in range(start, n)]
