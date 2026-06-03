"""Risk sizing and reward:risk gating (LOGIC.md §16).

Fixed-fractional risk only. The 2% cap is a HARD ceiling — `position_size`
raises if a caller tries to exceed it (CLAUDE.md: never silently change risk).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import EngineConfig, Direction


class RiskCapExceeded(ValueError):
    """Raised when requested risk_per_trade exceeds the hard cap."""


@dataclass
class Sizing:
    units: float            # contracts / lots (in `contract_value` units)
    risk_amount: float      # currency at risk if stop is hit
    stop_distance: float    # price distance to stop


def rr_multiple(entry: float, stop: float, take_profit: float,
                direction: Direction) -> float:
    """Reward:risk of a proposed trade. 0 if the geometry is invalid."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = (take_profit - entry) if direction == "LONG" else (entry - take_profit)
    return max(reward / risk, 0.0)


def rr_ok(entry: float, stop: float, take_profit: float,
          direction: Direction, cfg: EngineConfig) -> bool:
    return rr_multiple(entry, stop, take_profit, direction) >= cfg.min_rr


def position_size(equity: float, entry: float, stop: float,
                  cfg: EngineConfig, risk_per_trade: float | None = None) -> Sizing:
    """Size from fixed-fractional risk and the stop distance.

    ``size = (equity * risk) / (stop_distance * contract_value)`` (LOGIC.md §16).
    Enforces the hard cap; never silently clamps to it.
    """
    risk = cfg.risk_per_trade if risk_per_trade is None else risk_per_trade
    if risk > cfg.risk_cap + 1e-12:
        raise RiskCapExceeded(
            f"risk_per_trade={risk:.4f} exceeds hard cap {cfg.risk_cap:.4f}")
    if risk <= 0 or equity <= 0:
        return Sizing(0.0, 0.0, abs(entry - stop))
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return Sizing(0.0, 0.0, 0.0)
    risk_amount = equity * risk
    units = risk_amount / (stop_distance * cfg.contract_value)
    return Sizing(units=units, risk_amount=risk_amount, stop_distance=stop_distance)
