"""Central configuration for the XAU/USD signal engine.

Every tunable lives here so a walk-forward / sensitivity run can sweep one
object instead of hunting magic numbers across modules. Defaults come from
``LOGIC.md`` (the rule spec) and ``PLAN.md`` (the MVP plan). Nothing here is a
proven edge — these are *hypotheses to be backtested*.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

Direction = Literal["LONG", "SHORT", "FLAT"]

# Canonical timeframe durations in minutes. Frame DataFrames are indexed by the
# candle OPEN time (UTC); a bar opened at T on timeframe TF closes at T + TF.
TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


@dataclass(frozen=True)
class SessionWindow:
    """A UTC hour window [start, end) used for session / kill-zone logic."""

    name: str
    start_hour: int
    end_hour: int

    def contains(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # wraps past midnight
        return hour >= self.start_hour or hour < self.end_hour


@dataclass(frozen=True)
class EngineConfig:
    """All knobs for the deterministic MVP spine (LOGIC.md §3–§16)."""

    # --- instrument ---------------------------------------------------------
    symbol: str = "XAU/USD"
    # PLAN.md default. NOTE: gold "pips" are quoted inconsistently in the
    # source material; 0.01 means $0.01 = 1 pip. Keep configurable — the BIGEY
    # pip-distance rules (deferred) are sensitive to this.
    pip_size: float = 0.01
    # 100 oz per standard contract; sizing uses this as price->P&L multiplier.
    contract_value: float = 100.0

    # --- market structure (LOGIC.md §3) ------------------------------------
    pivot_confirm: int = 2          # `p`: bars closed each side to confirm a swing

    # --- S/R level construction (LOGIC.md §4.1) ----------------------------
    sr_lookback: int = 20           # `N`: H1 bars scanned for swing extremes
    sr_touch_count: int = 2         # `k`: touches inside the tolerance band
    sr_tolerance_pct: float = 0.0010  # 0.10% of price clustering band
    near_level_pct: float = 0.0008  # "at level" proximity band (~0.08% of price)

    # --- dealing range (LOGIC.md §10) --------------------------------------
    dealing_range_lookback: int = 40  # H1 bars defining the active range
    midpoint_band_pct: float = 0.0005  # band around equilibrium = MID zone

    # --- volume gate (LOGIC.md §8.1) ---------------------------------------
    volume_ma_window: int = 20
    volume_multiplier: float = 1.5
    # If true and provider volume is missing/flat, fall back to a candle-range
    # participation proxy and tag the reason. (LOGIC.md §8.1.)
    allow_range_proxy: bool = True

    # --- candle confirmation (LOGIC.md §8.2) -------------------------------
    strong_body_ratio: float = 0.6   # body > range * ratio
    rejection_wick_ratio: float = 1.5  # wick > body * ratio

    # --- sessions / quarterly theory (LOGIC.md §7) -------------------------
    # UTC windows. London/NY are the only breakout windows; Asia biases FLAT.
    session_asia: SessionWindow = SessionWindow("ASIA", 0, 7)
    session_london: SessionWindow = SessionWindow("LONDON_OPEN", 7, 12)
    session_ny: SessionWindow = SessionWindow("NY_OPEN", 12, 16)
    session_ny_pm: SessionWindow = SessionWindow("NY_PM", 16, 21)

    # --- no-trade / exhaustion guards (LOGIC.md §12) -----------------------
    max_consecutive_motion: int = 3  # 4th+ same-direction candle => FLAT

    # --- sweep / fakeout (LOGIC.md §6.2, §9.4) -----------------------------
    sweep_max_bars_back_inside: int = 2  # close back inside within N bars

    # --- risk (LOGIC.md §16) -----------------------------------------------
    risk_per_trade: float = 0.01     # fraction of equity
    risk_cap: float = 0.02           # HARD cap — never silently exceed
    min_rr: float = 1.5              # minimum reward:risk to take a trade
    target_rr: float = 2.0           # fixed-R take-profit when no level is nearer
    starting_equity: float = 10_000.0

    # --- HTF veto (LOGIC.md §15) -------------------------------------------
    htf_veto_stop_multiple: float = 2.0  # opposing major zone within N*stop => FLAT

    # --- confluence / confidence (LOGIC.md §13) — MVP simple version -------
    confluence_enter_gate: int = 0   # MVP: rules self-gate; scoring is advisory

    # --- backtest costs (CLAUDE.md "metrics that lie") ---------------------
    spread_price: float = 0.30       # round-trip half-spread per side, in price
    slippage_price: float = 0.10     # extra adverse fill, in price
    commission_per_trade: float = 0.0  # currency per round trip

    # --- feature flags for deferred layers (PLAN.md roadmap) ---------------
    enable_smc_layer: bool = False   # OB/FVG/QM/reversal playbook (§5,§6,§9.6-9.7)
    enable_bigey_layer: bool = False  # §24–§33 execution layer

    # frames the engine expects, primary signal frame first
    primary_frame: str = "M30"
    htf_frames: tuple[str, ...] = ("H1", "H4", "D1")
    ltf_frames: tuple[str, ...] = ("M15",)

    def with_overrides(self, **kwargs) -> "EngineConfig":
        """Return a copy with fields replaced (for sensitivity sweeps)."""
        return replace(self, **kwargs)

    def scaled(self, factor: float) -> "EngineConfig":
        """Scale the numeric thresholds by ``factor`` for ±10% sensitivity runs.

        Only scales rule thresholds, not risk caps or structural integers that
        must stay valid (pivot_confirm, touch counts, lookbacks stay fixed).
        """
        return replace(
            self,
            sr_tolerance_pct=self.sr_tolerance_pct * factor,
            near_level_pct=self.near_level_pct * factor,
            volume_multiplier=self.volume_multiplier * factor,
            strong_body_ratio=self.strong_body_ratio * factor,
            rejection_wick_ratio=self.rejection_wick_ratio * factor,
            min_rr=self.min_rr * factor,
        )

    def session_of(self, hour_utc: int) -> str:
        for win in (self.session_asia, self.session_london,
                    self.session_ny, self.session_ny_pm):
            if win.contains(hour_utc):
                return win.name
        return "OFF"

    def is_kill_zone(self, hour_utc: int) -> bool:
        return self.session_london.contains(hour_utc) or self.session_ny.contains(hour_utc)


DEFAULT_CONFIG = EngineConfig()
